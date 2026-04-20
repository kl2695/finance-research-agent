"""Financial concepts reference — embedded in the planner prompt.

Provides formulas and definitions so the planner creates correct calculation plans.
"""

FINANCIAL_CONCEPTS = """
FINANCIAL CONCEPTS REFERENCE — use these to create correct calculation plans.

PROFITABILITY:
- Gross Margin = Gross Profit / Revenue (Gross Profit = Revenue - COGS)
- Operating Margin = Operating Income / Revenue
- EBITDA Margin = EBITDA / Revenue (EBITDA = Operating Income + D&A)
- Net Margin = Net Income / Revenue
- Adjusted EBITDA: start from GAAP operating income, add back D&A, SBC, restructuring, one-time items

EFFICIENCY:
- Inventory Turnover = COGS / Ending Inventory (use end-of-year inventory, NOT average)
- Receivables Turnover = Revenue / Accounts Receivable
- Asset Turnover = Revenue / Total Assets
- Days Sales Outstanding (DSO) = (Accounts Receivable / Revenue) * 365

LEVERAGE:
- Debt/Equity = Total Debt / Total Stockholders' Equity
- Net Debt = Total Debt - Cash and Cash Equivalents
- Debt/EBITDA = Net Debt / EBITDA (or Total Debt / EBITDA)
- Interest Coverage = EBITDA / Interest Expense

GROWTH:
- Revenue CAGR = (Revenue_end / Revenue_start) ^ (1 / N) - 1
  where N = the number in the label ("3-year CAGR" → N = 3, NOT 2)
  "3-year CAGR" = most recent 3 fiscal years unless otherwise specified
- YoY Growth = (Current Period - Prior Period) / Prior Period
- Same-Store Sales Growth: growth at locations open for comparable periods (usually 12+ months)
- EPS Growth = (EPS_current - EPS_prior) / abs(EPS_prior)

VALUATION:
- P/E = Stock Price / EPS (or Market Cap / Net Income)
- EV/EBITDA = Enterprise Value / EBITDA
- EV/Revenue = Enterprise Value / Revenue
- Enterprise Value = Market Cap + Net Debt + Preferred Stock + Minority Interest
- PEG Ratio = P/E / EPS Growth Rate

M&A:
- M&A Firepower = Cash + Undrawn Revolver + (EBITDA * Target Leverage) - Net Debt
- Accretion/Dilution: compare combined EPS to standalone buyer EPS

GAAP vs NON-GAAP:
- Common Non-GAAP adjustments: stock-based compensation (SBC), amortization of intangibles,
  restructuring charges, acquisition-related costs, litigation settlements, impairments
- Adjusted EBITDA typically = GAAP Net Income + Interest + Taxes + D&A + SBC + one-time items
- When question asks for "adjusted" metric, look for the company's Non-GAAP reconciliation table

GUIDANCE COMPARISON (BEAT/MISS):
- Beat = Actual > Guidance; Miss = Actual < Guidance
- For ranges: report beat/miss vs midpoint, AND vs low/high end separately
- Express margin differences in basis points (bps): 1 bps = 0.01 percentage points
- Beat/miss calculation: (Actual - Guidance) in same units
  For margins: (Actual Margin - Guided Margin) * 10000 = bps difference

FISCAL YEAR NOTES:
- Most companies: FY ends Dec 31 (FY2024 = Jan-Dec 2024)
- Exceptions: Apple (Sep), Microsoft (Jun), Walmart (Jan), Nike (May)
- "Q4 FY2025" may not be calendar Q4 — check the company's fiscal year end
- When in doubt, use the 10-K/10-Q filing date to determine the period

TAKE RATE / MARKETPLACE:
- Take Rate = Revenue / Gross Booking Value (or Gross Merchandise Value)
- Revenue growth decomposition: Volume Growth + Take Rate Change
  (if take rate is flat, all growth is from volume)
"""
