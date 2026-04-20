"""Tool definitions and executor for the finance research agent."""

from __future__ import annotations

from src.tools.sec_edgar import get_company_facts, get_recent_filings, get_filing_text, get_segment_financials, get_earnings_press_release
from src.tools.fmp import fmp_request

# Anthropic server-side web search
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}

SEC_EDGAR_FINANCIALS_TOOL = {
    "name": "sec_edgar_financials",
    "description": (
        "Get structured XBRL financial data from SEC EDGAR. "
        "Use exact XBRL concept names for the metric filter: "
        "Revenues, CostOfGoodsAndServicesSold, CostOfRevenue, GrossProfit, "
        "OperatingIncomeLoss, NetIncomeLoss, InventoryNet, Assets, Liabilities, "
        "StockholdersEquity, CashAndCashEquivalentsAtCarryingValue, "
        "AccountsReceivableNetCurrent, LongTermDebt, PropertyPlantAndEquipmentNet, "
        "CommonStockSharesOutstanding, DepreciationAndAmortization, InterestExpense, "
        "EarningsPerShareBasic, EarningsPerShareDiluted. "
        "Without a metric filter, returns a summary of all key metrics."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Company ticker symbol"},
            "metric": {"type": "string", "description": "XBRL concept name (e.g., 'InventoryNet', 'CostOfGoodsAndServicesSold', 'Revenues')"},
        },
        "required": ["ticker"],
    },
}

SEC_EDGAR_FILING_TEXT_TOOL = {
    "name": "sec_edgar_filing_text",
    "description": (
        "Read the actual text of an SEC filing (10-K, 10-Q, 8-K). "
        "Returns narrative content: MD&A, footnotes, deal terms, risk factors, segment details. "
        "Supports historical filings via the period parameter."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Company ticker symbol"},
            "filing_type": {"type": "string", "enum": ["10-K", "10-Q", "8-K"], "description": "Type of filing"},
            "section": {
                "type": "string",
                "enum": ["mda", "risk", "financial_statements", "notes", "debt", "acquisitions", "impairment", "segments"],
                "description": "Optional: focus on a specific section",
            },
            "period": {"type": "string", "description": "Optional: e.g., 'Q1 2024', 'Q3 2025', '2024'"},
        },
        "required": ["ticker", "filing_type"],
    },
}

FMP_FINANCIALS_TOOL = {
    "name": "fmp_financials",
    "description": "Get financial data from Financial Modeling Prep: company profile, income statement, balance sheet, cash flow, ratios, or quote.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Company ticker symbol"},
            "endpoint": {
                "type": "string",
                "enum": ["profile", "income-statement", "balance-sheet", "cash-flow", "ratios", "quote"],
                "description": "Which data to retrieve",
            },
            "period": {"type": "string", "enum": ["annual", "quarter"], "description": "Period type (default: annual)"},
            "limit": {"type": "integer", "description": "Number of periods (default: 4)"},
        },
        "required": ["ticker", "endpoint"],
    },
}

SEC_EDGAR_EARNINGS_TOOL = {
    "name": "sec_edgar_earnings",
    "description": (
        "Fetch the earnings press release for a specific quarter from SEC EDGAR. "
        "Returns the actual press release exhibit (Exhibit 99.1) from the 8-K filing, "
        "NOT the 8-K cover page. Contains non-GAAP metrics, segment data, guidance, "
        "and quarterly financials that aren't in XBRL. "
        "Use for: Adjusted EBITDA, gross bookings, non-GAAP EPS, quarterly guidance, beat/miss analysis."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Company ticker symbol"},
            "quarter": {"type": "string", "description": "Quarter in format 'Q4 2024', 'Q1 2025', etc."},
        },
        "required": ["ticker", "quarter"],
    },
}

ALL_TOOLS = [
    WEB_SEARCH_TOOL,
    SEC_EDGAR_FINANCIALS_TOOL,
    SEC_EDGAR_FILING_TEXT_TOOL,
    SEC_EDGAR_EARNINGS_TOOL,
    FMP_FINANCIALS_TOOL,
]


def execute_tool(name: str, input_data: dict) -> str:
    """Execute a local tool. Server-side tools (web_search) never reach this."""
    if name == "sec_edgar_financials":
        return get_company_facts(
            ticker=input_data["ticker"],
            metric=input_data.get("metric"),
        )
    elif name == "sec_edgar_filing_text":
        return get_filing_text(
            ticker=input_data["ticker"],
            filing_type=input_data["filing_type"],
            section=input_data.get("section"),
            period=input_data.get("period"),
        )
    elif name == "sec_edgar_earnings":
        return get_earnings_press_release(
            ticker=input_data["ticker"],
            quarter=input_data["quarter"],
        )
    elif name == "fmp_financials":
        return fmp_request(
            ticker=input_data["ticker"],
            endpoint=input_data["endpoint"],
            period=input_data.get("period", "annual"),
            limit=input_data.get("limit", 4),
        )
    else:
        return f"Unknown tool: {name}"
