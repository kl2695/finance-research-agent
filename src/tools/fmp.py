"""Financial Modeling Prep (FMP) API client.

Requires FMP_API_KEY environment variable.
"""

from __future__ import annotations

import json
import os

import httpx

FMP_BASE = "https://financialmodelingprep.com/api/v3"


def fmp_request(
    ticker: str,
    endpoint: str,
    period: str = "annual",
    limit: int = 4,
) -> str:
    """Generic FMP API request. Returns formatted string for Claude."""
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        return "Error: FMP_API_KEY not set. Skipping financial data lookup."

    url_map = {
        "profile": f"{FMP_BASE}/profile/{ticker}",
        "income-statement": f"{FMP_BASE}/income-statement/{ticker}",
        "balance-sheet": f"{FMP_BASE}/balance-sheet-statement/{ticker}",
        "cash-flow": f"{FMP_BASE}/cash-flow-statement/{ticker}",
        "ratios": f"{FMP_BASE}/ratios/{ticker}",
        "quote": f"{FMP_BASE}/quote/{ticker}",
    }

    url = url_map.get(endpoint)
    if not url:
        return f"Unknown FMP endpoint: {endpoint}"

    params: dict = {"apikey": api_key}
    if endpoint not in ("profile", "quote"):
        params["period"] = period
        params["limit"] = limit

    try:
        resp = httpx.get(url, params=params, timeout=15)
    except httpx.RequestError as e:
        return f"FMP request failed: {e}"

    if resp.status_code != 200:
        return f"FMP API error: HTTP {resp.status_code}"

    data = resp.json()
    if not data:
        return f"No FMP data found for {ticker} ({endpoint})"

    # Format as readable JSON, truncated if too long
    text = json.dumps(data, indent=2)
    if len(text) > 6000:
        text = text[:6000] + "\n... [truncated]"

    return f"FMP {endpoint} data for {ticker}:\n{text}"
