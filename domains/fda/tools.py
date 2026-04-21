"""openFDA API client + AccessData PDF scraper for FDA regulatory QA.

Endpoints:
  /device/510k.json       — 510(k) clearance records
  /device/event.json      — MAUDE adverse event reports
  /device/recall.json     — Device recalls
  /device/enforcement.json — Enforcement actions (has recall class)
  /device/classification.json — Product code / device class lookup
  AccessData PDF           — 510(k) summary PDFs (for predicate devices)
"""

from __future__ import annotations

import io
import logging
import os
import re
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api.fda.gov"
ACCESSDATA_URL = "https://www.accessdata.fda.gov"

_OPENFDA_API_KEY = os.environ.get("OPENFDA_API_KEY", "")

_last_call_time: float = 0
CALL_DELAY = 0.25  # 4 req/sec — well under 240/min limit with key

_last_accessdata_time: float = 0
ACCESSDATA_DELAY = 1.0  # Conservative for government server


def _rate_limit(is_accessdata: bool = False):
    """Enforce rate limiting between API calls."""
    global _last_call_time, _last_accessdata_time
    if is_accessdata:
        elapsed = time.time() - _last_accessdata_time
        if elapsed < ACCESSDATA_DELAY:
            time.sleep(ACCESSDATA_DELAY - elapsed)
        _last_accessdata_time = time.time()
    else:
        elapsed = time.time() - _last_call_time
        if elapsed < CALL_DELAY:
            time.sleep(CALL_DELAY - elapsed)
        _last_call_time = time.time()


def _openfda_request(endpoint: str, params: dict[str, str]) -> dict:
    """Make a request to the openFDA API.

    Args:
        endpoint: API path (e.g., '/device/510k.json')
        params: Query parameters (search, limit, skip, count, sort)

    Returns: Parsed JSON response dict, or {"error": "..."} on failure.
    """
    _rate_limit()
    if _OPENFDA_API_KEY:
        params["api_key"] = _OPENFDA_API_KEY

    url = f"{BASE_URL}{endpoint}"
    try:
        r = httpx.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": "No matches found", "results": [], "meta": {"results": {"total": 0}}}
        return {"error": f"openFDA API error: {e.response.status_code} for {endpoint}"}
    except httpx.RequestError as e:
        return {"error": f"openFDA request failed: {e}"}


# ---------- 510(k) Clearances ----------

def search_510k(
    k_number: str | None = None,
    product_code: str | None = None,
    applicant: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10,
) -> str:
    """Search the FDA 510(k) clearance database.

    Returns formatted text with clearance records.
    """
    search_parts = []
    if k_number:
        search_parts.append(f"k_number:{k_number.upper()}")
    if product_code:
        search_parts.append(f"product_code:{product_code.upper()}")
    if applicant:
        search_parts.append(f'applicant:"{applicant}"')
    if date_from and date_to:
        # openFDA uses YYYYMMDD for date range searches
        df = date_from.replace("-", "")
        dt = date_to.replace("-", "")
        search_parts.append(f"decision_date:[{df}+TO+{dt}]")
    elif date_from:
        df = date_from.replace("-", "")
        search_parts.append(f"decision_date:[{df}+TO+29991231]")
    elif date_to:
        dt = date_to.replace("-", "")
        search_parts.append(f"decision_date:[19900101+TO+{dt}]")

    if not search_parts:
        return "Error: at least one search parameter required (k_number, product_code, applicant, or date range)"

    search = "+AND+".join(search_parts)
    params = {"search": search, "limit": str(min(limit, 100)), "sort": "decision_date:desc"}

    data = _openfda_request("/device/510k.json", params)
    if "error" in data and not data.get("results"):
        return f"No 510(k) records found for: {search}"

    total = data.get("meta", {}).get("results", {}).get("total", 0)
    results = data.get("results", [])

    lines = [f"510(k) Search Results ({total} total, showing {len(results)}):"]
    lines.append("")
    for r in results:
        days = ""
        if r.get("decision_date") and r.get("date_received"):
            try:
                from datetime import datetime
                dec = datetime.strptime(r["decision_date"], "%Y-%m-%d")
                rec = datetime.strptime(r["date_received"], "%Y-%m-%d")
                days = f" ({(dec - rec).days} days to clearance)"
            except ValueError:
                pass

        openfda = r.get("openfda", {})
        device_class = openfda.get("device_class", "?")

        lines.append(f"  {r.get('k_number', '?')} | {r.get('device_name', '?')}")
        lines.append(f"    Applicant: {r.get('applicant', '?')}")
        lines.append(f"    Product Code: {r.get('product_code', '?')} | Class {device_class}")
        lines.append(f"    Decision: {r.get('decision_description', '?')} ({r.get('clearance_type', '?')})")
        lines.append(f"    Received: {r.get('date_received', '?')} | Decided: {r.get('decision_date', '?')}{days}")
        lines.append(f"    Advisory Committee: {r.get('advisory_committee_description', '?')}")
        lines.append("")

    return "\n".join(lines)


# ---------- Predicate Devices (AccessData PDF scraping) ----------

def get_510k_predicates(k_number: str) -> str:
    """Get predicate devices for a 510(k) submission by parsing the summary PDF.

    Fetches the AccessData detail page → extracts PDF URL → downloads PDF →
    extracts text → finds K-numbers via regex.
    """
    k_number = k_number.upper().strip()
    if not re.match(r'^K\d{6}$', k_number):
        return f"Invalid K-number format: {k_number}. Expected K followed by 6 digits."

    # Step 1: Fetch detail page to find PDF URL
    _rate_limit(is_accessdata=True)
    detail_url = f"{ACCESSDATA_URL}/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={k_number}"
    try:
        r = httpx.get(detail_url, timeout=30, follow_redirects=True)
        r.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return f"Could not fetch AccessData page for {k_number}: {e}"

    html = r.text

    # Step 2: Extract PDF URL
    pdf_match = re.search(r'HREF="([^"]*cdrh_docs[^"]*\.pdf)"', html, re.IGNORECASE)
    if not pdf_match:
        return f"No summary PDF found for {k_number}. The submission may only have a Statement (not a Summary), or it may be too old."

    pdf_url = pdf_match.group(1)
    if not pdf_url.startswith("http"):
        pdf_url = f"https://www.accessdata.fda.gov{pdf_url}"

    # Step 3: Download and parse PDF
    _rate_limit(is_accessdata=True)
    try:
        pdf_response = httpx.get(pdf_url, timeout=60, follow_redirects=True)
        pdf_response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return f"Could not download summary PDF for {k_number}: {e}"

    try:
        import pdfplumber
        pdf_text = ""
        with pdfplumber.open(io.BytesIO(pdf_response.content)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pdf_text += text + "\n"
    except Exception as e:
        return f"Could not parse PDF for {k_number}: {e}"

    if not pdf_text.strip():
        return f"Summary PDF for {k_number} is empty or could not be extracted."

    # Step 4: Extract K-numbers from PDF text
    all_k_numbers = set(re.findall(r'K\d{6}', pdf_text))
    all_k_numbers.discard(k_number)  # Remove the subject device itself

    if not all_k_numbers:
        return f"No predicate K-numbers found in the summary PDF for {k_number}.\n\nPDF excerpt (first 2000 chars):\n{pdf_text[:2000]}"

    # Step 5: Format results with context
    lines = [f"Predicate devices found in 510(k) {k_number} summary PDF:"]
    lines.append("")

    for pred_k in sorted(all_k_numbers):
        # Find surrounding context in the PDF
        for match in re.finditer(re.escape(pred_k), pdf_text):
            start = max(0, match.start() - 100)
            end = min(len(pdf_text), match.end() + 100)
            context = pdf_text[start:end].replace("\n", " ").strip()
            lines.append(f"  {pred_k}: ...{context}...")
            break
        else:
            lines.append(f"  {pred_k}")

    # Also include full PDF text for LLM extraction
    lines.append("")
    lines.append("--- Full summary PDF text (first 5000 chars) ---")
    lines.append(pdf_text[:5000])

    return "\n".join(lines)


# ---------- MAUDE Adverse Events ----------

def search_maude(
    brand_name: str | None = None,
    product_code: str | None = None,
    event_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    count_only: bool = False,
    limit: int = 10,
) -> str:
    """Search the MAUDE adverse event database.

    If count_only=True, returns just the total count of matching events.
    Otherwise returns detailed event records with narrative excerpts.
    """
    search_parts = []
    if brand_name:
        search_parts.append(f'device.brand_name:"{brand_name}"')
    if product_code:
        search_parts.append(f"device.device_report_product_code:{product_code.upper()}")
    if event_type:
        search_parts.append(f'event_type:"{event_type}"')
    if date_from and date_to:
        df = date_from.replace("-", "")
        dt = date_to.replace("-", "")
        search_parts.append(f"date_received:[{df}+TO+{dt}]")
    elif date_from:
        df = date_from.replace("-", "")
        search_parts.append(f"date_received:[{df}+TO+29991231]")
    elif date_to:
        dt = date_to.replace("-", "")
        search_parts.append(f"date_received:[19900101+TO+{dt}]")

    if not search_parts:
        return "Error: at least one search parameter required (brand_name, product_code, event_type, or date range)"

    search = "+AND+".join(search_parts)

    # Count-only mode: use count endpoint for breakdown by event type
    if count_only:
        # Build search WITHOUT event_type — we'll get the breakdown from count
        count_search_parts = [p for p in search_parts if not p.startswith("event_type")]
        count_search = "+AND+".join(count_search_parts) if count_search_parts else search

        params = {"search": count_search, "count": "event_type.exact"}
        data = _openfda_request("/device/event.json", params)
        if "error" in data:
            # Fallback: get total from a limit=1 query
            params2 = {"search": count_search, "limit": "1"}
            data2 = _openfda_request("/device/event.json", params2)
            total = data2.get("meta", {}).get("results", {}).get("total", 0)
            return f"MAUDE event count: {total:,} total matching events"

        lines = ["MAUDE event count by type:"]
        total = 0
        filtered_total = 0
        for item in data.get("results", []):
            count = item["count"]
            term = item["term"] or "(blank)"
            lines.append(f"  {term}: {count:,}")
            total += count
            # If user filtered by event_type, highlight that count
            if event_type and term.lower() == event_type.lower():
                filtered_total = count
        lines.append(f"  TOTAL: {total:,}")
        if event_type and filtered_total:
            lines.append(f"\n  Filtered ({event_type}): {filtered_total:,}")
        return "\n".join(lines)

    # Detail mode: fetch records
    params = {"search": search, "limit": str(min(limit, 20))}
    data = _openfda_request("/device/event.json", params)
    if "error" in data and not data.get("results"):
        return f"No MAUDE events found for: {search}"

    total = data.get("meta", {}).get("results", {}).get("total", 0)
    results = data.get("results", [])

    lines = [f"MAUDE Adverse Event Results ({total:,} total, showing {len(results)}):"]
    lines.append("")

    for r in results:
        devices = r.get("device", [])
        device_info = devices[0] if devices else {}
        patients = r.get("patient", [])
        narratives = r.get("mdr_text", [])

        lines.append(f"  Report: {r.get('report_number', '?')}")
        lines.append(f"    Event Type: {r.get('event_type', '?')}")
        lines.append(f"    Date Received: {r.get('date_received', '?')}")
        lines.append(f"    Device: {device_info.get('brand_name', '?')} ({device_info.get('generic_name', '?')})")
        lines.append(f"    Manufacturer: {device_info.get('manufacturer_d_name', '?')}")
        lines.append(f"    Product Code: {device_info.get('device_report_product_code', '?')}")

        if patients:
            problems = patients[0].get("patient_problems", [])
            if problems:
                lines.append(f"    Patient Problems: {', '.join(problems)}")

        # Include first narrative excerpt
        for n in narratives:
            if n.get("text_type_code") == "Description of Event or Problem":
                text = n.get("text", "")[:300]
                lines.append(f"    Narrative: {text}")
                break

        lines.append("")

    return "\n".join(lines)


# ---------- Recalls ----------

def search_recalls(
    product_code: str | None = None,
    recalling_firm: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10,
) -> str:
    """Search the FDA device recall database."""
    search_parts = []
    if product_code:
        search_parts.append(f"product_code:{product_code.upper()}")
    if recalling_firm:
        search_parts.append(f'recalling_firm:"{recalling_firm}"')
    if date_from and date_to:
        df = date_from.replace("-", "")
        dt = date_to.replace("-", "")
        search_parts.append(f"event_date_initiated:[{df}+TO+{dt}]")

    if not search_parts:
        return "Error: at least one search parameter required"

    search = "+AND+".join(search_parts)
    params = {"search": search, "limit": str(min(limit, 50)), "sort": "event_date_initiated:desc"}

    data = _openfda_request("/device/recall.json", params)
    if "error" in data and not data.get("results"):
        return f"No recalls found for: {search}"

    total = data.get("meta", {}).get("results", {}).get("total", 0)
    results = data.get("results", [])

    lines = [f"Device Recall Results ({total} total, showing {len(results)}):"]
    lines.append("")

    for r in results:
        k_numbers = r.get("k_numbers", [])
        k_ref = f" (related 510(k)s: {', '.join(k_numbers)})" if k_numbers else ""

        lines.append(f"  Recall: {r.get('product_res_number', '?')}{k_ref}")
        lines.append(f"    Status: {r.get('recall_status', '?')}")
        lines.append(f"    Date Initiated: {r.get('event_date_initiated', '?')}")
        lines.append(f"    Firm: {r.get('recalling_firm', '?')}")
        lines.append(f"    Product: {r.get('product_description', '?')[:200]}")
        lines.append(f"    Reason: {r.get('reason_for_recall', '?')[:300]}")
        lines.append(f"    Root Cause: {r.get('root_cause_description', '?')}")
        lines.append("")

    return "\n".join(lines)


# ---------- Product Classification ----------

def lookup_classification(
    product_code: str | None = None,
    device_name: str | None = None,
    limit: int = 5,
) -> str:
    """Look up FDA device classification by product code or device name."""
    if product_code:
        search = f"product_code:{product_code.upper()}"
    elif device_name:
        search = f'device_name:"{device_name}"'
    else:
        return "Error: provide either product_code or device_name"

    params = {"search": search, "limit": str(min(limit, 20))}
    data = _openfda_request("/device/classification.json", params)
    if "error" in data and not data.get("results"):
        return f"No classification found for: {search}"

    results = data.get("results", [])

    # Decode submission type
    submission_types = {"1": "510(k)", "2": "PMA", "3": "De Novo", "4": "Not Classified",
                        "5": "Humanitarian Device Exemption", "7": "Exempt"}

    lines = [f"FDA Device Classification ({len(results)} results):"]
    lines.append("")

    for r in results:
        sub_type = submission_types.get(r.get("submission_type_id", ""), "Unknown")
        lines.append(f"  Product Code: {r.get('product_code', '?')}")
        lines.append(f"    Device Name: {r.get('device_name', '?')}")
        lines.append(f"    Device Class: {r.get('device_class', '?')}")
        lines.append(f"    Regulatory Pathway: {sub_type}")
        lines.append(f"    Regulation Number: {r.get('regulation_number', '?')}")
        lines.append(f"    Medical Specialty: {r.get('medical_specialty_description', '?')}")
        lines.append(f"    Implant: {r.get('implant_flag', 'N')} | Life Sustaining: {r.get('life_sustain_support_flag', 'N')}")
        lines.append("")

    return "\n".join(lines)
