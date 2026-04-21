"""FinanceDomain — SEC EDGAR financial research domain implementation."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

from domains.base import Domain
from domains.finance.concepts import FINANCIAL_CONCEPTS
from domains.finance.methodology import FINANCIAL_METHODOLOGY
from domains.finance.identifier import extract_ticker
from domains.finance.tools import (
    get_company_facts, get_filing_text, get_earnings_press_release,
)
from domains.finance.fmp import fmp_request
from core.types import BenchmarkQuestion, FilingRequest, ToolResult

log = logging.getLogger(__name__)


class FinanceDomain(Domain):
    """SEC EDGAR financial research domain.

    Implements the Domain ABC for answering quantitative financial questions
    using SEC filings, XBRL data, and earnings press releases.
    """

    # ---- Identity ----

    @property
    def name(self) -> str:
        return "finance"

    # ---- Prompts ----
    # For now, return the EXACT same prompt strings as the current system.
    # This preserves byte-identical prompt behavior during migration.
    # Later steps will extract structural templates into core/.

    @property
    def planner_system(self) -> str:
        return f"""\
You are a financial research planner. Given a question, you create a structured research plan.

Your job is to:
1. Identify exactly what data points are needed to answer the question
2. Resolve any ambiguity (which company, which period, which metric definition)
3. Choose the CORRECT METHODOLOGY for this type of question
4. Define the calculation steps (if any) following the methodology
5. Choose the right structure: simple data lookup, multi-step calculation, or cross-company comparison

{FINANCIAL_CONCEPTS}

{FINANCIAL_METHODOLOGY}

Respond with valid JSON only. No markdown, no explanation outside the JSON."""

    @property
    def planner_prompt_template(self) -> str:
        return """\
Create a research plan for this financial question:

QUESTION: {question}

Today's date is {date}. Use this to determine "most recent" periods.

Return a JSON state dict with this structure:
{{
    "plan": "one-line description of what we're solving",
    "clarifications": {{
        "company": "full name, ticker, and CIK if known",
        "period": "exact period(s) needed, resolved from the question",
        "formula": "the calculation formula if applicable, or 'lookup only'",
        "source_strategy": "which documents to search — e.g., 'Q4 2024 earnings press release for actuals, Q3 2024 earnings transcript for guidance'",
        "definitions": {{}}  // any metric definitions that need to be precise
    }},
    "data_needed": {{
        "descriptive_key": {{
            "value": null,
            "unit": "USD millions or %, etc.",
            "source": null,
            "confidence": null,
            "attempts": [],
            "label": "human-readable description"
        }}
        // ... one entry per data point needed
    }},
    "filings_needed": [
        // List of specific SEC filings/data to fetch. The orchestrator fetches these BEFORE you start researching.
        // TYPES:
        //   "10-K" — annual filing. Specify section for deep data (kpi, tax, revenue, reconciliation, officers, etc.)
        //   "10-Q" — quarterly filing.
        //   "8-K"  — earnings press release (finds the exhibit automatically).
        //   "xbrl" — structured financial data (exact GAAP line items with period info). Specify concept names.
        //
        // EXAMPLES:
        //   {{"type": "xbrl", "concepts": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"], "reason": "annual revenue for CAGR"}}
        //   {{"type": "xbrl", "concepts": ["CostOfGoodsAndServicesSold", "CostOfRevenue"], "reason": "COGS for inventory turnover"}}
        //   {{"type": "xbrl", "concepts": ["InventoryNet"], "reason": "ending inventory"}}
        //   {{"type": "xbrl", "concepts": ["GrossProfit"], "reason": "gross profit for margin calculation"}}
        //   {{"type": "10-K", "period": "2024", "section": "kpi", "reason": "2022-2024 ARPU from operational highlights"}}
        //   {{"type": "10-K", "period": "2022", "section": "kpi", "reason": "2020-2022 ARPU from operational highlights"}}
        //   {{"type": "8-K", "period": "Q4 2024", "section": null, "reason": "Q4 2024 actual earnings results"}}
        //   {{"type": "8-K", "period": "Q3 2024", "section": null, "reason": "Q3 2024 guidance for Q4"}}
        //   {{"type": "10-K", "period": "2024", "section": "tax", "reason": "effective tax rate from income tax footnote"}}
        //   {{"type": "8-K", "period": "Q4 2024", "ticker": "META", "reason": "Meta Q4 2024 capex guidance"}}
        //   {{"type": "xbrl", "ticker": "PEP", "concepts": ["PaymentsOfDividends"], "reason": "PepsiCo FY2024 dividends"}}
        //
        // XBRL CONCEPT NAMES (common ones — try alternatives for each):
        //   Revenue: "Revenues" or "RevenueFromContractWithCustomerExcludingAssessedTax"
        //   COGS: "CostOfGoodsAndServicesSold" or "CostOfRevenue"
        //   Gross Profit: "GrossProfit"
        //   Net Income: "NetIncomeLoss"
        //   Total Assets: "Assets"
        //   Inventory: "InventoryNet"
        //   Cash: "CashAndCashEquivalentsAtCarryingValue"
        //   Operating Income: "OperatingIncomeLoss"
        //   Shares Outstanding: "CommonStockSharesOutstanding"
        //   Long-term Debt: "LongTermDebt" or "LongTermDebtNoncurrent"
        //   Stockholders Equity: "StockholdersEquity"
        //   Tax Expense: "IncomeTaxExpenseBenefit"
        //   D&A: "DepreciationAndAmortization"
        //
        // SECTION NAMES — use the topic as the section name. The system searches for it in the filing:
        //   "risk" — Item 1A Risk Factors (business risks, market risks)
        //   "mda" — Management's Discussion & Analysis
        //   "kpi" — Operational highlights, key performance metrics, ARPU
        //   "tax" — Income tax footnote (effective tax rate, deferred taxes)
        //   "revenue" — Revenue disaggregation (by channel, geography, customer type)
        //   "reconciliation" — Non-GAAP reconciliation tables
        //   "compensation" — Director/executive compensation
        //   "officers" — Executive officer names and titles (signature page)
        //   "debt" — Debt terms, maturities, credit facilities
        //   "leases" — Operating/finance lease schedules
        //   "segments" — Segment reporting and geographic breakdown
        //   Any other topic → use that phrase as the section name (e.g., "concentration_risk",
        //     "legal_proceedings", "goodwill_impairment", "pension_obligations"). The system
        //     will search for this phrase in the filing text.
        //
        // WHERE DIFFERENT DISCLOSURES LIVE:
        //   - "concentration risk", "vendor risk" → notes to financial statements (NOT risk factors)
        //   - "regulatory risks" → risk factors section (Item 1A)
        //   - specific KPIs (ARPU, take rate) → MD&A operational highlights
        //   - effective tax rate → income tax footnote in notes
        //   - contractual obligations → liquidity section of MD&A
        //
        // IMPORTANT:
        //   - For forward-looking data (cash requirements for 2025), use the PRIOR year's 10-K (period: "2024")
        //   - For beat/miss, include BOTH the actuals quarter (8-K) AND the prior quarter (8-K for guidance)
        //   - Each 10-K shows ~3 years of data. For 6-year trends, include 2+ 10-Ks.
        //   - Non-calendar fiscal years: use the company's FY convention (e.g., TJX FY2025 ends Jan 2025)
        //   - Use "xbrl" for standard GAAP line items. Use "10-K" sections for non-GAAP, KPIs, or disclosure tables.
    ],
    "entities": {{}},  // only for cross-company comparisons: {{"TICKER": {{"metric": {{"period": null}}}}}}
    "calculation_steps": [
        // only if calculation needed. Each step:
        // {{"step": "name", "formula": "python expression using data_needed keys", "inputs": ["key1", "key2"], "result": null}}
    ],
    "answer": {{
        "value": null,
        "formatted": null,
        "sources": [],
        "work_shown": null
    }}
}}

IMPORTANT:
- Use descriptive keys for data_needed (e.g., "revenue_fy2024" not "x1")
- For "3-year CAGR", use the most recent 3 fiscal years
- For "Q4 FY2025", check if the company has a non-standard fiscal year
- If the question is a simple lookup (no calculation), use empty calculation_steps
- For cross-company comparisons, use the entities dict with one entry per company
- Formulas must be valid Python expressions using the data_needed keys as variable names

SOURCE SELECTION BY QUESTION TYPE — include this in clarifications.source_strategy:
- Beat/miss vs guidance: search for the EARNINGS PRESS RELEASE or 8-K for actual results
  (e.g., "Company Q4 2024 earnings press release"). For guidance, search for the PRIOR
  quarter's earnings call transcript or press release (e.g., "Company Q3 2024 earnings guidance").
  Do NOT use 10-K/10-Q for beat/miss — they mix annual and quarterly figures.
- Quarterly-specific data: use 10-Q for that quarter, or the earnings press release / 8-K.
  The 10-K has full-year figures that are easy to confuse with quarterly.
- Annual financial data: use 10-K or sec_edgar_financials (XBRL).
- Non-GAAP metrics (Adjusted EBITDA, Free Cash Flow, non-GAAP EPS): these are in earnings
  press releases and 8-K filings, NOT in XBRL. Plan to use web_search to find the press release.
- Trend data across multiple periods: use sec_edgar_financials for GAAP metrics (returns
  multiple periods automatically) or search for each period's press release."""

    @property
    def react_system(self) -> str:
        return f"""\
You are a financial research agent. You have a research plan and tools to find data.

Your job: find all the data needed in the plan, then state your findings clearly.
Think step by step. Use tools to find specific data. When done, summarize what you found.

TOOL SELECTION — CRITICAL:
You MUST use SEC filing tools as your PRIMARY data source. Do NOT rely solely on web search.

- GAAP financial data (revenue, COGS, inventory, assets) → sec_edgar_financials FIRST
  USE EXACT XBRL CONCEPT NAMES: InventoryNet, CostOfGoodsAndServicesSold, Revenues, etc.
- Quarterly earnings data, guidance, non-GAAP metrics → sec_edgar_filing_text with
  filing_type="8-K" for earnings press releases, or filing_type="10-Q" for the quarter
- Filing narrative (MD&A, deal terms, risk factors) → sec_edgar_filing_text
- ONLY use web_search when SEC tools don't have the data (analyst estimates, market data,
  competitor info) or to find the SPECIFIC filing period/date you need

SOURCE HIERARCHY: SEC filing > 8-K press release > institutional source > web article
Web articles (Nasdaq, Yahoo Finance) may have ERRORS. Always verify against the primary filing.

IMPORTANT:
- State exact numbers you find with their sources — DO NOT ROUND
- If a source says "$4,319 million", report "$4,319 million", NOT "$4.3 billion"
- Preserve ALL significant digits in intermediate values
- If a tool returns data, extract the specific value you need
- If you can't find something after 2 tries with different tools, say so
- Do NOT hallucinate numbers — only report what tools actually returned
- When you have all the data, summarize it clearly with the EXACT values

USE FORMAL NAMES from SEC filings, not informal names from press/web:
- Officers: use the legal name from the 10-K signature page (e.g., "Elinor Mertz" not "Ellie Mertz")
- Companies: use the registered name from the filing header
- SEC filings are the authoritative source for names, titles, and dates

BE DECISIVE — do NOT hedge when the data clearly answers the question:
- SEC filings often use different labels than the question. "Contractual obligations next 12
  months" IS "projected cash requirements for 2025." "Revenue by sales channel" IS "% of
  customers from channel partners." Report the number with its filing label.
- If a filing table contains the answer under a slightly different name, REPORT IT.
  Do not say "not disclosed" when the data is in front of you under a related label.
- CHECK THE PRE-FETCHED DATA FIRST. It was selected specifically for this question.

BEAT/MISS WITH GUIDANCE RANGES — when guidance is a range (e.g., "10.8% to 10.9%"):
- ALWAYS report beat/miss vs BOTH the low end AND high end of guidance:
  "80bps beat from low end, 70bps beat from high end"
- Calculate BOTH: (actual - low_end) AND (actual - high_end), report both in your summary.
- Also compute from component dollar amounts if available (see planner methodology).

CROSS-VALIDATION — before reporting your final numbers:
- Check that your numbers are internally consistent. If a source says "margin was 2.6%"
  and you have EBITDA of $112.8M and bookings of $4,429M, verify: 112.8/4429 = 2.55%,
  NOT 2.6%. The numbers don't match — one of them is wrong.
- If two numbers from the same source are inconsistent, search for a second source to verify.
- If you find a percentage AND the underlying values, recompute the percentage to check.

{FINANCIAL_CONCEPTS}"""

    @property
    def react_prompt_template(self) -> str:
        return """\
RESEARCH PLAN:
{plan}

RESEARCH DATE: {date}
You are researching as of this date. Only include events and data available by this date.
If web search returns events AFTER this date, IGNORE them — they haven't happened yet.
For example, if researching as of 2025-02-01 and an article says "merger closed June 2025",
that event is in the future — report the status as of your research date instead.

EXAMPLES OF SUCCESSFUL RESEARCH PATTERNS:

Example 1 — Beat/miss with guidance range:
Question: "Did TJX beat or miss Q4 FY2025 pre-tax margin guidance?"
Research flow:
- Found Q4 FY2025 actuals in earnings press release: pre-tax margin = 11.6%
- Found guidance in PRIOR quarter (Q3) press release: guided range 10.8% to 10.9%
- Calculated: beat from low end = 11.6% - 10.8% = 80 bps
- Calculated: beat from high end = 11.6% - 10.9% = 70 bps
- Cross-validated: company stated "above high-end of plan by 0.7 percentage points" ✓
- REPORTED BOTH: "80bps beat from low end, 70bps beat from high end"

Example 2 — Multi-company comparison:
Question: "Compare FY24 dividend payout ratio of KO to competitors"
Research flow:
- Identified competitors: PEP, KDP, KHC, SJM (major packaged beverage/food peers)
- For EACH company: found net income and dividends paid from XBRL
- Computed payout ratio = dividends / net income for each
- Ranked highest to lowest: KDP 0.83, KO 0.79, PEP 0.75, KHC 0.70, SJM 0.59
- Cross-validated: ratios are reasonable for mature consumer staples (typically 0.5-0.9)

Example 3 — Qualitative extraction from deep in filing:
Question: "What is Shift4's vendor concentration risk?"
Research flow:
- Searched 10-K for "concentration risk" — found in notes to financial statements
- NOTE: first hit was in forward-looking disclaimers (no data) — skipped to next occurrence
- Found actual disclosure: "merchant processing activity in North America is facilitated by one vendor"
- Extracted: vendor maintains backup systems, 180-day transition period if terminated
- REPORTED the specific filing language with citation

Find all the data points listed in the plan above.
Follow the source_strategy in the clarifications — it tells you WHICH documents to search.
When you have all the data, provide a clear summary with exact numbers and sources."""

    @property
    def answer_system(self) -> str:
        return """\
You format financial research results into precise, well-cited answers.
Respond with the answer text only — no JSON, no markdown fences.

IMPORTANT: Use FORMAL LEGAL NAMES from SEC filings, not informal/nickname versions.
If the SEC filing says "Elinor Mertz" but press calls her "Ellie Mertz", use "Elinor Mertz".
The SEC 10-K signature page has the authoritative legal name for all officers."""

    @property
    def answer_prompt_template(self) -> str:
        return """\
Format the final answer for this financial question.

QUESTION: {question}

COMPLETED RESEARCH STATE:
{state}

FORMATTING RULES:
- Lead with the specific answer (number, name, or conclusion)
- Show calculation work if applicable
- Use 2 decimal places for percentages and ratios
- MANDATORY for beat/miss with guidance ranges: ALWAYS state beat/miss vs BOTH endpoints.
  Format: "X bps beat from low end, Y bps beat from high end"
  If the research has actual and guidance range, you MUST compute and state BOTH differences.
- Specify the exact time period
- Include step-by-step reasoning and justification
- Round only the final answer, not intermediate steps

At the end, include sources:
{{
    "sources": [
        {{"url": "...", "name": "source description"}}
    ]
}}"""

    # ---- Prefetch / tools ----

    @property
    def tool_dispatch(self) -> dict[str, Callable[[dict, str], ToolResult]]:
        return {
            "XBRL": self._fetch_xbrl,
            "xbrl": self._fetch_xbrl,
            "8-K": self._fetch_8k,
            "8-k": self._fetch_8k,
            "10-K": self._fetch_10k,
            "10-k": self._fetch_10k,
            "10-Q": self._fetch_10q,
            "10-q": self._fetch_10q,
        }

    def _fetch_xbrl(self, filing: dict, default_id: str) -> list[ToolResult]:
        """Fetch XBRL concepts. Returns list because multiple concepts may be tried."""
        concepts = filing.get("concepts", [])
        filing_ticker = filing.get("ticker") or default_id
        if filing_ticker and filing_ticker != default_id:
            filing_ticker = extract_ticker(filing_ticker) or filing_ticker
        results = []
        for concept in concepts:
            output = get_company_facts(filing_ticker, concept)
            if "No XBRL data found" not in output and len(output) > 50:
                results.append(ToolResult(
                    raw=output,
                    tool_name="sec_edgar_financials",
                    input_data={"ticker": filing_ticker, "metric": concept},
                ))
                log.info(f"  Prefetched XBRL {concept} for {filing_ticker}")
                break  # Found data, don't try alternatives
            else:
                log.info(f"  XBRL {concept} not found for {filing_ticker}, trying next")
        return results

    def _fetch_8k(self, filing: dict, default_id: str) -> list[ToolResult]:
        """Fetch earnings press release."""
        period = filing.get("period", "")
        filing_ticker = filing.get("ticker") or default_id
        if filing_ticker and filing_ticker != default_id:
            filing_ticker = extract_ticker(filing_ticker) or filing_ticker
        if not period:
            return []
        output = get_earnings_press_release(filing_ticker, period)
        if "No " in output[:50] or len(output) <= 100:
            log.info(f"  8-K not found: {period} for {filing_ticker}")
            return []
        return [ToolResult(
            raw=output,
            tool_name="sec_edgar_earnings",
            input_data={"ticker": filing_ticker, "quarter": period},
        )]

    def _fetch_10k(self, filing: dict, default_id: str) -> list[ToolResult]:
        return self._fetch_filing(filing, default_id, "10-K")

    def _fetch_10q(self, filing: dict, default_id: str) -> list[ToolResult]:
        return self._fetch_filing(filing, default_id, "10-Q")

    def _fetch_filing(self, filing: dict, default_id: str, ftype: str) -> list[ToolResult]:
        """Fetch 10-K or 10-Q filing text."""
        period = filing.get("period", "")
        section = filing.get("section")
        filing_ticker = filing.get("ticker") or default_id
        if filing_ticker and filing_ticker != default_id:
            filing_ticker = extract_ticker(filing_ticker) or filing_ticker
        # Qualitative questions get more filing text
        is_qualitative = filing.get("_is_qualitative", False)
        max_chars = 50000 if is_qualitative else 15000
        output = get_filing_text(filing_ticker, ftype, section=section, period=period,
                                 max_chars=max_chars)
        if "No " in output[:50] or len(output) <= 100:
            log.info(f"  {ftype} not found: {period} {section or ''} for {filing_ticker}")
            return []
        return [ToolResult(
            raw=output,
            tool_name="sec_edgar_filing_text",
            input_data={"ticker": filing_ticker, "type": ftype, "period": period, "section": section},
        )]

    @property
    def react_tools(self) -> list[dict]:
        from domains.finance.registry import (
            SEC_EDGAR_FINANCIALS_TOOL, SEC_EDGAR_FILING_TEXT_TOOL,
            SEC_EDGAR_EARNINGS_TOOL, FMP_FINANCIALS_TOOL,
        )
        return [
            SEC_EDGAR_FINANCIALS_TOOL,
            SEC_EDGAR_FILING_TEXT_TOOL,
            SEC_EDGAR_EARNINGS_TOOL,
            FMP_FINANCIALS_TOOL,
        ]

    def execute_tool(self, name: str, input_data: dict) -> str:
        from domains.finance.registry import execute_tool
        return execute_tool(name, input_data)

    # ---- Identifier handling ----

    def extract_identifier(self, company_text: str) -> Optional[str]:
        return extract_ticker(company_text)

    # ---- Context sizing ----

    def context_size_tier(self, state: dict) -> int:
        is_qualitative = (
            not state.get("calculation_steps") and
            "lookup" in state.get("clarifications", {}).get("formula", "").lower()
        )
        has_filing_sections = any(
            f.get("type", "").upper() in ("10-K", "10-Q") and f.get("section")
            for f in state.get("filings_needed", [])
        )
        if is_qualitative:
            return 50000
        elif has_filing_sections:
            return 15000
        else:
            return 4000

    # ---- Extraction support ----

    def classify_tools(self, tool_log: list[dict]) -> dict[str, list[dict]]:
        structured = []
        prose = []
        for entry in tool_log:
            tool = entry.get("tool", "")
            if tool == "sec_edgar_financials":
                structured.append(entry)
            elif tool in ("sec_edgar_earnings", "sec_edgar_filing_text"):
                prose.append(entry)
            elif tool == "fmp_financials":
                structured.append(entry)
            # Other tools (web_search etc.) are ignored for extraction
        return {"structured": structured, "prose": prose}

    @property
    def concept_map(self) -> dict[str, list[str]]:
        return {
            "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
            "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
            "inventory": ["InventoryNet"],
            "net_income": ["NetIncomeLoss"],
            "operating_income": ["OperatingIncomeLoss"],
            "total_assets": ["Assets"],
            "total_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
            "cash_equivalent": ["CashAndCashEquivalentsAtCarryingValue"],
            "depreciation": ["DepreciationAndAmortization"],
            "interest_expense": ["InterestExpense"],
            "shares_outstanding": ["CommonStockSharesOutstanding"],
            "gross_profit": ["GrossProfit"],
            "ebitda": ["OperatingIncomeLoss"],
            "equity": ["StockholdersEquity"],
            "income_tax": ["IncomeTaxExpenseBenefit"],
            "pretax": [
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
            ],
            "receivable": ["AccountsReceivableNetCurrent"],
        }

    @property
    def keyword_map(self) -> dict[str, list[str]]:
        return {
            "gross booking": ["gross_bookings", "bookings"],
            "adjusted ebitda": ["adjusted_ebitda", "ebitda"],
            "revenue": ["revenue"],
            "net income": ["net_income"],
            "net loss": ["net_income", "net_loss"],
            "gross profit": ["gross_profit"],
            "gross margin": ["gross_margin", "margin"],
            "operating income": ["operating_income"],
            "operating margin": ["operating_margin", "margin"],
            "ebitda margin": ["ebitda_margin", "margin"],
            "net margin": ["net_margin", "margin"],
            "free cash flow": ["free_cash_flow", "fcf"],
            "total assets": ["total_assets", "assets"],
            "total debt": ["total_debt", "debt"],
            "shares outstanding": ["shares_outstanding", "shares"],
            "diluted eps": ["diluted_eps", "eps"],
            "effective tax": ["effective_tax", "tax_rate"],
            "tax rate": ["tax_rate", "effective_tax"],
            "same-store": ["same_store", "comparable"],
            "comparable store": ["same_store", "comparable"],
            "take rate": ["take_rate"],
            "cash requirement": ["cash_requirement", "cash", "material_cash"],
            "contractual obligation": ["obligation", "cash_requirement", "cash"],
            "guidance": ["guided", "guidance"],
        }

    @property
    def extraction_hints(self) -> str:
        return """\
- Use the [from: ...] source tag to match guidance vs actuals:
  - "guided" or "guidance" fields → match to values from the PRIOR quarter's press release
  - "actual" fields → match to values from the CURRENT quarter's press release"""

    @property
    def sanity_check_config(self) -> dict:
        return {
            "pct_keywords": ["margin", "rate", "percent", "pct", "ratio", "growth"],
            "small_keywords": [
                "nights", "per_night", "per_booking", "per_room", "per_user", "per_member",
                "days_", "turnover", "times", "multiple", "utilization",
            ],
            "pct_max": 1000,
            "small_max": 10000,
        }

    # ---- Pre/post extraction hooks ----

    def pre_extraction_filter(self, state: dict) -> tuple[dict, Any]:
        """Hide guidance keys from structured extraction.
        Structured extractor can't distinguish actuals vs guidance when both
        use the same financial terms."""
        guidance_keys = {}
        for k, dp in list(state.get("data_needed", {}).items()):
            if "guid" in k.lower() and isinstance(dp, dict) and dp.get("value") is None:
                guidance_keys[k] = state["data_needed"].pop(k)
        return state, guidance_keys

    def post_extraction_restore(self, state: dict, stash: Any) -> dict:
        """Restore guidance keys after structured extraction."""
        if stash:
            state["data_needed"].update(stash)
        return state

    # ---- Cross-validation ----

    def cross_validate(self, state: dict) -> dict:
        """Finance-specific sanity checks on extracted values.

        Checks:
        1. Duplicate values: if 3+ keys have the same value, likely wrong extraction
        2. Absurd ratios: if a calculation would produce >10000x or <0.0001x, inputs are wrong
        """
        data_needed = state.get("data_needed", {})
        filled = {k: dp for k, dp in data_needed.items()
                  if isinstance(dp, dict) and dp.get("value") is not None}

        if len(filled) < 2:
            return state

        # Check 1: Duplicate values
        value_counts: dict[float, list[str]] = {}
        for k, dp in filled.items():
            val = dp["value"]
            if isinstance(val, (int, float)):
                value_counts.setdefault(val, []).append(k)

        for val, keys in value_counts.items():
            if len(keys) >= 3 and val > 1000:
                log.warning(f"  Cross-validation: {len(keys)} keys have same value {val:,.0f} — clearing.")
                for k in keys:
                    data_needed[k]["value"] = None
                    data_needed[k]["source"] = None

        # Check 2: Absurd ratios in calculation steps
        calc_steps = state.get("calculation_steps", [])
        for step in calc_steps:
            formula = step.get("formula", "")
            inputs = step.get("inputs", [])
            if "/" in formula and len(inputs) == 2:
                num_dp = data_needed.get(inputs[0], {})
                den_dp = data_needed.get(inputs[1], {})
                if isinstance(num_dp, dict) and isinstance(den_dp, dict):
                    num_val = num_dp.get("value")
                    den_val = den_dp.get("value")
                    if num_val and den_val and den_val != 0:
                        ratio = abs(num_val / den_val)
                        if ratio > 10000 or ratio < 0.0001:
                            log.warning(f"  Cross-validation: absurd ratio {ratio:.2f}, clearing")
                            data_needed[inputs[0]]["value"] = None
                            data_needed[inputs[1]]["value"] = None

        return state

    # ---- Benchmark ----

    @property
    def benchmark_questions(self) -> list[BenchmarkQuestion]:
        """Load FAB benchmark. Returns empty list if dataset unavailable."""
        try:
            from datasets import load_dataset
            ds = load_dataset("vals-ai/finance_agent_benchmark", split="train")
            questions = []
            for idx, row in enumerate(ds):
                questions.append(BenchmarkQuestion(
                    id=str(idx),
                    question=row["Question"],
                    answer=row["Answer"],
                    rubric=row["Rubric"],
                    question_type=row.get("Question Type", ""),
                    as_of_date="2025-02-01",
                ))
            return questions
        except Exception:
            return []

    @property
    def benchmark_date(self) -> str:
        return "2025-02-01"
