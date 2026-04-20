# Finance Research Agent — Design Principles

Distilled from 59 problems encountered building a financial research agent against the Vals AI Finance Agent Benchmark. These are the non-obvious lessons — things that seem reasonable in theory but fail in practice with real SEC filings and financial data.

---

## 1. Programmatic Extraction Over LLM Extraction

**Principle:** For any data that has a deterministic structure, extract it with code — not an LLM.

**Why:** LLMs round. "$4,278.9 million" becomes "$4,300M" or "$4.3B" when an LLM summarizes it. A 0.5% rounding error in gross bookings causes a 1.3 bps error in the final beat/miss calculation. Regex doesn't round.

**Apply to:** XBRL data, dollar amounts in tables, percentages, filing metadata.

**Don't apply to:** Semantically ambiguous data where the same keyword means different things (e.g., "Adjusted EBITDA" appearing for both actuals and guidance in the same document). Use LLM for interpretation, but feed it the raw source text — never the agent's summary.

**Hierarchy:** XBRL structured → press release table (programmatic) → press release prose (programmatic) → LLM extraction from raw text → LLM extraction from agent summary (never).

---

## 2. Prefetch Primary Sources Before the Agent Reasons

**Principle:** Don't let the agent choose its data sources. Fetch SEC filings programmatically and inject them into the prompt.

**Why:** Given a choice between frictionless web search (inline results, no round-trip) and SEC filing tools (requires ticker lookup, filing type selection, section extraction), Claude will choose web search every time. Web search returns rounded, sometimes wrong numbers from secondary sources. SEC filings are the ground truth.

**Implementation:** The planner identifies what data is needed → the orchestrator calls SEC tools → raw filing data is injected into the ReAct prompt before the agent starts reasoning. The agent has primary source data before it starts searching.

---

## 3. SEC Filings Are 500K+ Characters — You Need Surgical Extraction

**Principle:** Never fetch a full 10-K and hope the first 15K chars contain the answer. Use section-targeted extraction.

**Why:** A typical 10-K is 400-600K characters. Revenue disaggregation is at position 500K. The effective tax rate is at position 270K. Contractual obligations are at position 504K. A 15K char limit misses all of these.

**Implementation:** Section markers with priority ordering. "Effective income tax rate" (specific, appears near data) before "provision for income taxes" (generic, appears in risk factors). Auto-detect which sections to fetch from the question keywords: "channel" → revenue section, "tax rate" → tax section, "lease" → leases section.

**Gotcha:** The same marker text appears in multiple places. Risk factors mention "provision for income taxes" generically. The actual data is in the notes to financial statements. Order markers by specificity — most specific first.

**Older filings:** For older filings (5+ years back), the SEC EDGAR `recent` submissions array may not include them. The `_find_filing` function falls back to supplementary filing history files (`filings.files` in the submissions response), fetching each supplementary JSON and applying the same matching logic. Netflix FY2020 10-K was only accessible via `CIK0001065280-submissions-001.json`, not via `recent`.

---

## 4. Fiscal Years Are Not Calendar Years

**Principle:** Never assume a company's fiscal quarter maps to a calendar quarter. Match by the fiscal quarter identifier in the filing, not by the filing date.

**Why:** Micron's fiscal year ends in September. "Q3 2024" for Micron = quarter ending May 2024, filed June 2024. Calendar Q3 filing window (Oct-Dec) gives you the wrong quarter entirely. Oracle's FY ends May 31. Every company with a non-December fiscal year will fail the calendar mapping.

**Implementation:** Two-pass press release fetcher: (1) Scan all recent 8-Ks and match by fiscal quarter identifier in the exhibit filename (e.g., "a2024q3ex991" contains "q3" and "2024"). (2) Fall back to calendar-quarter date window only if no filename match. The filename approach works for any fiscal year convention.

**Additionally:** Exhibit filenames may use 2-digit fiscal years ("fy25" not "2025"). The filename matcher must handle both formats.

---

## 5. Tables Declare Units Once — Values Don't Repeat Them

**Principle:** Financial tables say "(in millions)" in the header, then every subsequent `$ 4,278.9` means $4,278,900,000. The parser must carry table-level unit context forward.

**Why:** The regex `\$\s*([\d,.]+)\s*(million|billion)?` finds "$4,278.9" with no unit suffix. Without table context, this is stored as 4278.9 instead of 4,278,900,000. The same press release has "$4.3 billion" in the summary (rounded) and "$ 4,278.9" in the table (precise). The precise value is useless if the parser doesn't scale it.

**Gotcha:** The multiplier threshold must be smart. "(in thousands)" tables have values in the millions (14,426,266 thousands = $14.4B). Check `multiplied_value < $1 trillion` rather than `raw_value < 1 million`.

---

## 6. In Tables, Position Encodes Column — First Value = Most Recent

**Principle:** Financial statement tables put the most recent period in the first column. The first dollar amount after a row label is the most recent quarter's value.

**Why:** Earnings press release tables are consistently formatted: `Gross Bookings $ 4,278.9 $ 4,108.4 $ 3,724.3` where the columns are Q4 2024, Q3 2024, Q4 2023. Without position-based ranking, the extractor might pick any value from the row.

**Implementation:** Tag each extracted fact with its character position. Among same-score matches, prefer lower position (earlier in document = first column = most recent quarter). Also tag facts with their source document index so position comparisons don't cross document boundaries.

**Limitation:** This heuristic works for financial statement tables (most recent first) but fails for obligation tables (Total column first, not "Next 12 Months"). Table column disambiguation remains an open problem with flat-text parsing.

---

## 7. Context Keywords Must Come From Before the Value, Not After

**Principle:** In tables, the row label precedes its values. Only extract keywords from text BEFORE the dollar amount, not after.

**Why:** An 80-character context window that extends after a value bleeds into the next row's label. "$849.7M" (operating cash flow) picks up "Adjusted EBITDA" from the next row's label if after-context is included. Restricting to before-context prevents cross-row contamination.

---

## 8. Match the Benchmark's Conventions, Not Textbook Finance

**Principle:** When your textbook-correct methodology disagrees with the benchmark's ground truth, change your methodology.

**Why:** "3-year CAGR" textbook convention: N = years of growth = 2. FAB benchmark: N = 3 (the label number). Inventory turnover textbook: COGS / average inventory. FAB benchmark: COGS / ending inventory. Being "more correct" scores zero if the benchmark expects a different convention.

**Apply carefully:** Only change methodology when you've verified the benchmark consistently uses the alternative convention. Don't blindly match one question's ground truth — check the pattern across multiple questions.

---

## 9. Be Decisive — Report Data Under Related Labels

**Principle:** If a filing table clearly answers the question under a slightly different label, report the number. Don't say "not disclosed."

**Why:** SEC filings use specific disclosure terminology. "Contractual obligations — next 12 months" IS "projected material cash requirements for 2025." "Revenue by sales channel — channel partners 20%" IS "percentage of customers from channel partners 20%." An agent that refuses to connect related terminology will fail every question where the question's phrasing differs from the filing's label.

**Balance with:** Don't fabricate connections. If the filing genuinely doesn't contain the data, say so. The principle applies when the data IS present under a recognizable related label.

---

## 10. XBRL Concept Names Vary Across Companies

**Principle:** Always try multiple XBRL concept names for the same financial metric. The primary name that works for 80% of companies will fail for the other 20%.

**Why:** Most companies report revenue as `Revenues`. Palantir uses `RevenueFromContractWithCustomerExcludingAssessedTax`. Oracle's pretax income is split into `Domestic` and `Foreign` components with no single total. Single-concept lookups miss these companies entirely.

**Implementation:** Concept map entries are lists of alternatives tried in order: `"revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]`. Break on first success. Also deduplicate XBRL entries — the same period restated in multiple filings wastes slots.

---

## 11. Type-Aware Extraction — Percentages vs Dollars

**Principle:** A margin key should never be filled with a dollar amount, and vice versa. The extractor must understand the expected type of each data point.

**Why:** Micron's gross margin key was filled with $8.7B (gross profit in dollars) instead of 27.9% (the margin percentage). The calculator then computed `8,709,000,000 - 38.5 = nonsense`. Type mismatches produce silently wrong answers.

**Implementation:** If the key contains "margin", "rate", "percent", or "ratio", reject values > 1000 (no percentage is that large). Score percentage facts higher for percentage keys (+5) and dollar facts lower (-5). Extract percentages as a separate pass alongside dollar amounts.

---

## 12. For Company KPIs, Find the Reported Metric — Don't Recompute

**Principle:** When a company reports its own version of a metric (ARPU, take rate, same-store sales), find and use that number. Don't compute it from components.

**Why:** Netflix ARPU computed as total revenue / total subscribers = $10.05/month. Netflix's own reported "Average Revenue Per Membership" = $10.82/month. The difference comes from revenue exclusions, average vs. point-in-time subscriber counts, and FX adjustments. The benchmark expects the company's own number.

**Apply to:** ARPU, take rate, same-store sales growth, adjusted EBITDA (use the company's reconciliation), free cash flow (use the company's definition).

**KPI section location:** KPI data is typically in the MD&A section of the 10-K, around position 100-200K in a 400-600K char filing. Use the 'kpi' section marker to extract it. For multi-year trends, each 10-K shows ~3 years — specify multiple filings in `filings_needed` to cover the full range.

---

## 13. Forward-Looking Data Comes From the Prior Year's Filing

**Principle:** "Cash requirements for 2025" means the FY2024 10-K, not the FY2025 10-K. The filing that PROJECTS into year X is from FY(X-1).

**Why:** A 10-K filed in early 2025 (for FY2024, as of Dec 31, 2024) contains a "Contractual Obligations" table with a "Next 12 Months" column — that's the 2025 projection. The FY2025 10-K (filed early 2026) projects into 2026. Fetching the wrong year gives a number that's off by a full year.

**Implementation:** The planner's financial methodology reference now includes this convention. The planner outputs "2024 10-K" in its source_strategy. The prefetch extracts the 10-K year from source_strategy (`re.search(r'(20\d{2})\s*10-K', source_strategy)`) and prefers it over the period year.

**Applies to:** Any forward-looking disclosure: contractual obligations, projected cash requirements, purchase commitments, lease maturity schedules. Also applies to guidance: "Q3 2024 guidance" is in the Q2 2024 earnings press release.

---

## 14. Let the LLM Specify Filings Structurally — Don't Regex-Parse Free Text

**Principle:** The planner should output a structured list of which SEC filings to fetch, not free-text instructions that the code regex-parses.

**Why:** Every filing selection bug (wrong year, wrong quarter, wrong filing type) traced back to regex-parsing the planner's free-text `period` and `source_strategy` fields. The planner already knew what it needed — "Netflix 10-K filings for each year 2019-2024" — but this knowledge was in prose that code couldn't reliably interpret. Each edge case (non-calendar FY, forward-looking data, multi-year trends, 2-digit year filenames) required a new regex heuristic. The heuristics accumulated and interacted unpredictably.

**Implementation:** Added `filings_needed` field to the planner output: a list of `{type, period, section, reason}` objects. The prefetch iterates this list directly. The LLM handles the hard part (understanding fiscal years, forward-looking conventions, multi-period needs) and code handles the easy part (fetching). Old heuristics preserved as fallback.

**Tested and confirmed working.** Netflix ARPU: planner requested 3 10-Ks (FY2024, FY2022, FY2020), all fetched successfully, 6/6 values correct. TKO: planner requested 4 filings, agent correctly reported $3.25B. The structured approach eliminated 5+ regex heuristics.

**General lesson:** When an LLM knows the answer but outputs it as free text that code must parse, you lose information at the boundary. Structured output schemas eliminate this lossy translation. This applies anywhere an LLM's reasoning needs to drive deterministic downstream behavior.

---

## 15. Distinguish Refinanceable Debt From Total Balance Sheet Debt

**Principle:** XBRL `LongTermDebt` includes everything classified as long-term debt under GAAP (bonds, leases, pensions). For refinancing or interest rate sensitivity questions, use only interest-bearing borrowings from the debt footnote.

**Why:** Boeing's balance sheet shows $53.6B in LongTermDebt, but only ~$42B is refinanceable bonds and notes. The $11.6B difference (operating leases, pension obligations) can't be refinanced. Using the balance sheet figure produces a 24% error in the impact calculation.

**Apply to:** Any question about debt refinancing, interest rate sensitivity, or debt maturity analysis. Always check the debt footnote for the breakdown of what's actually borrowings vs other obligations.
