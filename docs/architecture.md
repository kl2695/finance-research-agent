# Multi-Domain Research Agent — Technical Architecture

## System Overview

A domain-agnostic research agent that answers precise questions using structured public databases. The core pipeline (planner → prefetch → ReAct → extraction → calculator → formatter) is shared across domains. Each domain implements the Domain ABC, providing its own tools, prompts, concept maps, and benchmarks.

**Domains:**
- **Finance** — SEC EDGAR, XBRL, earnings press releases. 84% on FAB (20 points above Opus 4.7 at 64%).
- **FDA Regulatory** — openFDA APIs, MAUDE, AccessData PDFs. 90% on 510kQA (novel benchmark, 30 questions).

Key constraint: precision. Research questions require exact values (26.1 basis points, not "about 26"; K213456, not "a Xenco Medical device"). Every component is designed to minimize rounding, hallucination, and data loss.

## Domain Interface

All domain-specific logic is behind the `Domain` ABC (`domains/base.py`). Core pipeline code depends only on this interface:

```python
class Domain(ABC):
    name: str                           # "finance", "fda"
    planner_system: str                 # System prompt for planner LLM
    planner_prompt_template: str        # User prompt with {question}, {date} slots
    react_system: str                   # System prompt for ReAct agent
    react_tools: list[dict]             # Tool schemas for ReAct (Anthropic format)
    tool_dispatch: dict[str, Callable]  # Filing type → fetcher function for prefetch
    concept_map: dict[str, list[str]]   # Keyword → concept names for extraction matching
    keyword_map: dict[str, list[str]]   # Phrase → standardized keys for context matching
    benchmark_questions: list           # Benchmark for eval harness

    def execute_tool(name, input_data) -> str      # Tool execution for ReAct
    def extract_identifier(text) -> str             # Entity ID extraction (ticker, K-number)
    def context_size_tier(state) -> int             # Prefetch context size
    def classify_tools(tool_log) -> dict            # Route tools to extraction layers
    def pre_extraction_filter(state) -> (state, stash)  # Domain hooks (e.g., guidance hiding)
    def cross_validate(state) -> state              # Domain-specific sanity checks
```

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              MULTI-DOMAIN RESEARCH AGENT                        │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                          CORE PIPELINE (core/)                             │  │
│  │                                                                            │  │
│  │   main.py / eval.py                                                        │  │
│  │        │                                                                   │  │
│  │        ▼                                                                   │  │
│  │   ┌─────────┐    domain.planner_system    ┌───────────────┐                │  │
│  │   │  agent   │───────────────────────────►│  Anthropic API │                │  │
│  │   │  .py     │    domain.react_system      │  (Sonnet/Haiku)│                │  │
│  │   │         │◄───────────────────────────│               │                │  │
│  │   │  run()   │    domain.answer_system     └───────────────┘                │  │
│  │   └────┬────┘                                     ▲                        │  │
│  │        │                                          │                        │  │
│  │        │  domain.tool_dispatch[type]               │ tool_use / tool_result │  │
│  │        │  domain.execute_tool(name, input)         │                        │  │
│  │        ▼                                          │                        │  │
│  │   ┌──────────┐   ┌──────────┐   ┌────────────┐   │                        │  │
│  │   │extractor │   │calculator│   │  llm.py     │───┘                        │  │
│  │   │  .py     │   │  .py     │   │  call_claude│                            │  │
│  │   └──────────┘   └──────────┘   │  call_with_ │                            │  │
│  │        ▲                        │  tools      │                            │  │
│  │        │ domain.concept_map     └────────────┘                            │  │
│  │        │ domain.keyword_map          ▲                                     │  │
│  │        │ domain.classify_tools       │ cost tracking                       │  │
│  │        │ domain.cross_validate       │ rate limiting                       │  │
│  └────────┼─────────────────────────────┼─────────────────────────────────────┘  │
│           │                             │                                        │
│  ┌────────┼─────────────────────────────┼─────────────────────────────────────┐  │
│  │        │      DOMAIN INTERFACE (domains/base.py)                           │  │
│  │        │      Domain ABC — 15+ methods/properties                          │  │
│  └────────┼─────────────────────────────┼─────────────────────────────────────┘  │
│           │                             │                                        │
│  ┌────────┴──────────────┐  ┌───────────┴─────────────┐                         │
│  │  FINANCE DOMAIN       │  │  FDA DOMAIN              │                         │
│  │  domains/finance/     │  │  domains/fda/            │                         │
│  │                       │  │                          │                         │
│  │  Prompts:             │  │  Prompts:                │                         │
│  │   FINANCIAL_CONCEPTS  │  │   FDA_CONCEPTS           │                         │
│  │   FINANCIAL_METHODOLOGY│ │   FDA_METHODOLOGY        │                         │
│  │   Beat/miss examples  │  │   Clearance examples     │                         │
│  │                       │  │                          │                         │
│  │  Tools:               │  │  Tools:                  │                         │
│  │   sec_edgar_financials│  │   openfda_510k           │                         │
│  │   sec_edgar_earnings  │  │   openfda_predicates     │                         │
│  │   sec_edgar_filing    │  │   openfda_maude          │                         │
│  │   fmp_financials      │  │   openfda_recall         │                         │
│  │                       │  │   openfda_classification │                         │
│  │  Identifier:          │  │                          │                         │
│  │   Stock ticker        │  │  Identifier:             │                         │
│  │   (LYFT, AAPL)        │  │   K-number / product code│                         │
│  │                       │  │   (K213456, DRG)         │                         │
│  │  Benchmark:           │  │                          │                         │
│  │   FAB (50 questions)  │  │  Benchmark:              │                         │
│  │   84% accuracy        │  │   510kQA (30 questions)  │                         │
│  │                       │  │   90% accuracy           │                         │
│  └───────────┬───────────┘  └────────────┬────────────┘                         │
│              │                           │                                       │
└──────────────┼───────────────────────────┼───────────────────────────────────────┘
               │                           │
               ▼                           ▼
┌──────────────────────────┐  ┌──────────────────────────────────┐
│  EXTERNAL DATA SOURCES   │  │  EXTERNAL DATA SOURCES           │
│                          │  │                                  │
│  SEC EDGAR               │  │  openFDA APIs                    │
│   • XBRL company facts   │  │   • /device/510k.json            │
│   • Filing archives (HTML)│  │   • /device/event.json (MAUDE)   │
│   • Submissions metadata │  │   • /device/recall.json           │
│                          │  │   • /device/classification.json   │
│  FMP API                 │  │                                  │
│   • Income statements    │  │  FDA AccessData                  │
│   • Balance sheets       │  │   • 510(k) detail pages (HTML)   │
│                          │  │   • Summary PDFs (predicate data) │
└──────────────────────────┘  └──────────────────────────────────┘
```

## Pipeline Flow

```
Question + Domain
       │
       ▼
┌─────────────────┐
│  1. PLANNER      │  Sonnet — creates structured research plan:
│                  │    • data_needed keys (what values to find)
│                  │    • filings_needed (which data sources to fetch)
│                  │    • calculation_steps (formulas to compute)
│                  │    • clarifications (period, entity, methodology)
│                  │  Prompt: domain.planner_system + domain.planner_prompt_template
│                  │  Cached by MD5(question + date + domain.name)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  2a. PREFETCH    │  Programmatic — no LLM, just API calls:
│                  │    for entry in filings_needed:
│                  │      fetcher = domain.tool_dispatch[entry.type]
│                  │      result = fetcher(entry, identifier)
│                  │    Identifier from domain.extract_identifier()
│                  │    Results + tool_log collected for next steps.
└────────┬────────┘
         │  prefetch_results injected into ReAct prompt
         ▼
┌─────────────────┐
│  2b. ReAct LOOP  │  Sonnet multi-turn with tool use:
│                  │    System: domain.react_system
│                  │    Tools: [web_search] + domain.react_tools
│                  │    Executor: domain.execute_tool
│                  │    Max turns: 15 (complex) or 10 (default)
│                  │    Timeout: 120s wall clock
│                  │  Produces: research narrative + tool_log
└────────┬────────┘
         │  tool_log (prefetch + ReAct combined)
         ▼
┌─────────────────┐
│  3. EXTRACTION   │  Fill state.data_needed from tool_log:
│                  │    Classify: domain.classify_tools(tool_log)
│                  │      → {"structured": [...], "prose": [...]}
│                  │    3a. Structured: domain-specific parsers
│                  │    3b. Prose: regex + domain.keyword_map scoring
│                  │        (with domain.pre/post_extraction hooks)
│                  │    3c. LLM fact match: Haiku + domain.extraction_hints
│                  │    3d. LLM raw extraction: Sonnet reads source text
│                  │    3.5: domain.cross_validate(state)
│                  │  Rule: filled keys are NEVER overwritten.
└────────┬────────┘
         │  state.data_needed now populated
         ▼
┌─────────────────┐
│  4. CALCULATOR   │  Deterministic Python eval:
│                  │    for step in state.calculation_steps:
│                  │      result = eval(step.formula, data_needed_values)
│                  │    No LLM. Pure arithmetic.
└────────┬────────┘
         │  state.calculation_steps now have results
         ▼
┌─────────────────┐
│  5. FORMATTER    │  Haiku — formats final answer:
│                  │    Prompt: domain.answer_system + domain.answer_prompt_template
│                  │    Input: research narrative + calculation results
│                  │    Falls back to narrative if structured data incomplete.
│                  │  Produces: answer text with citations
└─────────────────┘
```

## Data Flow: State Dict

The **state dict** is the central data structure that flows through every pipeline stage. Created by the planner, filled by extraction, computed by the calculator, formatted into the answer.

```
state = {
    "plan": "one-line description",
    "clarifications": {
        "company": "Lyft, Inc., ticker LYFT" | "K-number K213456",
        "period": "Q4 2024" | "2022-2024",
        "formula": "python expression" | "lookup only",
        "source_strategy": "which databases to search",
    },
    "data_needed": {
        "revenue_fy2024": {              # ← planner creates these keys
            "value": 5791000000,         # ← extraction fills this
            "unit": "USD",
            "source": "XBRL (Revenues)", # ← extraction records provenance
            "confidence": "high",
            "label": "Revenue FY2024",
        },
        "clearance_date_k213456": {
            "value": "2022-06-24",
            "unit": "date",
            "source": "openFDA 510(k)",
            ...
        }
    },
    "filings_needed": [                  # ← planner specifies, prefetch consumes
        {"type": "xbrl", "concepts": ["Revenues"], "reason": "..."},
        {"type": "510k", "identifier": "K213456", "reason": "..."},
    ],
    "calculation_steps": [               # ← planner defines, calculator executes
        {"step": "cagr", "formula": "(revenue_fy2024/revenue_fy2021)**(1/3)-1",
         "inputs": ["revenue_fy2024", "revenue_fy2021"], "result": 0.156}
    ],
    "answer": {"value": 0.156, "formatted": "15.6%", "sources": [...]}
}
```

## Planner Caching

The planner (Step 1) is non-deterministic — different runs produce different key names, filings_needed lists, and formulas. This causes score variance even when the underlying extraction and calculation logic hasn't changed.

**Solution:** Plans are cached by `MD5(question + date)` in `results/planner_cache.json`. On cache hit, the plan is reused with all values reset to `null` for fresh extraction. This eliminates planner non-determinism between runs.

**Cache clearing:** Delete the cache file to force re-planning (needed after prompt changes or methodology updates).

**Entry point:** `agent.py:_plan(question, as_of_date, use_cache=True)`

## ReAct Agent Loop (Step 2b)

The ReAct (Reasoning + Acting) loop is the central reasoning engine. It receives prefetched data, reasons about what's missing, makes additional tool calls, and produces a research narrative with findings.

### How It Works

The loop is implemented in `core/llm.py:call_with_tools()`. It uses the Anthropic tool-use protocol:

```
┌─────────────────────────────────────────────────────────┐
│  INITIAL MESSAGE                                        │
│  System: domain.react_system (tool guidance, examples)  │
│  User: plan JSON + prefetched data + question           │
│  Tools: [web_search] + domain.react_tools               │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────┐
│  LLM RESPONSE (Sonnet)              │
│  Contains: text blocks + tool_use   │
│  blocks interleaved                 │
│                                     │◄──────────┐
│  If stop_reason == "tool_use":      │           │
│    Execute each tool_use block      │           │
│    via domain.execute_tool()        │           │
│    Append results as tool_result    │           │
│    Continue loop ───────────────────┼───────────┘
│                                     │
│  If stop_reason == "end_turn":      │
│    Extract final text               │
│    Return (narrative, tool_log)     │
└──────────────────────────────────────┘
```

Each turn:
1. Send full conversation history (system + messages) to Sonnet
2. Sonnet responds with text (reasoning) and/or `tool_use` blocks (actions)
3. For each `tool_use`: call `domain.execute_tool(name, input)`, get string result
4. Append assistant response + tool results to messages
5. Repeat until Sonnet says `end_turn` (done reasoning) or max turns reached

### What Goes Into the Prompt

The ReAct agent receives a rich context assembled from three sources:

**System prompt** (`domain.react_system`):
- Tool selection hierarchy (which tools to prefer and when)
- Precision instructions ("do NOT round", "preserve ALL significant digits")
- Domain-specific guidance (finance: "use FORMAL NAMES from SEC filings"; FDA: "clearance, not approval")
- Cross-validation instructions ("check that your numbers are internally consistent")
- Domain concept reference (injected via the domain's concepts module)

**User message** (assembled by `core/agent.py`):
- The full planner output as JSON (plan, data_needed, filings_needed, calculation_steps)
- Research date context (`as_of_date`)
- Few-shot examples of successful research patterns (domain-supplied, see below)
- Prefetched data (appended after the prompt, truncated by `domain.context_size_tier`)

**Tools** (available for the agent to call):
- `web_search` — always provided by core (Anthropic server-side tool)
- Domain-specific tools — provided by `domain.react_tools` (e.g., `sec_edgar_financials`, `openfda_510k`)

### Tool Execution

Two categories of tools with different execution paths:

**Server-side tools** (web_search): Executed by Anthropic's servers. Results appear as `web_search_tool_result` blocks in the response. The agent never "calls" these — it just decides to search and Anthropic handles execution. URLs from search results are logged for citation.

**Local tools** (domain tools): Executed by `domain.execute_tool(name, input_data) -> str`. The domain maps tool names to functions. For finance: `sec_edgar_financials` → `get_company_facts()`, `sec_edgar_earnings` → `get_earnings_press_release()`. For FDA: `openfda_510k` → `search_510k()`, `openfda_predicates` → `get_510k_predicates()`. Results are truncated to 4,000 chars before appending to the conversation (prevents context overflow).

### Tool Log

Every tool call (both server-side and local) is recorded in `tool_log` — a list of `{"tool": name, "input": {...}, "output": "..."}` dicts. This log serves three purposes:
1. **Extraction input** — the extraction pipeline (Step 3) parses tool outputs to fill `data_needed` values
2. **Offline replay** — saved with eval results for cheap-reeval (replay extraction without re-running the agent)
3. **Debugging** — the action log traces every tool call with inputs and output preview

### Few-Shot Examples

Each domain provides 3 worked research patterns in the ReAct prompt. These guide the agent's tool usage strategy and reasoning quality.

**Finance domain examples:**
1. **Beat/miss with guidance range** — TJX pre-tax margin: finds actuals in current 8-K, guidance in prior quarter's 8-K, computes beat from BOTH low and high end, cross-validates against company's own statement.
2. **Multi-company comparison** — KO dividend payout ratio vs peers: identifies competitors, fetches data for each via XBRL, computes ratios, ranks.
3. **Qualitative deep-section extraction** — Shift4 vendor concentration risk: navigates past ToC/disclaimer matches to find the actual disclosure in notes to financial statements.

**FDA domain examples:**
1. **Clearance timeline computation** — median clearance time for product code DRG: fetches all clearances in date range, computes per-record date arithmetic, reports median with sample size.
2. **Multi-source cross-reference** — product code LWS: three separate tool calls (510(k) + MAUDE + recalls), merges results, reports synthesis with cross-references.
3. **Predicate device lookup** — K213456: calls predicate tool (PDF scraping), identifies predicate K-numbers, looks up each predicate's clearance date via follow-up 510(k) calls.

### Context Size Tiers

Prefetched data is injected into the ReAct prompt with size limits determined by `domain.context_size_tier(state)`:

**Finance domain:**
| Question Type | Limit per Source | Rationale |
|---------------|-----------------|-----------|
| Qualitative (no calculation, "lookup only") | 50K chars | Need full sections for narrative answers |
| Quantitative with filing sections (10-K/10-Q) | 15K chars | Need tables from targeted sections |
| Simple XBRL calculation | 4K chars | Just need confirmation data |

**FDA domain:**
| Question Type | Limit per Source | Rationale |
|---------------|-----------------|-----------|
| Predicate lookup or qualitative | 30K chars | PDF text is long |
| Standard queries | 8K chars | openFDA JSON is compact |

### Safeguards

**Max turns:** 10 default, 15 for complex questions (5+ filings_needed entries). Prevents runaway loops where the agent keeps calling tools without converging. Motivated by FAB paper insight: "top performers register high numbers of tool calls."

**Wall-clock timeout:** 120 seconds. If the loop exceeds this, it returns whatever results it has. Prevents single questions from blocking the eval pipeline. Added after P84 where one question's ReAct loop ran for 8 minutes.

**Tool output truncation:** Each tool result is capped at 4,000 chars before being appended to the conversation. Prevents a single large filing from consuming the entire context window. The full output is still available in `tool_log` for the extraction pipeline.

### What the ReAct Agent Produces

The agent's final text output is a **research narrative** — natural language summarizing what it found, with exact numbers and source citations. This narrative serves two purposes:

1. **Input to extraction** — the extraction pipeline (Step 3) also receives the raw tool_log, but the narrative provides semantic context that helps the LLM extraction fallback (Step 3d) understand which numbers answer which questions.

2. **Fallback answer source** — if the structured extraction pipeline fails to fill all `data_needed` values, the formatter (Step 5) falls back to the narrative. This is less precise but more robust. Example: Micron beat/miss was answered from the narrative when structured extraction couldn't distinguish quarterly from annual figures.

### Non-Determinism

The ReAct loop is the primary source of score variance between runs. The same question can produce different tool call sequences, find different data, or reason differently. This is inherent to LLM-based reasoning — the planner cache eliminates plan variance, and mode-of-3 judging reduces judge variance, but the ReAct agent's tool path remains stochastic. In the finance eval, 6 of 50 questions flip between pass/fail across runs due to ReAct non-determinism.

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

**Entry point:** `domain.cross_validate(state)` — each domain implements its own checks. Finance checks duplicate values and absurd ratios. FDA checks clearance time ranges and device class validity.

### Fallback: ReAct Agent Narrative

When all four extraction steps fail, the answer formatter falls back to the ReAct agent's research narrative. The agent may have found and reported the right numbers in prose even though the structured pipeline didn't capture them. This is the least reliable path — it works (Micron beat/miss got 140bps from narrative) but is not deterministic.

## Prefetch System (Step 2a)

The prefetch is the most impactful component — it determines what data the extraction pipeline and agent see. It is domain-agnostic: core iterates `filings_needed` and dispatches each entry via `domain.tool_dispatch[entry.type]`.

### How It Works

The planner outputs a `filings_needed` list — each entry specifies a data source to fetch:

**Finance domain example:**
```json
[
    {"type": "xbrl", "concepts": ["Revenues"], "reason": "annual revenue for CAGR"},
    {"type": "8-K", "period": "Q4 2024", "reason": "Q4 2024 actual earnings results"},
    {"type": "10-K", "period": "2024", "section": "tax", "reason": "effective tax rate"}
]
```

**FDA domain example:**
```json
[
    {"type": "510k", "identifier": "K213456", "reason": "clearance record"},
    {"type": "predicates", "identifier": "K213456", "reason": "predicate device chain"},
    {"type": "maude_count", "concepts": ["brand_name:HeartMate 3"], "reason": "adverse event counts"}
]
```

Core prefetch (`core/agent.py:_prefetch_data`) iterates this list, looks up `domain.tool_dispatch[type]` for each entry, calls the fetcher, and collects results. Each domain defines its own type → fetcher mapping:

| Domain | Types | Fetcher Examples |
|--------|-------|-----------------|
| Finance | xbrl, 8-K, 10-K, 10-Q | SEC EDGAR XBRL API, earnings press release finder, filing text with section extraction |
| FDA | 510k, predicates, maude, maude_count, recall, classification | openFDA JSON APIs, AccessData PDF scraper |

**Per-entry identifier override:** Each entry can specify its own entity ID (e.g., `"ticker": "PEP"` or `"identifier": "K160702"`), enabling multi-entity questions with a single filings_needed list.

**Why this works:** The planner (LLM) does the hard thinking — which sources? which concepts? which periods? The domain's tool_dispatch does the fetching. Core just iterates and dispatches. New domains work by implementing `tool_dispatch` — no core changes needed.

### Finance Domain: Filing Access Details

**Fiscal year handling:** Press release fetcher matches by fiscal quarter in exhibit filename (e.g., "a2024q3ex991" or "tjxq4fy25"). Handles both 4-digit ("2024") and 2-digit ("fy25") year formats.

**Older filings:** SEC EDGAR's `recent` array only covers ~40-50 most recent filings. For older filings, `_find_filing()` searches supplementary filing history files.

**Skip-ToC logic (core):** The first match for a section marker is often a Table of Contents entry. `_extract_section()` checks if there are 2+ digit numbers within 500 chars. If not, skips to the next occurrence. This is domain-agnostic (in core) — markers are domain-supplied.

### FDA Domain: Data Access Details

**openFDA API:** Returns structured JSON with named fields. Date formats differ by endpoint (510(k) uses YYYY-MM-DD, MAUDE uses YYYYMMDD) — normalized in the parser.

**Predicate device scraping:** openFDA lacks predicate K-numbers. The FDA domain scrapes AccessData HTML to find the 510(k) summary PDF URL, downloads the PDF, extracts text with pdfplumber, and uses regex `K\d{6}` to find predicate K-numbers.

**Pagination ceiling:** openFDA limits skip to 25,000. For large result sets (MAUDE can have millions), use date range partitioning or the count endpoint for aggregates.

## Components

### Core (`core/`)

| File | Purpose |
|------|---------|
| `core/agent.py` | Domain-agnostic orchestrator. `run(question, domain, as_of_date)`. Coordinates planner, prefetch, ReAct, extraction, calculator, formatter. |
| `core/extractor.py` | Multi-layer extraction framework. Parameterized by domain's concept_map, keyword_map, and sanity_check_config. |
| `core/calculator.py` | Deterministic Python formula evaluation from state dict. |
| `core/llm.py` | Anthropic API client with prompt caching, rate limiting, model routing, and cost tracking. |
| `core/state.py` | State dict utilities (create, validate, render). |
| `core/types.py` | Shared types: Fact, FilingRequest, ToolResult, BenchmarkQuestion. |

### Finance Domain (`domains/finance/`)

| File | Purpose |
|------|---------|
| `domain.py` | FinanceDomain — implements Domain ABC. Wires tools, prompts, concept maps, benchmark. |
| `tools.py` | SEC EDGAR API client: XBRL, filing text, earnings press releases. HTML table parser. |
| `concepts.py` | Financial concepts reference (CAGR, margins, ratios, GAAP vs non-GAAP). |
| `methodology.py` | Financial methodology (beat/miss, turnover, fiscal year conventions). |
| `identifier.py` | Stock ticker extraction from planner output. |
| `registry.py` | Tool schemas and dispatch for ReAct loop. |

### FDA Domain (`domains/fda/`)

| File | Purpose |
|------|---------|
| `domain.py` | FDADomain — implements Domain ABC. Wires tools, prompts, concept maps, benchmark. |
| `tools.py` | openFDA API client (510(k), MAUDE, recalls, classification) + AccessData PDF scraper. |
| `concepts.py` | Regulatory concepts (device classes, pathways, MAUDE taxonomy). |
| `methodology.py` | Regulatory methodology (clearance timelines, predicate analysis, event counting). |
| `identifier.py` | K-number / product code extraction. |
| `benchmark/` | 510kQA benchmark (30 questions with programmatic ground truth). |

### Evaluation

| File | Purpose |
|------|---------|
| `eval.py` | Finance domain eval harness (FAB benchmark). Mode-of-3 judging, numeric pre-check, cost tracking. |
| `main.py` | Multi-domain CLI. `--domain {finance\|fda} --question "..." --eval --list-domains` |

### Backwards Compatibility (`src/`)

The `src/` directory contains shim modules that re-export from `core/` and `domains/finance/`. Existing code using `from src.agent import run` continues to work — it creates a FinanceDomain internally. New code should use `from core.agent import run` with an explicit domain.

## External Dependencies

### Core (all domains)

| Service | Purpose | Auth | Rate Limit | Failure Mode |
|---------|---------|------|------------|-------------|
| Anthropic Claude API | LLM calls (planner, ReAct, extraction, formatting) | API key | Tier-dependent | Pipeline stops |
| Web Search (Claude built-in) | Fallback data source | Via Claude API | Per-request | Returns no results — agent reports "not found" |

### Finance Domain

| Service | Purpose | Auth | Rate Limit | Failure Mode |
|---------|---------|------|------------|-------------|
| SEC EDGAR XBRL API | Company financial facts | None (User-Agent header) | 10 req/sec | Returns empty — falls back to filing text |
| SEC EDGAR Submissions | Filing metadata, accession numbers | None | 10 req/sec | Can't find filing — falls back to web search |
| SEC EDGAR Archives | Raw filing HTML | None | 10 req/sec | Can't fetch document — skip this source |
| FMP API | Alternative financial data | API key | Tier-dependent | Returns empty — agent uses SEC data |

### FDA Domain

| Service | Purpose | Auth | Rate Limit | Failure Mode |
|---------|---------|------|------------|-------------|
| openFDA Device APIs | 510(k), MAUDE, recalls, classification | Optional API key | 240 req/min (with key) | Returns error — agent reports "not found" |
| FDA AccessData | 510(k) detail pages + summary PDFs | None | ~1 req/sec (conservative) | No PDF available — predicate lookup fails gracefully |

## Known Constraints & Gotchas

### Cross-Domain

**Date Context:** The planner uses today's date to determine "most recent" periods. Benchmarks require point-in-time answers. `run(question, domain, as_of_date="2025-02-01")` overrides the date context. Each BenchmarkQuestion carries its own `as_of_date`.

**Planner Cache Key:** Includes domain name — `MD5(question + date + domain.name)`. Without the domain name, finance and FDA plans for the same question text would collide.

### Finance Domain

**XBRL Concept Matching:** Substring matching causes false matches. "IncomeTaxExpenseBenefit" matches both the exact concept AND "CurrentIncomeTaxExpenseBenefit". See P42.

**10-K Size vs Context Limit:** 10-K filings are 400-600K chars. Section-targeted fetching with domain-configured context tiers (50K qualitative / 15K quantitative / 4K simple) mitigates this.

**Non-Calendar Fiscal Years:** Press release fetcher handles non-standard fiscal years (Micron FY ends Sep, Oracle FY ends May) via fiscal quarter filename matching. See P45.

### FDA Domain

**No Predicate Data in API:** openFDA 510(k) endpoint has no predicate device field. Predicate K-numbers are extracted from 510(k) summary PDFs via AccessData scraping. Coverage is good for 2010+ submissions; older ones may lack PDFs.

**MAUDE Date Format:** MAUDE uses YYYYMMDD while 510(k) uses YYYY-MM-DD. The parser normalizes all dates to YYYY-MM-DD.

**Pagination Ceiling:** openFDA limits `skip` to 25,000. For product codes with millions of MAUDE events, use date range partitioning or the count endpoint for aggregates.

**Ground Truth Drift:** openFDA data updates continuously. Benchmark answers for count-based questions will change over time. Each question carries a `ground_truth_captured_at` date.
