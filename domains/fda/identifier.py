"""FDA identifier extraction — K-numbers, PMA numbers, product codes."""

from __future__ import annotations

import re


def extract_identifier(text: str) -> str | None:
    """Extract the primary FDA entity identifier from free-form text.

    Priority: K-number > PMA number > product code.
    """
    if not text:
        return None

    # K-number: K + 6 digits
    match = re.search(r'\bK\d{6}\b', text, re.IGNORECASE)
    if match:
        return match.group().upper()

    # PMA number: P + 6 digits
    match = re.search(r'\bP\d{6}\b', text, re.IGNORECASE)
    if match:
        return match.group().upper()

    # Product code: exactly 3 uppercase letters (with word boundaries)
    # Must filter out common English words
    for match in re.finditer(r'\b([A-Z]{3})\b', text):
        candidate = match.group(1)
        if candidate not in _EXCLUDED_WORDS:
            return candidate

    return None


_EXCLUDED_WORDS = {
    "THE", "AND", "FOR", "NOT", "BUT", "ALL", "ANY", "HAS", "HAD",
    "WAS", "ARE", "HIS", "HER", "ITS", "OUR", "WHO", "HOW", "WHY",
    "FDA", "USA", "PMA", "IDE", "HDE", "CFR", "MDR", "GMP", "QSR",
    "OTC", "NDA", "BLA", "IND", "IRB", "ICH", "ISO", "CEO", "CFO",
    "CTO", "COO", "LLC", "INC", "LTD", "YES", "YET", "USE", "MAY",
    "CAN", "NOW", "NEW", "OLD", "TWO", "ONE", "TEN", "DAY", "END",
}
