"""Minimal SEC EDGAR API client.

Uses the public EDGAR APIs (no auth required).
SEC requires a User-Agent header with contact info.
"""

from __future__ import annotations

import json
import time

import httpx

SEC_HEADERS = {
    "User-Agent": "ResearchAgent research-agent@example.com",
    "Accept": "application/json",
}

# Known tickers that aren't in SEC's tickers.json (single-letter, recently changed, etc.)
_cik_cache: dict[str, str] = {
    "X": "0001163302",   # United States Steel Corp
    "V": "0001403161",   # Visa Inc
    "C": "0000831001",   # Citigroup
    "F": "0000037996",   # Ford Motor
    "T": "0000732717",   # AT&T
    "K": "0000055067",   # Kellanova
}


def _ticker_to_cik(ticker: str) -> str:
    """Convert ticker to zero-padded CIK. Caches results.

    Falls back to EDGAR company search if ticker isn't in the tickers file
    (handles edge cases like NYSE:X for US Steel).
    """
    ticker_upper = ticker.upper()
    if ticker_upper in _cik_cache:
        return _cik_cache[ticker_upper]

    # Try the tickers file first
    try:
        resp = httpx.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=SEC_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for entry in data.values():
            if entry["ticker"].upper() == ticker_upper:
                cik = str(entry["cik_str"]).zfill(10)
                _cik_cache[ticker_upper] = cik
                return cik
    except Exception:
        pass

    # Fallback: search EDGAR full-text search for the ticker in company names
    import re as _re
    time.sleep(0.15)
    try:
        resp = httpx.get(
            f"https://efts.sec.gov/LATEST/search-index?q=%22({ticker_upper})%22&forms=10-K",
            headers=SEC_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        for hit in hits:
            names = hit.get("_source", {}).get("display_names", [])
            for name in names:
                # Parse CIK from display name like "COMPANY NAME  (TICKER)  (CIK 0001163302)"
                match = _re.search(r'\(CIK\s+(\d+)\)', name)
                ticker_match = _re.search(r'\((' + _re.escape(ticker_upper) + r')\)', name)
                if match and ticker_match:
                    cik = match.group(1).zfill(10)
                    _cik_cache[ticker_upper] = cik
                    return cik
    except Exception:
        pass

    # Second fallback: EDGAR company search (handles delisted/removed tickers)
    time.sleep(0.15)
    try:
        resp = httpx.get(
            f"https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK={ticker_upper}"
            f"&type=10-K&dateb=&owner=include&count=1&search_text=&action=getcompany",
            headers=SEC_HEADERS,
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        cik_match = _re.search(r'CIK=(\d+)', resp.text)
        if cik_match:
            cik = cik_match.group(1).zfill(10)
            _cik_cache[ticker_upper] = cik
            return cik
    except Exception:
        pass

    raise ValueError(
        f"Ticker '{ticker}' not found in SEC EDGAR ticker database. "
        f"Try using the full company name with sec_edgar_filing_text, "
        f"or search for the company's CIK number via web search."
    )


def get_company_facts(ticker: str, metric: str | None = None) -> str:
    """Get XBRL financial facts for a company from SEC EDGAR.

    Returns formatted string for Claude to interpret.
    """
    try:
        cik = _ticker_to_cik(ticker)
    except ValueError as e:
        return str(e)

    time.sleep(0.15)  # SEC rate limit: 10 req/sec

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        resp = httpx.get(url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return f"SEC EDGAR API error: {e.response.status_code} for {ticker}"
    except httpx.RequestError as e:
        return f"SEC EDGAR request failed: {e}"

    data = resp.json()
    entity_name = data.get("entityName", ticker)

    # If a specific metric is requested, filter for it
    if metric:
        return _extract_metric(data, entity_name, metric)

    # Otherwise, return a summary of key financial metrics
    return _summarize_financials(data, entity_name)


def _extract_metric(data: dict, entity_name: str, metric: str) -> str:
    """Extract a specific metric from XBRL facts.

    Deduplicates entries (same period can appear in multiple filings) and
    returns up to 20 unique period entries, covering ~5 years of annual data.
    """
    facts = data.get("facts", {})
    results = []

    for taxonomy in ["us-gaap", "dei"]:
        tax_facts = facts.get(taxonomy, {})
        for concept_name, concept_data in tax_facts.items():
            if metric.lower() in concept_name.lower():
                units = concept_data.get("units", {})
                for unit_type, entries in units.items():
                    # Deduplicate: same concept + period can appear in multiple filings.
                    # Keep the most recently filed version of each unique period.
                    seen_periods = {}  # (start, end) -> entry
                    for entry in entries:
                        period_key = (entry.get("start", "?"), entry.get("end", "?"))
                        existing = seen_periods.get(period_key)
                        if not existing or entry.get("filed", "") > existing.get("filed", ""):
                            seen_periods[period_key] = entry

                    deduped = sorted(seen_periods.values(),
                                     key=lambda x: x.get("end", ""), reverse=True)[:20]
                    for entry in deduped:
                        results.append(
                            f"{concept_name} ({unit_type}): "
                            f"{entry.get('val', 'N/A')} "
                            f"(period: {entry.get('start', '?')} to {entry.get('end', '?')}, "
                            f"filed: {entry.get('filed', '?')})"
                        )
                    if len(results) >= 20:
                        break

    if not results:
        return f"No XBRL data found for metric '{metric}' for {entity_name}"

    header = f"SEC EDGAR XBRL data for {entity_name} — metric filter: {metric}\n"
    return header + "\n".join(results[:20])


def get_segment_financials(ticker: str) -> str:
    """Get segment-level financial data from XBRL (operating income, revenue by segment).

    Returns structured segment data that can be used to calculate segment margins.
    """
    try:
        cik = _ticker_to_cik(ticker)
    except ValueError as e:
        return str(e)

    time.sleep(0.15)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        resp = httpx.get(url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return f"SEC EDGAR request failed: {e}"

    data = resp.json()
    entity_name = data.get("entityName", ticker)
    facts = data.get("facts", {}).get("us-gaap", {})

    # Segment-relevant XBRL concepts
    segment_concepts = [
        "OperatingIncomeLoss",
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "DepreciationAndAmortization",
        "InterestExpense",
        "GeneralAndAdministrativeExpense",
        "AssetManagementCosts",
        "PropertyManagementFee",
    ]

    results = []
    for concept in segment_concepts:
        if concept not in facts:
            continue
        units = facts[concept].get("units", {})
        for unit_type, entries in units.items():
            if unit_type != "USD":
                continue
            # Look for entries with segment dimensions
            segment_entries = []
            consolidated_entries = []
            for entry in entries:
                segments = entry.get("segments", {})
                end_date = entry.get("end", "")
                filed = entry.get("filed", "")
                val = entry.get("val")
                if segments:
                    # Has segment dimension
                    seg_name = list(segments.values())[0] if segments else "?"
                    segment_entries.append({
                        "concept": concept,
                        "segment": seg_name,
                        "value": val,
                        "end": end_date,
                        "filed": filed,
                    })
                else:
                    consolidated_entries.append({
                        "concept": concept,
                        "segment": "Consolidated",
                        "value": val,
                        "end": end_date,
                        "filed": filed,
                    })

            # Get recent segment entries (last 8 periods)
            segment_entries.sort(key=lambda x: x["end"], reverse=True)
            for entry in segment_entries[:16]:
                results.append(
                    f"{entry['concept']} [{entry['segment']}]: "
                    f"${entry['value']:,.0f} "
                    f"(period ending {entry['end']}, filed {entry['filed']})"
                )

            # Also get recent consolidated entries for comparison
            consolidated_entries.sort(key=lambda x: x["end"], reverse=True)
            for entry in consolidated_entries[:4]:
                results.append(
                    f"{entry['concept']} [Consolidated]: "
                    f"${entry['value']:,.0f} "
                    f"(period ending {entry['end']}, filed {entry['filed']})"
                )

    if not results:
        return f"No segment financial data found in XBRL for {entity_name}. Try sec_edgar_filing_text with section='segments' instead."

    header = f"SEC EDGAR segment financial data for {entity_name}:\n"
    return header + "\n".join(results[:60])


def _summarize_financials(data: dict, entity_name: str) -> str:
    """Return a summary of key financial metrics."""
    facts = data.get("facts", {}).get("us-gaap", {})
    key_concepts = [
        # Income statement
        "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
        "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
        "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss",
        "OperatingExpenses", "SellingGeneralAndAdministrativeExpense",
        "ResearchAndDevelopmentExpense", "DepreciationAndAmortization",
        "InterestExpense", "IncomeTaxExpenseBenefit",
        "EarningsPerShareBasic", "EarningsPerShareDiluted",
        # Balance sheet
        "Assets", "Liabilities", "StockholdersEquity",
        "CashAndCashEquivalentsAtCarryingValue",
        "AccountsReceivableNetCurrent", "InventoryNet",
        "PropertyPlantAndEquipmentNet", "Goodwill", "IntangibleAssetsNetExcludingGoodwill",
        "AccountsPayableCurrent", "LongTermDebt", "LongTermDebtNoncurrent",
        "ShortTermBorrowings", "DebtCurrent",
        "CommonStockSharesOutstanding",
        # Cash flow
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInInvestingActivities",
        "NetCashProvidedByUsedInFinancingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsOfDividends",
    ]
    results = []
    for concept in key_concepts:
        if concept in facts:
            units = facts[concept].get("units", {})
            for unit_type, entries in units.items():
                if unit_type != "USD":
                    continue
                recent = sorted(entries, key=lambda x: x.get("end", ""), reverse=True)[:2]
                for entry in recent:
                    results.append(
                        f"{concept}: ${entry.get('val', 'N/A'):,} "
                        f"(period ending {entry.get('end', '?')}, filed {entry.get('filed', '?')})"
                    )

    if not results:
        return f"No key financial metrics found in XBRL data for {entity_name}"

    header = f"SEC EDGAR financial summary for {entity_name}:\n"
    return header + "\n".join(results[:20])


def _html_to_text(raw: str) -> str:
    """Convert HTML filing content to clean readable text.

    Tables are parsed structurally — each data cell is annotated with its
    column header so downstream extractors know which column a value belongs to.
    E.g., "Total [Next 12 Months]: $ 14,426,266" instead of just "$ 14,426,266".
    """
    import re
    import html as html_mod

    # First, convert tables to column-annotated text
    def _parse_table(table_html: str) -> str:
        """Parse an HTML table into column-annotated text."""
        # Strip ix: tags and extract cell contents
        clean = re.sub(r'<ix:[^>]+>', '', table_html)
        clean = re.sub(r'</ix:[^>]+>', '', clean)

        # Extract rows
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', clean, re.DOTALL)
        if not rows:
            return ""

        # Extract cells from each row (handle colspan)
        def _extract_cells(row_html: str, fill_colspan: bool = False) -> list[str]:
            cells = []
            for m in re.finditer(r'<td[^>]*(?:colspan="(\d+)")?[^>]*>(.*?)</td>', row_html, re.DOTALL):
                colspan = int(m.group(1)) if m.group(1) else 1
                cell_text = re.sub(r'<[^>]+>', '', m.group(2))
                cell_text = html_mod.unescape(cell_text).replace('\xa0', ' ').strip()
                cells.append(cell_text)
                for _ in range(colspan - 1):
                    # For headers, fill all spanned columns with the same text
                    # For data, use empty (values are in specific cells)
                    cells.append(cell_text if fill_colspan else "")
            return cells

        parsed_rows = [_extract_cells(r, fill_colspan=False) for r in rows]
        if not parsed_rows:
            return ""

        # Identify header rows: rows before the first row with a "$" sign
        header_rows = []
        data_start = 0
        for i, row in enumerate(parsed_rows):
            row_text = " ".join(row)
            if "$" in row_text or any(c.replace(",", "").replace(".", "").replace("-", "").isdigit()
                                      and len(c.replace(",", "").replace(".", "").replace("-", "")) > 3
                                      for c in row if c):
                # Check if this looks like a data row (has substantial numbers)
                has_number = any(
                    re.match(r'^[\d,.\-()]+$', c.strip().replace(" ", ""))
                    for c in row if c.strip() and len(c.strip()) > 2
                )
                if has_number or "$" in row_text:
                    data_start = i
                    break
            header_rows.append(row)

        # Build column headers by combining header row content
        # Re-parse header rows with fill_colspan=True so headers span all their columns
        max_cols = max(len(r) for r in parsed_rows) if parsed_rows else 0
        col_headers = [""] * max_cols
        for hr_html in rows[:data_start]:
            header_cells = _extract_cells(hr_html, fill_colspan=True)
            for j, cell in enumerate(header_cells):
                if j < max_cols and cell:
                    if col_headers[j] and cell not in col_headers[j]:
                        col_headers[j] += " " + cell
                    elif not col_headers[j]:
                        col_headers[j] = cell

        # Build ordered list of unique column headers (skip the row-label column)
        # The first non-empty header is usually the row label descriptor, not a data column
        first_header = next((h for h in col_headers if h), "")
        unique_headers = []
        seen = set()
        for h in col_headers:
            if h and h not in seen and h != first_header:
                unique_headers.append(h)
                seen.add(h)

        # Format data rows with column annotations
        lines = []
        # Include unit declaration if present in headers
        unit_line = ""
        for row in header_rows:
            row_text = " ".join(row).lower()
            if "in millions" in row_text or "in thousands" in row_text or "in billions" in row_text:
                unit_line = " ".join(c for c in row if c).strip()
                break

        if unit_line:
            lines.append(f"({unit_line})")

        for row in parsed_rows[data_start:]:
            # First non-empty cell is usually the row label
            row_label = ""
            values = []
            pending_dollar = False
            for cell in row:
                if not cell:
                    continue
                if cell.strip() == "$":
                    pending_dollar = True  # Attach to next value cell
                    continue
                if not row_label and not re.match(r'^[\d,.\-()% ]+$', cell.strip()):
                    row_label = cell
                    pending_dollar = False
                else:
                    if cell.strip():
                        val = f"${cell.strip()}" if pending_dollar else cell.strip()
                        values.append(val)
                        pending_dollar = False

            if row_label and values:
                # Match values to column headers by ordinal position
                parts = []
                for i, val in enumerate(values):
                    if i < len(unique_headers):
                        parts.append(f"[{unique_headers[i]}]: {val}")
                    else:
                        parts.append(val)
                lines.append(f"{row_label} {' '.join(parts)}")
            elif row_label:
                lines.append(row_label)

        return "\n".join(lines)

    # Replace tables with structured text
    def _replace_table(match):
        structured = _parse_table(match.group(0))
        return f"\n{structured}\n" if structured else ""

    text = re.sub(r'<style[^>]*>.*?</style>', '', raw, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<table[^>]*>.*?</table>', _replace_table, text, flags=re.DOTALL)

    # Now strip remaining HTML tags
    text = re.sub(r'<ix:[^>]+>', '', text)
    text = re.sub(r'</ix:[^>]+>', '', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_mod.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' ([.,;:])', r'\1', text)
    return text.strip()


def _extract_section(text: str, section: str, max_chars: int = 15000) -> str:
    """Extract a specific section from filing text."""
    section_markers = {
        "mda": ["management's discussion", "md&a", "results of operations", "liquidity and capital"],
        "risk": ["risk factors"],
        "financial_statements": ["financial statements", "consolidated balance"],
        "notes": ["notes to consolidated", "notes to the consolidated", "note 1"],
        "debt": ["debt and preferred equity", "mortgage notes", "credit facility", "term loan", "unsecured debt", "notes payable"],
        "acquisitions": ["acquisition", "business combination"],
        "impairment": ["impairment", "goodwill"],
        "segments": ["segment information", "reportable segment", "segment reporting"],
        "revenue": ["disaggregation of revenue", "revenue from contracts by type", "revenue by channel", "revenue by geography", "revenue recognition"],
        "compensation": ["director compensation", "executive compensation", "compensation discussion"],
        "leases": ["operating lease", "lease obligations", "right-of-use"],
        "tax": ["effective income tax rate", "effective tax rate", "total provision for income tax", "provision for income tax", "income tax expense"],
        "employees": ["human capital", "number of employees", "full-time employees", "headcount"],
        "shares": ["shares outstanding", "share repurchase", "stock repurchase"],
        "cash_obligations": ["material cash requirement", "contractual obligation", "future minimum payment",
                             "cash commitment", "total projected"],
        "reconciliation": ["reconciliation of gaap to non-gaap", "adjusted ebitda reconcil",
                           "reconciliation of net income to adjusted ebitda",
                           "non-gaap financial measure", "stock-based compensation expense"],
        "kpi": ["operating statistics", "average monthly revenue per paying", "average revenue per",
                "key performance", "operational highlights", "key operating metrics",
                "global streaming memberships"],
        "officers": ["power of attorney", "pursuant to the requirements of section 13",
                     "information about our executive officers",
                     "principal financial officer", "chief financial officer"],
    }
    # If section is in the known map, use curated markers. Otherwise, search for
    # the section name itself (with underscores replaced by spaces for natural text matching).
    # This lets the planner specify ANY section without needing a hardcoded entry.
    fallback_marker = section.lower().replace("_", " ")
    markers = section_markers.get(section.lower(), [fallback_marker])
    text_lower = text.lower()
    # Try markers in priority order — first marker found wins (not earliest position).
    # BUT: skip occurrences in ToC/forward-looking sections (early filing text with no data).
    # If the first match has no numbers within 500 chars, try later occurrences.
    import re as _re
    for marker in markers:
        start_pos = 0
        while True:
            idx = text_lower.find(marker, start_pos)
            if idx < 0:
                break
            # Check if there are actual numbers near this occurrence (not just a ToC reference)
            nearby = text[idx:idx + 500]
            has_data = bool(_re.search(r'\d{2,}', nearby))  # At least a 2+ digit number
            if has_data:
                return text[max(0, idx - 200):idx + max_chars]
            # No data nearby — skip to next occurrence
            start_pos = idx + len(marker)
        # If no data-rich occurrence found, use the first occurrence anyway
        idx = text_lower.find(marker)
        if idx >= 0:
            return text[max(0, idx - 200):idx + max_chars]
    return text[:max_chars]


def _find_filing(cik: str, filing_type: str, period: str | None = None) -> tuple[str | None, str | None, str | None]:
    """Find a filing by type and optional period. Returns (accession, primary_doc, filing_date).

    period examples: "Q1 2024", "Q3 2025", "2024" (annual), "2023"
    """
    time.sleep(0.15)
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = httpx.get(url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError):
        return None, None, None

    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])

    # Parse period into target date range
    target_year = None
    target_quarter = None
    if period:
        period = period.strip().upper()
        if period.startswith("Q") and len(period) >= 6:
            # "Q1 2024" format
            target_quarter = int(period[1])
            target_year = int(period.split()[-1])
        elif len(period) == 4 and period.isdigit():
            # "2024" format — annual
            target_year = int(period)

    quarter_end_months = {1: "03", 2: "06", 3: "09", 4: "12"}

    for i in range(len(forms)):
        if forms[i] != filing_type:
            continue

        if period and target_year:
            report_date = report_dates[i] if i < len(report_dates) else ""

            if target_quarter:
                # Match by exact report date quarter end
                expected_month = quarter_end_months[target_quarter]
                expected_prefix = f"{target_year}-{expected_month}"
                if not (report_date and report_date.startswith(expected_prefix)):
                    continue
            else:
                # Annual — for 10-K, match fiscal year end (typically Dec)
                # Report date should end with target_year-12 for calendar year companies
                if not (report_date and report_date.startswith(f"{target_year}-12")):
                    # Also accept if the report date year matches (non-Dec fiscal year)
                    if not (report_date and report_date.startswith(str(target_year))):
                        continue

        accession = accessions[i]
        primary_doc = primary_docs[i] if i < len(primary_docs) else None
        filing_date = dates[i] if i < len(dates) else None
        return accession, primary_doc, filing_date

    # Fallback: search supplementary filing history files for older filings
    for file_info in data.get("filings", {}).get("files", []):
        fname = file_info.get("name", "")
        if not fname:
            continue
        time.sleep(0.15)
        try:
            supp_url = f"https://data.sec.gov/submissions/{fname}"
            supp_resp = httpx.get(supp_url, headers=SEC_HEADERS, timeout=15)
            supp_resp.raise_for_status()
            supp = supp_resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError):
            continue

        s_forms = supp.get("form", [])
        s_accessions = supp.get("accessionNumber", [])
        s_primary_docs = supp.get("primaryDocument", [])
        s_dates = supp.get("filingDate", [])
        s_report_dates = supp.get("reportDate", [])

        for i in range(len(s_forms)):
            if s_forms[i] != filing_type:
                continue
            if period and target_year:
                report_date = s_report_dates[i] if i < len(s_report_dates) else ""
                if target_quarter:
                    expected_month = quarter_end_months[target_quarter]
                    expected_prefix = f"{target_year}-{expected_month}"
                    if not (report_date and report_date.startswith(expected_prefix)):
                        continue
                else:
                    if not (report_date and report_date.startswith(f"{target_year}-12")):
                        if not (report_date and report_date.startswith(str(target_year))):
                            continue

            accession = s_accessions[i]
            primary_doc = s_primary_docs[i] if i < len(s_primary_docs) else None
            filing_date = s_dates[i] if i < len(s_dates) else None
            return accession, primary_doc, filing_date

    return None, None, None


def _fetch_filing_doc(cik: str, accession: str, primary_doc: str | None) -> str | None:
    """Fetch a filing document's raw HTML."""
    cik_num = cik.lstrip("0")
    acc_clean = accession.replace("-", "")
    if primary_doc:
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}/{primary_doc}"
    else:
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}"

    time.sleep(0.15)
    try:
        resp = httpx.get(doc_url, headers=SEC_HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except (httpx.HTTPStatusError, httpx.RequestError):
        return None


def get_filing_text(
    ticker: str,
    filing_type: str = "10-Q",
    section: str | None = None,
    period: str | None = None,
    max_chars: int = 15000,
) -> str:
    """Fetch an SEC filing and return its text content.

    This reads the actual filing document (not just XBRL data), which contains
    MD&A, footnotes, deal terms, and other narrative details.

    Args:
        ticker: Company ticker symbol
        filing_type: "10-K", "10-Q", "8-K", etc.
        section: Optional section filter (e.g., "mda", "risk", "debt", "segments")
        period: Optional period filter (e.g., "Q1 2024", "Q3 2025", "2024").
                If omitted, returns the most recent filing of this type.
        max_chars: Maximum characters to return (default 15000, use 50000 for qualitative)
    """
    try:
        cik = _ticker_to_cik(ticker)
    except ValueError as e:
        return str(e)

    accession, primary_doc, filing_date = _find_filing(cik, filing_type, period)
    if not accession:
        period_str = f" for period {period}" if period else ""
        return f"No {filing_type} filing found for {ticker}{period_str}"

    raw = _fetch_filing_doc(cik, accession, primary_doc)
    if not raw:
        return f"Failed to fetch {filing_type} document for {ticker}"

    text = _html_to_text(raw)

    cik_num = cik.lstrip("0")
    acc_clean = accession.replace("-", "")
    if primary_doc:
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}/{primary_doc}"
    else:
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}"

    if section:
        text = _extract_section(text, section, max_chars)
    else:
        text = text[:max_chars]

    header = (
        f"SEC EDGAR {filing_type} for {ticker} (filed {filing_date or '?'}, "
        f"accession: {accession}, period: {period or 'most recent'})\n"
        f"URL: {doc_url}\n"
        f"Citation: {ticker} Form {filing_type}, filed {filing_date or '?'}\n\n"
    )
    return header + text.strip()


def get_earnings_press_release(ticker: str, quarter: str) -> str:
    """Fetch the earnings press release for a specific quarter.

    This finds the 8-K with the earnings exhibit (Exhibit 99.1 / press release)
    for the given quarter, not just any 8-K.

    Handles non-calendar fiscal years (e.g., Micron FY ends Sep, Oracle FY ends May)
    by scanning all recent 8-Ks and matching by exhibit filename or content.

    Args:
        ticker: Company ticker symbol
        quarter: e.g., "Q4 2024", "Q1 2025", "Q3 2024"

    Returns formatted text from the press release exhibit.
    """
    import re as _re

    try:
        cik = _ticker_to_cik(ticker)
    except ValueError as e:
        return str(e)

    # Parse quarter
    q_match = _re.match(r'Q(\d)\s*(\d{4})', quarter.strip())
    if not q_match:
        return f"Invalid quarter format: '{quarter}'. Use 'Q4 2024' format."

    q_num = int(q_match.group(1))
    year = int(q_match.group(2))

    time.sleep(0.15)
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = httpx.get(url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return f"SEC EDGAR request failed: {e}"

    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])

    # Collect ALL 8-Ks from a wide window (18 months around the expected date)
    # This handles non-calendar fiscal years where the filing date doesn't match
    # the calendar quarter mapping
    wide_start = f"{year - 1}-07"
    wide_end = f"{year + 1}-06"
    all_8ks = []
    for i in range(len(forms)):
        if forms[i] != "8-K":
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if filing_date >= wide_start and filing_date <= wide_end + "-31":
            all_8ks.append((filing_date, accessions[i]))

    if not all_8ks:
        return f"No 8-K filings found for {ticker} around {quarter}"

    # Two-pass approach:
    # Pass 1: Find exhibit whose filename contains the fiscal quarter (e.g., "q3" in "a2024q3ex991")
    # Pass 2: Fall back to calendar-quarter date window matching
    cik_num = cik.lstrip("0")
    quarter_tag = f"q{q_num}"  # e.g., "q3"
    year_tag = str(year)       # e.g., "2024"

    def _find_exhibit(candidates):
        """Search candidates for a press release exhibit. Returns (exhibit_url, filing_date, accession) or None."""
        for filing_date, accession in candidates:
            acc_clean = accession.replace("-", "")
            index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}/"

            time.sleep(0.15)
            try:
                resp = httpx.get(index_url, headers=SEC_HEADERS, timeout=15, follow_redirects=True)
                resp.raise_for_status()
            except (httpx.HTTPStatusError, httpx.RequestError):
                continue

            for match in _re.finditer(r'href="([^"]*)"', resp.text):
                href = match.group(1)
                href_lower = href.lower()
                if any(kw in href_lower for kw in ["pressreleas", "ex99", "earnings", "exhibit99", "shareholder", "letter"]):
                    if href.startswith("/"):
                        exhibit_url = f"https://www.sec.gov{href}"
                    else:
                        exhibit_url = f"{index_url}{href}"
                    return exhibit_url, filing_date, accession, href_lower

        return None

    # Pass 1: Match by fiscal quarter in exhibit filename (handles non-calendar FY)
    # e.g., "a2024q3ex991-pressrelease.htm" contains "q3" and "2024"
    best_exhibit = None
    for filing_date, accession in all_8ks:
        acc_clean = accession.replace("-", "")
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}/"

        time.sleep(0.15)
        try:
            resp_idx = httpx.get(index_url, headers=SEC_HEADERS, timeout=15, follow_redirects=True)
            resp_idx.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError):
            continue

        for match in _re.finditer(r'href="([^"]*)"', resp_idx.text):
            href = match.group(1)
            href_lower = href.lower()
            if any(kw in href_lower for kw in ["pressreleas", "ex99", "earnings", "exhibit99", "shareholder", "letter"]):
                # Check if filename contains the fiscal quarter tag
                # Try both 4-digit ("2025") and 2-digit ("25") year matching
                # because companies use "fy25" or "fy2025" interchangeably
                year_tag_short = str(year)[-2:]  # "25"
                year_match = year_tag in href_lower or f"fy{year_tag_short}" in href_lower
                if quarter_tag in href_lower and year_match:
                    if href.startswith("/"):
                        exhibit_url = f"https://www.sec.gov{href}"
                    else:
                        exhibit_url = f"{index_url}{href}"
                    best_exhibit = (exhibit_url, filing_date, accession)
                    break
        if best_exhibit:
            break

    # Pass 2: Fall back to calendar-quarter date window
    if not best_exhibit:
        filing_windows = {
            4: (f"{year + 1}-01", f"{year + 1}-03"),
            1: (f"{year}-04", f"{year}-06"),
            2: (f"{year}-07", f"{year}-09"),
            3: (f"{year}-10", f"{year}-12"),
        }
        start_month, end_month = filing_windows[q_num]
        window_candidates = [(d, a) for d, a in all_8ks
                             if d >= start_month and d <= end_month + "-31"]
        result = _find_exhibit(window_candidates)
        if result:
            best_exhibit = (result[0], result[1], result[2])

    if not best_exhibit:
        return f"No earnings press release exhibit found for {ticker} {quarter}"

    exhibit_url, filing_date, accession = best_exhibit

    # Fetch the exhibit
    time.sleep(0.15)
    try:
        resp = httpx.get(exhibit_url, headers=SEC_HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError):
        return f"Failed to fetch earnings exhibit for {ticker} {quarter}"

    text = _html_to_text(resp.text)
    text = text[:15000]  # Cap for context

    header = (
        f"EARNINGS PRESS RELEASE for {ticker} {quarter}\n"
        f"Filed: {filing_date}, Accession: {accession}\n"
        f"Source: {exhibit_url}\n\n"
    )
    return header + text


def get_recent_filings(ticker: str, filing_type: str | None = None, count: int = 5) -> str:
    """Get recent SEC filings for a company."""
    try:
        cik = _ticker_to_cik(ticker)
    except ValueError as e:
        return str(e)

    time.sleep(0.15)

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = httpx.get(url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return f"SEC EDGAR API error: {e.response.status_code}"
    except httpx.RequestError as e:
        return f"SEC EDGAR request failed: {e}"

    data = resp.json()
    entity_name = data.get("name", ticker)
    recent = data.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    descriptions = recent.get("primaryDocDescription", [])
    accessions = recent.get("accessionNumber", [])

    results = []
    for i in range(min(len(forms), 50)):
        if filing_type and forms[i] != filing_type:
            continue
        acc = accessions[i].replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc}"
        results.append(
            f"{forms[i]} — filed {dates[i]} — {descriptions[i] or 'N/A'} — {url}"
        )
        if len(results) >= count:
            break

    if not results:
        ft = f" of type {filing_type}" if filing_type else ""
        return f"No recent filings{ft} found for {entity_name}"

    header = f"Recent SEC filings for {entity_name}:\n"
    return header + "\n".join(results)
