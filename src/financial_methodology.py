"""Financial methodology reference — HOW to correctly apply financial analysis.

This isn't about formulas (those are in financial_concepts.py).
This is about methodology: the correct procedure for each type of analysis,
common pitfalls, and how professional analysts approach these problems.

Populated from lessons learned against the Vals AI Finance Agent Benchmark
and standard sell-side/buy-side analytical practices.
"""

FINANCIAL_METHODOLOGY = """
FINANCIAL METHODOLOGY REFERENCE — correct analytical procedures by question type.

========================================
BEAT/MISS ANALYSIS
========================================

METHODOLOGY:
1. Find the company's GUIDANCE from the PRIOR quarter's earnings release.
   - Guidance is typically expressed as dollar ranges (e.g., "Revenue of $1.5B to $1.6B")
   - Some companies also state derived metrics (e.g., "Adjusted EBITDA margin of 2.3% to 2.4%")

2. Find the ACTUAL results from the CURRENT quarter's earnings release.

3. CRITICAL: When comparing margins, compute the guided margin from the guided
   component dollar amounts — do NOT use a stated margin range directly.

   WRONG: Guided margin = midpoint of stated range (2.3% + 2.4%) / 2 = 2.35%
   RIGHT: Guided margin = guided EBITDA midpoint / guided bookings midpoint
          = $102.5M / $4,315M = 2.3754%

   Why: Stated margin ranges are rounded approximations. The precise implied margin
   from dollar guidance may differ by several basis points.

4. Express the difference:
   - For margins: (actual - guided) × 10,000 = basis points
   - For dollar amounts: (actual - guided) / guided × 100 = percentage beat/miss
   - For EPS: (actual - guided) in dollars and as percentage

5. Report vs BOTH endpoints AND midpoint when guidance is a range:
   - "Beat low end by 80bps, beat high end by 70bps, beat midpoint by 75bps"
   - If question asks "at midpoint", report midpoint only

6. SOURCE PRIORITY for guidance:
   - Best: Prior quarter earnings press release (8-K Exhibit 99.1)
   - Good: Earnings call transcript
   - Avoid: Analyst estimates (these are street consensus, not management guidance)

========================================
GAAP vs NON-GAAP ADJUSTMENTS
========================================

METHODOLOGY:
1. Start from the GAAP figure (net income, operating income, etc.)
2. Find the company's NON-GAAP RECONCILIATION TABLE in:
   - Earnings press release (most common location)
   - 10-K/10-Q footnotes
3. Identify each adjustment line item and its dollar amount
4. Common adjustments (in order of typical magnitude):
   - Stock-based compensation (SBC) — almost always added back
   - Depreciation & amortization — for EBITDA
   - Restructuring / reorganization charges
   - Acquisition-related costs (integration, transaction fees)
   - Litigation settlements
   - Impairment charges
   - Tax effects of adjustments
5. CRITICAL: Each company defines "Adjusted" metrics differently.
   Always use THE COMPANY'S OWN reconciliation, not a generic formula.
6. Verify: GAAP figure + sum of adjustments = Non-GAAP figure

========================================
GROWTH RATE / CAGR CALCULATIONS
========================================

METHODOLOGY:
1. PERIOD SELECTION:
   - "3-year CAGR" = most recent 3 completed fiscal years
   - For calendar year companies (Dec FY): 3-year CAGR as of 2024 = FY2022 to FY2024
   - For non-standard FY: use the most recent 3 fiscal year-ends
   - CRITICAL: Use ANNUAL figures, not trailing-twelve-month

2. FORMULA: CAGR = (End Value / Start Value) ^ (1/N) - 1
   where N = the number of years in the label (e.g., "3-year CAGR" → N = 3)

3. CRITICAL — N equals the number in the label:
   - "3-year CAGR" from 2022 to 2024 → N = 3
   - "5-year CAGR" from 2019 to 2024 → N = 5
   - DO NOT subtract 1 from N. The label IS the exponent denominator.

4. SOURCE: Use revenue from 10-K filings or XBRL (most reliable)

========================================
RATIO CALCULATIONS (TURNOVER, MARGINS, LEVERAGE)
========================================

METHODOLOGY:
1. TURNOVER RATIOS use END-OF-PERIOD balances:
   - Inventory Turnover = COGS / Ending Inventory
   - Use the ending inventory for the SAME fiscal year as COGS
   - Do NOT average beginning and ending inventory

2. MARGIN RATIOS use same-period figures:
   - Gross Margin = Gross Profit / Revenue (both from same period)
   - Operating Margin = Operating Income / Revenue

3. LEVERAGE RATIOS — check which debt figure:
   - Debt/Equity: Total Debt / Total Stockholders' Equity
   - Net Debt = Total Debt - Cash (may include short-term investments)
   - Debt/EBITDA: typically uses LTM (last twelve months) EBITDA

4. DEBT TYPES — CRITICAL for refinancing/interest rate questions:
   - XBRL "LongTermDebt" includes EVERYTHING classified as long-term debt: bonds, notes,
     operating lease liabilities, finance lease obligations, pension obligations, etc.
   - REFINANCEABLE DEBT = only interest-bearing borrowings: bonds payable, notes payable,
     term loans, credit facility draws. Found in the "Debt" or "Borrowings" footnote.
   - Operating leases, deferred revenue, and pension obligations are NOT refinanceable
     even though they appear in total debt on the balance sheet.
   - For "what if all debt were refinanced" questions: use the DEBT FOOTNOTE total of
     bonds/notes outstanding, NOT the XBRL LongTermDebt balance sheet figure.
   - The debt footnote typically has a table listing each bond/note with face value,
     rate, and maturity. Sum these for refinanceable debt.

5. COMMON PITFALL: Mixing periods
   - Balance sheet items are point-in-time (as of Dec 31)
   - Income statement items are over a period (Jan 1 - Dec 31)
   - For ratios mixing both, use average balance sheet figures

========================================
COMPANY-REPORTED KPIs
========================================

METHODOLOGY:
1. ALWAYS search for the company's OWN reported version of a metric before computing it.
   - ARPU → search for "average revenue per member" or "average revenue per user" in the 10-K
   - Take rate → search for the company's own calculation in earnings materials
   - Same-store sales → always use the company's reported figure (their comp base may differ)
   - Adjusted EBITDA → use the company's reconciliation table, not a generic formula

2. WHY: Company-reported KPIs differ from naive computation because of:
   - Revenue exclusions (not all revenue goes into the KPI denominator)
   - Average vs point-in-time denominators (avg monthly members vs year-end count)
   - FX adjustments, one-time items, segment-specific definitions
   - Example: Netflix ARPU = "average monthly revenue per paying membership" ≠ total revenue / subscribers

3. WHERE TO FIND: Company KPIs are typically in the MD&A section of the 10-K, or in
   the operational highlights of the earnings press release. Each 10-K shows 3 years of data.
   For longer trends (5+ years), you may need multiple 10-Ks.

========================================
TREND ANALYSIS (MULTI-PERIOD)
========================================

METHODOLOGY:
1. Collect the SAME metric for EVERY period requested
2. Use CONSISTENT sources — don't mix 10-K data for one year with web data for another
3. For YoY comparisons: (Current Period / Prior Period) - 1
4. For multi-period trends: show the actual values AND the growth rates
5. COMMON PITFALL: Missing restated figures
   - When a company restates prior periods, the 10-K shows restated figures
   - Use the MOST RECENT filing's presentation of historical data for consistency

========================================
FINANCIAL MODELING / PROJECTIONS
========================================

METHODOLOGY:
1. State ALL assumptions explicitly
2. Show the calculation step by step
3. For "what if" scenarios: change ONE variable at a time
4. For seasonality analysis:
   - Calculate the seasonal pattern from 3+ years of history
   - Apply the average/median pattern to project
5. COMMON PITFALL: Applying annual figures to quarterly questions
   - If asked about Q2, don't use full-year figures
   - Quarterly seasonality varies significantly in most industries

========================================
CROSS-COMPANY COMPARISON
========================================

METHODOLOGY:
1. Use the SAME metric definition for all companies
2. Use the SAME time period for all companies
3. Account for different fiscal year ends (Apple=Sep, Microsoft=Jun, etc.)
4. When comparing non-GAAP metrics, note that definitions differ across companies
5. Present in a structured table format for clarity

========================================
GENERAL PRINCIPLES
========================================

- PRECISION: Do not round intermediate calculations. Round only the final answer.
  Report to 2 decimal places for percentages, whole numbers for dollar amounts.

- UNITS: State units explicitly. "$14,060 million" not "$14,060".
  Be consistent — don't mix millions and billions in the same calculation.

- SOURCES: Every number must come from a source. Prefer:
  1. SEC filings (10-K, 10-Q, 8-K press releases)
  2. Company investor relations
  3. Institutional data providers
  Never: blog posts, AI-generated summaries, or social media

- FORWARD-LOOKING DATA (cash requirements, projected obligations, guidance):
  The source is the most recent filing BEFORE the target year, not the filing FOR that year.
  "Cash requirements for 2025" → FY2024 10-K (filed early 2025, as of Dec 31, 2024).
  The "Next 12 Months" column in the contractual obligations table projects into 2025.
  Do NOT use the FY2025 10-K — its "Next 12 Months" projects into 2026.
  Similarly: "Q3 2024 guidance" → Q2 2024 earnings press release (guidance given for next quarter).

- CROSS-VALIDATION: After computing a result, sanity check it:
  - Is the magnitude reasonable? (Inventory turnover of 600x is clearly wrong)
  - Is it consistent with the source? (If the source says "margin was 2.6%"
    and your calculation gives 2.5%, investigate the discrepancy)
  - Does the direction make sense? (Revenue grew but margins declined = plausible)
"""
