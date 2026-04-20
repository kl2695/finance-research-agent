"""Robust ticker extraction and resolution.

Handles messy planner output like "Lyft, Inc., ticker LYFT, CIK 0001759509"
and extracts a clean ticker that works with SEC EDGAR.
"""

from __future__ import annotations

import re


def extract_ticker(text: str) -> str | None:
    """Extract a stock ticker from free-form text.

    Handles:
    - "ticker LYFT" / "ticker: LYFT"
    - "NASDAQ: LYFT" / "NYSE: X"
    - "(LYFT)" / "(NASDAQ: LYFT)"
    - "LYFT" as standalone uppercase word
    - "Lyft, Inc., ticker LYFT, CIK 0001759509"
    """
    if not text:
        return None

    # Strategy 1: "ticker LYFT" or "ticker: LYFT"
    match = re.search(r'ticker[:\s]+([A-Z]{1,5})\b', text, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Strategy 2: "NASDAQ: LYFT" or "NYSE: X"
    match = re.search(r'(?:NASDAQ|NYSE|AMEX|OTC)[:\s]+([A-Z]{1,5})\b', text, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Strategy 3: "(LYFT)" or "(NASDAQ: LYFT)"
    match = re.search(r'\((?:NASDAQ:\s*|NYSE:\s*)?([A-Z]{1,5})\)', text)
    if match:
        candidate = match.group(1)
        if candidate not in _EXCLUDED_WORDS:
            return candidate

    # Strategy 4: Find standalone uppercase words 1-5 chars
    words = re.findall(r'\b([A-Z]{1,5})\b', text)
    for w in words:
        if w not in _EXCLUDED_WORDS:
            return w

    return None


# Words that look like tickers but aren't
_EXCLUDED_WORDS = {
    "CIK", "INC", "LLC", "LTD", "CORP", "NYSE", "NASDAQ", "AMEX",
    "OTC", "SEC", "CEO", "CFO", "COO", "CTO", "VP", "SVP", "EVP",
    "USA", "USD", "FY", "YTD", "TTM", "QTD", "EPS", "PE", "PEG",
    "THE", "AND", "FOR", "NOT", "BUT", "ALL", "ANY", "HAS", "HAD",
    "WAS", "ARE", "HIS", "HER", "ITS", "OUR", "WHO", "HOW", "WHY",
    "GAAP", "NON", "PRE", "POST", "NET", "REV", "ADJ", "DIL",
    "IPO", "M&A", "ETF", "ESG", "DEF", "FORM",
}
