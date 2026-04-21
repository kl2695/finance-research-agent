# Finance Research Agent — Technical Architecture

## System Overview

A financial research agent that answers quantitative questions about public companies using SEC filings as the primary data source. Optimized for the Vals AI Finance Agent Benchmark (FAB) — 50 public questions, 337 private test set. Current accuracy: **78% (39/50)** on the FAB public validation set, 14 points above the leaderboard leader (Claude Opus 4.7 at 64%).

Key constraint: precision. Financial questions require exact numbers (e.g., 26.1 basis points, not "about 26"). Every component is designed to minimize rounding, hallucination, and data loss.

## Pipeline Flow

```
Question
    │
    ▼
┌─────────────────┐
│  1. PLANNER      │  Sonnet — creates:
│                  │    • data_needed keys (what values to find)
│                  │    • filings_needed (which SEC filings to fetch)
│                  │    • calculation_steps (formulas to compute)
│                  │    • clarifications (period, company, methodology)
│                  │  Cached by question+date hash for reproducibility.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  2a. PREFETCH    │  Programmatic — iterates filings_needed list:
│                  │    • "xbrl" → get_company_facts(concepts)
│                  │    • "8-K"  → get_earnings_press_release(quarter)
│                  │    • "10-K" → get_filing_text(section, period)
│                  │  Results injected into ReAct prompt.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  2b. ReAct LOOP  │  Sonnet — reasons with tools. Prefetched data in prompt.
│                  │  Agent may call additional tools (web search, filing text).
│                  │  Produces research narrative.
│                  │  3 few-shot examples guide research patterns.
│                  │  max_turns: 15 for complex (5+ filings), 10 default.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  3. EXTRACTION   │  4-step hybrid (each fills unfilled keys, never overwrites):
│                  │    3a. XBRL exact concept matching (precise, no LLM)
│                  │    3b. Structured text matching (regex + keyword scoring)
│                  │    3c. LLM fact matching (Haiku assigns parsed values to keys)
│                  │    3d. LLM raw extraction (Sonnet reads source text directly)
│                  │  + 3.5: Cross-validation (catches duplicates & absurd ratios)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  4. CALCULATOR   │  Python eval — deterministic arithmetic from state dict.
│                  │  Formulas defined by planner, executed as code.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  5. FORMATTER    │  Sonnet — formats answer from state + research narrative.
│                  │  Falls back to narrative if structured pipeline fails.
└─────────────────┘
```

## Planner Caching

The planner (Step 1) is non-deterministic — different runs produce different key names, filings_needed lists, and formulas. This causes score variance even when the underlying extraction and calculation logic hasn't changed.

**Solution:** Plans are cached by `MD5(question + date)` in `results/planner_cache.json`. On cache hit, the plan is reused with all values reset to `null` for fresh extraction. This eliminates planner non-determinism between runs.

**Cache clearing:** Delete the cache file to force re-planning (needed after prompt changes or methodology updates).

**Entry point:** `agent.py:_plan(question, as_of_date, use_cache=True)`

## ReAct Agent Details (Step 2b)

### Few-Shot Examples

The ReAct prompt includes 3 worked research patterns to guide the agent's tool usage and reasoning:

1. **Beat/miss with guidance range** — TJX pre-tax margin: finds actuals in current 8-K, guidance in prior quarter's 8-K, computes beat from BOTH low and high end, cross-validates against company's own statement.
2. **Multi-company comparison** — KO dividend payout ratio vs peers: identifies competitors, fetches data for each via XBRL, computes ratios, ranks.
3. **Qualitative deep-section extraction** — Shift4 vendor concentration risk: navigates past ToC/disclaimer matches to find the actual disclosure in notes to financial statements.

### Context Size Tiers

Prefetched filing data is injected into the ReAct prompt with size limits based on question type:

| Question Type | Limit per Source | Rationale |
|---------------|-----------------|-----------|
| Qualitative (no calculation, "lookup only") | 50K chars | Need full sections for narrative answers |
| Quantitative with filing sections (10-K/10-Q with section) | 15K chars | Need tables from targeted sections |
| Simple XBRL calculation | 4K chars | Just need confirmation data |

### Max Turns Scaling

Complex questions with 5+ filings_needed entries get 15 turns (vs 10 default). This accommodates multi-company comparisons and questions requiring data from many filings. Motivated by FAB paper insight: "top performers register high numbers of tool calls."

## Data Extraction Flow (Step 3 — the critical path)

This is the most complex part of the system. Four extraction methods run in sequence, each filling unfilled data_needed keys. A key filled by an earlier method is never overwritten.

### Step 3a: XBRL Structured Extraction

**Input:** Tool log entries from `sec_edgar_financials` (XBRL API responses).
**Method:** Regex parsing of XBRL output format: `ConceptName (USD): value (period: start to end, filed: date)`.
**Matching:** Concept map matches key name keywords to XBRL concept names. E.g., "revenue" → `["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]`.

**Strengths:**
- Exact values — no rounding, no interpretation
- Period-aware — strict year filtering, "beginning FY2024" maps to 2023 ending balance
- Deduplication — same period filed in multiple years doesn't waste slots

**Failure modes:**
- Company uses unexpected concept name (P33 — Palantir uses different revenue concept)
- Keyword substring matching too loose (P42 — "tax" in "pretax" matches wrong concept)
- XBRL doesn't have the data (non-GAAP metrics, disclosure tables)

**Entry point:** `extractor.py:extract_from_tool_log()` → `_parse_xbrl_output()` → `_match_fact_to_key()`

### Step 3b: Filing Text Structured Extraction

**Input:** Tool log entries from `sec_edgar_earnings` and `sec_edgar_filing_text`.
**Method:** Two regex passes:
1. Dollar amounts: `\$\s*([\d,.]+)\s*(million|billion)?` — with table unit context ("(in millions)" header applies multiplier to subsequent bare values)
2. Percentages: `([\d,.]+)\s*%` — new, for margin/rate/growth questions

**Table parsing:** HTML tables are converted to column-annotated text before regex extraction. E.g., `Total [Next 12 Months]: 14,426,266 [Beyond 12 Months]: 31,425,247`. This preserves column context that flat-text extraction loses.

**Matching — how parsed values get assigned to data_needed keys:**

After regex parsing finds all dollar amounts and percentages, each value needs to be matched to the right `data_needed` key (e.g., "this $4,278.9M is `q4_2024_gross_bookings`"). This is done by scoring each value against each unfilled key:

1. **Context keyword extraction:** For each parsed value, extract financial keywords from the 80 characters BEFORE it in the text (before-only prevents cross-row contamination in tables). The `_CONTEXT_KEYWORD_MAP` maps phrases found in filing text to standardized key terms:
   ```
   "gross booking" in text  →  ["gross_bookings", "bookings"]
   "adjusted ebitda" in text →  ["adjusted_ebitda", "ebitda"]
   "gross margin" in text    →  ["gross_margin", "margin"]
   ```
   This map covers ~20 common financial phrases. Novel phrases not in the map fall through to Step 3c (LLM matching).

2. **Scoring:** Each value is scored against each key by summing:
   - **Compound keyword match** (keyword found in key name, e.g., "gross_margin" in "q4_2024_gross_margin"): 6 + keyword_length points. Longer matches score higher — "gross_margin" (12 chars) beats "margin" (6 chars).
   - **Partial keyword match** (single key part found in keyword, e.g., "gross" in "gross_bookings"): 4 points.
   - **XBRL concept match** (for XBRL data): exact concept name = 10 points, substring = 5 points.
   - **Type matching:** percentage keys (containing "margin", "rate", "ratio") boost percentage facts (+5) and penalize dollar facts (-5).
   - **Table bonus:** +4 for values from structured HTML tables (more precise than prose).
   - **Period match:** strict year filtering for XBRL data. "Beginning FY2024" maps to year 2023.
   - **Quarterly context:** boost if context mentions "fourth quarter"/"Q4" for Q4 keys; penalize "full-year" values.

3. **Tiebreaking:** When multiple values have the same score:
   - **Source ordering:** earlier tool_log entries preferred (Q4 press release prefetched before Q3).
   - **Position:** first value after a row label = most recent quarter (financial table convention).

4. **Unit sanity check:** After matching, reject values > 1000 for percentage keys (margin, rate, ratio).

The top-scoring value for each key is selected. If no value scores above 0 for a key, it remains unfilled for Step 3c.

**Guidance key protection:** Keys containing "guid" are temporarily hidden during this step. They're restored for LLM extraction (Steps 3c and 3d) because structured extraction can't distinguish actuals from guidance when both use the same financial terms.

**Strengths:**
- Precise dollar amounts from financial tables ($4,278.9M not $4.3B)
- No LLM cost — pure regex
- Column-aware via table parser

**Failure modes:**
- Multi-column table values: can distinguish columns for simple tables but complex multi-level headers may not parse correctly (P59)
- "cash" keyword too broad — matches both "cash equivalents" (asset) and "cash requirements" (obligation)
- Period selection for non-calendar fiscal years (P45 — Micron's FY ends September)

**Entry point:** `extractor.py:extract_from_tool_log()` → `_parse_filing_text()` → `_match_fact_to_key()`

### Step 3c: LLM Fact Matching (scalable — handles novel metrics)

**When it runs:** Only for data_needed keys that Step 3b couldn't fill — either because the metric isn't in the 20-phrase keyword map, or because no value scored above 0 for that key.

**Input:**
- All parsed facts from Step 3b (dollar amounts + percentages with their context)
- All unfilled data_needed keys with their labels and units

**Method:** Builds a prompt for Haiku with:

1. **Fact summaries** — each parsed value formatted as:
   ```
   [18] $4,278,900,000.00 [TABLE] [from: 8-K Q4 2024] — Gross Bookings [Year E...
   [54] $100,000,000.00 [from: sec_edgar_earnings Q3 2024] — $4.28 billion to $4.35 billion...
   ```
   Each fact includes: index, value, [TABLE] flag if from structured table, [from: source document], surrounding context.

2. **Key descriptions** — each unfilled key with its human-readable label:
   ```json
   {"q4_2024_gross_bookings": "Q4 2024 Gross Bookings (USD millions)"}
   ```

3. **Matching rules in the prompt:**
   - Metric name in context must match the field AND period must match
   - Prefer [TABLE] values over prose (more precise)
   - Use [from: ...] source tags to distinguish guidance (prior quarter) from actuals (current quarter)
   - Percentage fields should match percentage values, not dollar amounts

**Output:** JSON mapping keys to fact indices: `{"q4_2024_gross_bookings": 18}`

**Pre-processing before the LLM call:**
- **Deduplication:** Removes exact duplicate facts (same value + same context from duplicate prefetch paths)
- **Imprecise value filtering:** When a TABLE value and a prose approximation exist for similar amounts (within 5%), removes the less precise prose value. E.g., "$4.3 billion" (prose) removed when "$4,278.9M" (table) exists.
- **Proportional source sampling:** Selects up to 80 facts distributed across all source documents (not just first 60), so every prefetched filing is represented.

**Cost:** One Haiku API call per question (~$0.004, ~1-2 seconds).

**Strengths:**
- Scales to ANY financial metric without code changes (no hardcoded keyword map needed)
- Understands semantic context ("Adjusted EBITDA" in Q3 outlook section = guidance, not actuals)
- Source document labels help distinguish which press release a value came from

**Failure modes:**
- Haiku may confuse similar values from different periods (e.g., Q4 2024 EBITDA vs Q4 2023 EBITDA when both are in the same press release)
- Less precise than Step 3b for known metrics — doesn't use position tiebreaker or period-strict filtering
- With many facts (80+), the prompt gets long and Haiku may make errors
- Guidance vs actuals distinction relies on source labels being correct

**Entry point:** `extractor.py:llm_match_facts_to_keys()`

### Step 3d: LLM Raw Extraction Fallback

**When it runs:** Only for data_needed keys that Steps 3a-3c all failed to fill. This is the last resort before the narrative fallback.

**How it differs from Step 3c:** Step 3c assigns pre-parsed values (regex already found the numbers) to keys. Step 3d reads RAW filing text and extracts values directly — it handles cases where:
- The value isn't in a standard `$X.XX million` or `X.X%` format that regex would catch
- The value requires semantic interpretation (e.g., "guidance range of $100 to $105 million" → low=100, high=105)
- The metric name in the filing doesn't match any parsed fact's context

**Input:**
- List of still-unfilled data_needed keys with their labels and units
- Raw source text assembled from:
  1. All `sec_edgar_earnings` and `sec_edgar_filing_text` tool outputs (categorized as ACTUALS or GUIDANCE based on content keywords like "outlook", "guidance", "anticipates")
  2. All prefetch_results (targeted 10-K sections, XBRL data) — always included regardless of tool type
  3. Fallback: if no structured sources found, uses the ReAct agent's research narrative + all tool outputs

**Method:**

1. **Source assembly:** Builds `raw_sources` string (~8K chars max sent to the LLM):
   - For guidance keys: extracts the specific outlook section from the prior quarter's press release (e.g., "Fourth Quarter 2024 Outlook" section, not the headline)
   - For actuals keys: includes the current quarter's press release text
   - Prefetched 10-K sections always appended (up to 3K chars each)

2. **LLM call:** Sonnet with JSON prefill:
   ```
   System: "Extract specific numeric values from SEC filings. Respond with a JSON object ONLY."
   User: [period context, key descriptions, source text, matching rules]
   Assistant (prefill): "{"    ← forces JSON output
   ```

3. **Matching rules in the prompt:**
   - Keys containing "guided"/"guidance" → extract from OUTLOOK section, not actuals
   - Keys without "guided" → extract from RESULTS section, not guidance
   - Guidance ranges → extract low and high separately
   - Use exact numbers ("$4,278.9 million" → 4278.9, not 4300)
   - Match the period specified — don't mix annual and quarterly

4. **Response parsing:** Prepends `{` (the prefill), parses JSON. If JSON fails, tries to find embedded `{...}` in prose response.

**Output:** JSON mapping keys to numeric values: `{"q4_2024_guided_adjusted_ebitda_low": 100, "q4_2024_guided_adjusted_ebitda_high": 105}`

**Cost:** One Sonnet API call per question when needed (~$0.05, ~2-3 seconds). Only runs if earlier steps left keys unfilled.

**Why Sonnet (not Haiku):** This step requires semantic interpretation — distinguishing actuals from guidance in the same document, understanding that "Fourth Quarter 2024 Outlook" section contains guidance for Q4 (not Q4 actuals). Haiku was unreliable for this disambiguation (P28).

**Strengths:**
- Understands semantic context (actuals vs. guidance in same document)
- Can interpret column headers ("Next 12 Months" = 2025 cash requirements)
- Handles any data format — tables, prose, footnotes, nested disclosures
- Works for guidance ranges where regex can't distinguish low/high endpoints

**Failure modes:**
- Returns empty JSON `{}` when data isn't in the provided source text
- May round numbers despite "don't round" instructions (mitigated by earlier steps handling precise values first)
- Source assembly may miss the right text if it's not in tool_log or prefetch_results
- If raw_sources is too long (>8K chars), the relevant section may be truncated

**Entry point:** `agent.py:_llm_extract_remaining()`

### Step 3.5: Cross-Validation

**When it runs:** After all four extraction steps complete, before the calculator.

**Purpose:** Catches two classes of extraction errors that individual steps can't detect:

1. **Duplicate values:** If 3+ data_needed keys have the exact same numeric value (and it's > 1000), the XBRL matcher likely filled them all from one concept. Clears all duplicated values so the formatter falls back to the narrative. See P78.

2. **Absurd ratios:** Previews division-based calculation steps. If the numerator/denominator ratio would be > 10,000x or < 0.0001x, the inputs are likely wrong (e.g., mixing millions and raw dollars). Clears both values.

**Philosophy:** Better to return no structured answer than a confidently wrong one. Cleared values force a narrative fallback, which is less precise but less likely to be catastrophically wrong.

**Entry point:** `agent.py:_cross_validate_extraction(state)`

### Fallback: ReAct Agent Narrative

When all four extraction steps fail, the answer formatter falls back to the ReAct agent's research narrative. The agent may have found and reported the right numbers in prose even though the structured pipeline didn't capture them. This is the least reliable path — it works (Micron beat/miss got 140bps from narrative) but is not deterministic.

## Prefetch System (Step 2a)

The prefetch is the most impactful component — it determines what data the extraction pipeline and agent see.

### Primary Path: Structured `filings_needed`

The planner outputs a `filings_needed` list — each entry specifies exactly what to fetch:

```json
"filings_needed": [
    {"type": "xbrl", "concepts": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"], "reason": "annual revenue for CAGR"},
    {"type": "8-K", "period": "Q4 2024", "reason": "Q4 2024 actual earnings results"},
    {"type": "8-K", "period": "Q3 2024", "reason": "Q3 2024 guidance for Q4"},
    {"type": "10-K", "period": "2024", "section": "tax", "reason": "effective tax rate from income tax footnote"},
    {"type": "10-K", "period": "2022", "section": "kpi", "reason": "2020-2022 ARPU from operational highlights"}
]
```

The prefetch iterates this list and fetches each entry:

| Type | Action | Example |
|------|--------|---------|
| `"xbrl"` | Calls `get_company_facts(ticker, concept)` for each concept in the list. Tries alternatives in order, breaks on first success. | Revenue: tries "Revenues", then "RevenueFromContractWithCustomer..." |
| `"8-K"` | Calls `get_earnings_press_release(ticker, period)`. Finds the press release exhibit via fiscal quarter filename matching (handles non-calendar FY, 2-digit year). | "Q4 2024" → finds `lyft-2024x12x31pressreleas.htm` |
| `"10-K"` / `"10-Q"` | Calls `get_filing_text(ticker, type, section, period)`. Section markers jump to the right part of the 400-600K char filing. | section="tax" → jumps to "effective income tax rate" at pos 270K |

**Per-entry ticker override:** Each filing entry can specify its own `ticker`, enabling multi-company questions with a single filings_needed list (e.g., `{"type": "xbrl", "ticker": "PEP", "concepts": ["PaymentsOfDividends"]}`). The default ticker comes from the planner's `clarifications.company`.

**Why this works:** The planner (LLM) does the hard thinking — which filings? which XBRL concepts? which sections? which periods? The code does the easy part — fetching what was specified. New question types work without code changes.

**What the planner knows:** The prompt includes a reference list of common XBRL concept names, section types, and conventions (e.g., "forward-looking data for 2025 → FY2024 10-K", "beat/miss needs both actuals and prior quarter guidance").

### Fallback Path: Keyword-Based Heuristics (Legacy)

When `filings_needed` is empty or doesn't cover all needs, the old heuristic path runs:

1. **XBRL concept map** — maps data_needed key keywords to XBRL concepts
2. **Earnings press release** — triggered by source_strategy mentioning "8-K" or "earnings"
3. **Section keywords** — maps question keywords to 10-K section types

This fallback is being phased out as `filings_needed` coverage improves. It's harmless but redundant when the planner generates good structured requests.

### Filing Access Details

**Fiscal year handling:** Press release fetcher matches by fiscal quarter in exhibit filename (e.g., "a2024q3ex991" or "tjxq4fy25"). Handles both 4-digit ("2024") and 2-digit ("fy25") year formats.

**Older filings:** SEC EDGAR's `recent` array only covers ~40-50 most recent filings. For older filings, `_find_filing()` searches supplementary filing history files (`filings.files` in the submissions response).

**Section marker priority:** Markers tried in specificity order — "effective income tax rate" before "provision for income taxes" to avoid matching risk factor boilerplate.

**Skip-ToC logic:** The first match for a section marker is often a Table of Contents entry or forward-looking disclaimer — no actual data. `_extract_section()` checks if there are 2+ digit numbers within 500 chars of the match. If not, it skips to the next occurrence. Falls back to the first occurrence if no data-rich match is found. See P99.

**10-K sections available:** revenue, tax, compensation, leases, employees, shares, cash_obligations, reconciliation, kpi, officers, mda, risk, financial_statements, notes, debt, acquisitions, segments. Arbitrary section names are also supported — the system converts underscores to spaces and uses the phrase as a fallback marker.

## Components

### `agent.py` — Orchestrator
**Purpose:** Runs the full pipeline. Coordinates planner, prefetch, ReAct, extraction, cross-validation, calculator, formatter.
**Entry point:** `run(question, as_of_date=None) → dict`
**Key functions:** `_plan()` (with caching), `_prefetch_sec_data()`, `_cross_validate_extraction()`, `_llm_extract_remaining()`

### `extractor.py` — Structured Data Extraction
**Purpose:** Parses XBRL, dollar amounts, percentages from tool results. Matches to data_needed keys.
**Entry point:** `extract_from_tool_log(tool_log, state) → state`
**Key functions:** `_parse_xbrl_output()`, `_parse_filing_text()`, `_match_fact_to_key()`

### `tools/sec_edgar.py` — SEC Filing Tools
**Purpose:** Fetches XBRL data, filing text, earnings press releases from SEC EDGAR.
**Key functions:** `get_company_facts()`, `get_filing_text()`, `get_earnings_press_release()`
**HTML conversion:** `_html_to_text()` with table structure parser — converts HTML tables to column-annotated text.
**Section extraction:** `_extract_section()` — jumps to specific parts of long filings using keyword markers.

### `calculator.py` — Deterministic Calculation
**Purpose:** Executes Python formulas from planner's calculation_steps.
**Entry point:** `execute_calculations(state) → state`

### `prompts.py` — All Prompt Templates
**Purpose:** System prompts for planner, ReAct agent, and formatter. Includes financial methodology and concepts references.

### `financial_methodology.py` / `financial_concepts.py` — Domain Knowledge
**Purpose:** Correct formulas and procedures embedded in planner prompt. Aligned to FAB benchmark conventions (N = label number for CAGR, ending inventory for turnover).

### `ticker.py` — Robust Ticker Extraction
**Purpose:** Extracts stock tickers from planner output. 4 strategies, excluded words list.

### `llm.py` — Anthropic API Client
**Purpose:** Claude API calls with prompt caching and rate limiting. Model routing (Sonnet for reasoning, Haiku for formatting).

### `eval.py` — Evaluation Harness
**Purpose:** Runs the agent against the FAB benchmark, scores answers against rubrics.
**Entry point:** `python eval.py [--indices 0 2 9] [--score-only FILE] [--cheap-reeval FILE]`
**Key features:**
- **Benchmark date:** All runs use `as_of_date="2025-02-01"` (the epoch when FAB ground truth was authored).
- **Deterministic numeric pre-check:** Before calling the LLM judge, checks if all numbers in the criterion appear in the answer within 2% tolerance. Auto-passes if they match — eliminates judge non-determinism for numeric questions.
- **Mode-of-3 judging:** Each rubric criterion is judged 3x by Haiku, with majority vote (2/3 or 3/3 = pass). Eliminates single-call judge variance that caused 6 questions to flip between pass/fail across runs.
- **Cheap re-eval:** Replays extraction + calculator + formatter + judge on saved tool_logs from a previous run (~$1.50 vs ~$8 for a full run). Only reliable for judging changes, not extraction changes.

## External Dependencies

| Service | Purpose | Auth | Rate Limit | Failure Mode |
|---------|---------|------|------------|-------------|
| SEC EDGAR XBRL API | Company financial facts | None (User-Agent header) | 10 req/sec | Returns empty/error — agent falls back to filing text |
| SEC EDGAR Submissions | Filing metadata, accession numbers | None | 10 req/sec | Can't find filing — agent falls back to web search |
| SEC EDGAR Archives | Raw filing HTML | None | 10 req/sec | Can't fetch document — skip this source |
| Anthropic Claude API | LLM calls (planner, ReAct, extraction, formatting) | API key | Tier-dependent | Pipeline stops |
| Web Search (Claude built-in) | Fallback data source | Via Claude API | Per-request | Returns no results — agent reports "not found" |
| FMP API | Alternative financial data | API key | Tier-dependent | Returns empty — agent uses SEC data |

## Known Constraints & Gotchas

### Date Context
The planner uses today's date to determine "most recent" periods. The FAB benchmark was authored when FY2024 was the latest available. Running in 2026, the planner picks FY2025 data, which may not match ground truth. **Addressed:** `run(question, as_of_date="2025-02-01")` overrides the planner's and ReAct agent's date context. The eval harness sets this automatically via `BENCHMARK_DATE`.

### XBRL Concept Matching
Substring matching (`if metric.lower() in concept_name.lower()`) causes false matches. "IncomeTaxExpenseBenefit" matches both the exact concept AND "CurrentIncomeTaxExpenseBenefit". The extractor then fills unrelated keys with the same value. See P42.

### 10-K Size vs Context Limit
10-K filings are 400-600K chars. Default 15K char limit means >95% of the filing is invisible. Section-targeted fetching mitigates this but requires knowing which section to fetch. Questions about data in unexpected sections may fail.

### Table Column Disambiguation
The table parser annotates values with column headers for simple tables (Netflix contractual obligations). Multi-level headers (Lyft: "Three Months Ended" + "Dec. 31, 2024") and complex layouts may not parse correctly. The position tiebreaker (first value = most recent quarter) works for financial statements but not all table types. See P59.

### Non-Calendar Fiscal Years
The earnings press release fetcher matches by fiscal quarter in exhibit filename. This handles Micron (FY ends Sep) and Oracle (FY ends May). But period detection from the planner still assumes calendar years in many places. See P45.

### Older SEC Filings
The SEC EDGAR submissions API's `recent` array only covers the most recent ~40-50 filings. For older filings (typically 3+ years back for prolific filers, or any filing pre-2021 for many companies), `_find_filing` searches supplementary filing history files listed in `filings.files`. This adds 1-2 extra API calls but enables access to filings going back to 2000. See P67.
