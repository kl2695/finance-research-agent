"""Shim — re-exports from domains.finance.tools for backwards compatibility."""
from domains.finance.tools import (  # noqa: F401
    get_company_facts,
    get_recent_filings,
    get_filing_text,
    get_segment_financials,
    get_earnings_press_release,
    _html_to_text,
    _extract_section,
    _find_filing,
    _ticker_to_cik,
)
