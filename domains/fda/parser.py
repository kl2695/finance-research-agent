"""FDA response parsers — JSON field extraction and date normalization."""

from __future__ import annotations

import re
from typing import Any


def parse_510k_json(result: dict) -> list[dict]:
    """Parse a 510(k) API result into structured facts for extraction."""
    facts = []
    openfda = result.get("openfda", {})

    fields = {
        "k_number": result.get("k_number"),
        "applicant": result.get("applicant"),
        "device_name": result.get("device_name"),
        "product_code": result.get("product_code"),
        "decision_date": result.get("decision_date"),
        "date_received": result.get("date_received"),
        "decision_description": result.get("decision_description"),
        "clearance_type": result.get("clearance_type"),
        "device_class": openfda.get("device_class"),
        "advisory_committee": result.get("advisory_committee_description"),
        "regulation_number": openfda.get("regulation_number"),
    }

    # Compute clearance time if both dates available
    if fields["decision_date"] and fields["date_received"]:
        try:
            from datetime import datetime
            dec = datetime.strptime(fields["decision_date"], "%Y-%m-%d")
            rec = datetime.strptime(fields["date_received"], "%Y-%m-%d")
            fields["clearance_days"] = (dec - rec).days
        except ValueError:
            pass

    for concept, value in fields.items():
        if value is not None:
            facts.append({
                "concept": concept,
                "value": value,
                "source": f"openFDA 510(k) ({result.get('k_number', '?')})",
                "confidence": "high",
            })

    return facts


def parse_maude_json(result: dict) -> list[dict]:
    """Parse a MAUDE event result into structured facts."""
    facts = []
    devices = result.get("device", [])
    device = devices[0] if devices else {}

    fields = {
        "event_type": result.get("event_type"),
        "date_received": normalize_date(result.get("date_received", "")),
        "report_number": result.get("report_number"),
        "brand_name": device.get("brand_name"),
        "manufacturer": device.get("manufacturer_d_name"),
        "product_code": device.get("device_report_product_code"),
    }

    for concept, value in fields.items():
        if value:
            facts.append({
                "concept": concept,
                "value": value,
                "source": f"MAUDE ({result.get('report_number', '?')})",
                "confidence": "high",
            })

    # Extract narrative text
    for text_entry in result.get("mdr_text", []):
        if text_entry.get("text_type_code") == "Description of Event or Problem":
            facts.append({
                "concept": "event_narrative",
                "value": text_entry.get("text", ""),
                "source": f"MAUDE narrative ({result.get('report_number', '?')})",
                "confidence": "high",
            })
            break

    return facts


def parse_classification_json(result: dict) -> list[dict]:
    """Parse a classification result into structured facts."""
    submission_types = {
        "1": "510(k)", "2": "PMA", "3": "De Novo",
        "4": "Not Classified", "5": "HDE", "7": "Exempt",
    }

    facts = []
    fields = {
        "product_code": result.get("product_code"),
        "device_name": result.get("device_name"),
        "device_class": result.get("device_class"),
        "regulatory_pathway": submission_types.get(result.get("submission_type_id", ""), "Unknown"),
        "regulation_number": result.get("regulation_number"),
        "medical_specialty": result.get("medical_specialty_description"),
        "implant_flag": result.get("implant_flag"),
        "life_sustaining": result.get("life_sustain_support_flag"),
    }

    for concept, value in fields.items():
        if value:
            facts.append({
                "concept": concept,
                "value": value,
                "source": f"FDA Classification ({result.get('product_code', '?')})",
                "confidence": "high",
            })

    return facts


def normalize_date(date_str: str) -> str:
    """Normalize FDA date formats to YYYY-MM-DD.

    Handles: YYYYMMDD (MAUDE), YYYY-MM-DD (510k/recall), MM/DD/YYYY (AccessData HTML).
    """
    if not date_str:
        return ""

    # Already YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return date_str

    # YYYYMMDD (MAUDE format)
    if re.match(r'^\d{8}$', date_str):
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    # MM/DD/YYYY (AccessData HTML)
    m = re.match(r'^(\d{2})/(\d{2})/(\d{4})$', date_str)
    if m:
        return f"{m.group(3)}-{m.group(1)}-{m.group(2)}"

    return date_str  # Return as-is if unrecognized
