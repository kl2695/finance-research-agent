# Multi-Domain Research Agent Refactor Plan

## Context

The finance research agent scores 78% (39/50) on FAB, 14 points above the leaderboard. We want to generalize it into a domain-agnostic research runtime with pluggable domains (finance, FDA stub). The spec (`docs/multi-domain-refactor-spec.md`) defines a Domain ABC, 14-step migration, and regression gate. Before committing to the plan, a grep audit of finance-specific terms in would-be core files was required.

## Grep Audit Results (Spec 12.1)

Searched `agent.py`, `extractor.py`, `calculator.py`, `llm.py`, `eval.py` for: ticker, USD, fiscal, XBRL, 10-K/Q, 8-K, SEC, EDGAR, CIK, FY, Q1-Q4, $, press release, earnings, financial, GAAP, guidance, filing, sec_edgar, revenue/margin/ebitda.

### Summary of Hits

| File | Finance Refs | Assessment |
|------|-------------|------------|
| `agent.py` (820 lines) | **200+** | ~400 lines finance-specific. `_prefetch_sec_data` (270 lines) is 100% finance. 13 direct sec_edgar imports. 17 ticker refs. Hardcoded XBRL/10-K/8-K dispatch. Guidance key hiding. Context tier logic references filing types. |
| `extractor.py` (608 lines) | **~120** | ~300 lines finance-specific. `_parse_xbrl_output` (26 lines), `_CONTEXT_KEYWORD_MAP` (26 lines), `_parse_fmp_output` (19 lines) are 100% finance. `concept_map` in `_match_fact_to_key` (20 lines) is 100% finance. LLM prompt has ~20 lines finance-specific. Tool name routing hardcoded. |
| `prompts.py` (286 lines) | **~170** | ~60-70% finance. Lines 50-115 (filings_needed docs), 137-148 (source selection), 157-170 (SEC tool selection), 193-197 (beat/miss), 273-276 (basis points formatting). FINANCIAL_CONCEPTS (70 lines) and FINANCIAL_METHODOLOGY (216 lines) are already parameterized via f-string injection. |
| `eval.py` (490 lines) | **~30** | Moderate. `load_dataset("vals-ai/finance_agent_benchmark")`, `BENCHMARK_DATE`, tool name refs in cheap-reeval (lines 398-411). Core judging (mode-of-3, numeric pre-check) is generic. |
| `calculator.py` (173 lines) | **0** | Clean. No finance coupling. |
| `llm.py` (145 lines) | **0** | Clean. No finance coupling. |
| `state.py` (~100 lines) | **0** | Clean. No finance coupling. |

### Critical Coupling Surfaces (from audit)

1. **agent.py `_prefetch_sec_data()`** (lines 382-654): 270 lines of pure finance -- structured filings_needed dispatch + keyword fallback heuristics + prior quarter guidance logic + ticker extraction. This is the single largest block of finance-specific code.

2. **agent.py extraction routing** (lines 130-205): Hardcoded tool names `"sec_edgar_financials"`, `"sec_edgar_earnings"`, `"sec_edgar_filing_text"` control which extraction layers run.

3. **agent.py guidance key hiding** (lines 148-157): `"guid" in key.lower()` temporarily removes guidance keys before structured extraction. Finance-specific hack.

4. **extractor.py `_match_fact_to_key()`** (lines 299-453): Generic scoring framework with finance `concept_map` (lines 311-329) interleaved. Quarterly/annual context keywords (lines 413-430) are finance vocabulary.

5. **prompts.py PLANNER_PROMPT** (lines 50-115): 65 lines of filings_needed documentation (SEC filing types, XBRL concepts, section names) embedded in the JSON schema description.

6. **tools/registry.py** (136 lines): 100% finance tool schemas + dispatch.

## Honest Assessment

### It IS doable because:
- The coupling, while pervasive (200+ refs in agent.py alone), is **identifiable and classifiable**. The grep audit shows exactly where every finance reference lives.
- The pipeline stages (plan -> prefetch -> ReAct -> extract -> calc -> format) have natural cut points for domain injection.
- Three files are already clean (`calculator.py`, `llm.py`, `state.py`).
- `financial_concepts.py` and `financial_methodology.py` are already parameterized via f-string injection in prompts -- they're basically template slots already.
- The incremental 14-step approach with regression testing at each step is sound.

### It's HARDER than the spec implies because:
- **`agent.py` is not "move and add a param"** -- it's rewriting ~400 of 820 lines. The prefetch function (270 lines) can't just be "dispatched" -- it's a complex subsystem with branching, fallback heuristics, and prior-quarter calculation logic.
- **`extractor.py` has finance interleaved, not layered.** The scoring function has concept_map, type-matching keywords, and quarterly context embedded in the scoring rules. Separating requires threading a config object through the function.
- **Prompt template split is byte-sensitive.** The prompts were tuned over 109 problems. Any change to whitespace, wording, or ordering during template extraction could change LLM behavior and regress the 78% score.
- **Realistic effort: 5-8 working days**, not 2-3. Step 8 alone (refactoring agent.py) is 1-2 days.

### The post-refactor core IS substantial:
- `core/agent.py` ~200 lines (orchestration skeleton)
- `core/extractor.py` ~250 lines (scoring framework, currency parser, LLM matching)
- `core/calculator.py` ~173 lines (unchanged)
- `core/llm.py` ~145 lines (unchanged)
- `core/state.py` ~100 lines (unchanged)
- `core/eval_harness.py` ~250 lines (judging engine)
- `core/types.py` ~50 lines (shared types)
- **Total: ~1,100-1,200 lines of real framework logic.**

`domains/finance/` absorbs ~2,000 lines (tools, concepts, methodology, parsers, prefetch logic, prompt fragments).

## Open Question Resolutions

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Tool dispatch ownership | **Core dispatches, domain provides callables** | Core handles the Anthropic tool-use protocol uniformly. Domain provides `tool_dispatch: dict[str, Callable]` for prefetch and `execute_tool(name, input)` for ReAct. |
| 2 | Web search | **Core always provides it** | It's a meta-capability. Add `disable_web_search: bool = False` property later if needed. |
| 3 | Skip-ToC heuristic | **Core** | "Skip first match if no digits nearby" is about document navigation, not finance. Markers are domain-supplied. |
| 4 | LLM model routing | **Core, no domain override** | Model choice is cost/quality tradeoff, not domain content. Sonnet/Haiku split is good for all domains. |
| 5 | Naming | **`domains/`** | Standard DDD term. |
| 6 | Error handling | **Log + continue** | Preserves current graceful degradation behavior. |

## Spec Amendments (from audit)

These additions to the Domain ABC are needed based on the audit findings:

1. **Add `execute_tool(name: str, input_data: dict) -> str`** -- The spec's `tool_dispatch` handles prefetch (`FilingRequest` -> `ToolResult`), but the ReAct loop needs `(name, input_dict) -> str`. These are two separate call sites.

2. **Add `classify_tools(tool_log: list[dict]) -> dict[str, list[dict]]`** -- Returns `{"structured": [...], "prose": [...]}`. Replaces hardcoded tool name routing in extraction (agent.py lines 140-146).

3. **Add `context_size_tier(state: dict) -> int`** -- Replaces filing-type-based context tier logic (agent.py lines 80-98) that references "10-K", "10-Q".

4. **Add `pre_extraction_filter(state) -> (state, stash)` and `post_extraction_restore(state, stash) -> state`** -- For guidance key hiding. Called at the exact same points in extraction pipeline.

5. **Keep `_parse_filing_text` in core.** Dollar/percentage parsing from prose is generally useful (FDA docs mention costs too). Move only XBRL and FMP parsers to finance domain.

6. **Do NOT create `core/react.py`** as a separate file. The ReAct loop is already in `llm.py:call_with_tools()`. Only ~15 lines of ReAct-specific logic exist in agent.py (max_turns, tool injection). These stay in agent.py.

## Migration Sequence

### Step 0: Baseline Pin
- Run `python eval.py` on main, save `baseline_fab.json`
- Record per-question pass/fail outcomes
- Commit to repo as the regression target

### Step 1: Create Directory Structure
Create `core/`, `domains/base.py`, `domains/finance/`, `domains/fda/` with `__init__.py` files.
**Verification:** All existing imports still work.

### Step 2: Define Types and ABC
- `core/types.py`: `Fact`, `FilingRequest`, `ToolResult`, `BenchmarkQuestion` dataclasses
- `domains/base.py`: `Domain` ABC with all abstract members (including the 6 amendments above)
**Verification:** Files import cleanly. ABC can be subclassed.

### Step 3: Move Pure Finance Files (mechanical move + import update)
- `src/ticker.py` -> `domains/finance/identifier.py`
- `src/financial_concepts.py` -> `domains/finance/concepts.py`
- `src/financial_methodology.py` -> `domains/finance/methodology.py`
- `src/tools/sec_edgar.py` -> `domains/finance/tools.py`
- `src/tools/fmp.py` -> `domains/finance/fmp.py`
- `src/tools/registry.py` -> `domains/finance/registry.py`
- Update ALL import sites (agent.py, prompts.py, eval.py, tests)
**Verification:** `python -c "from src.agent import run"` works. `pytest tests/` passes.

### Step 4: Move Clean Core Files (mechanical move)
- `src/calculator.py` -> `core/calculator.py`
- `src/llm.py` -> `core/llm.py`
- `src/state.py` -> `core/state.py`
**Verification:** `pytest tests/` passes.

### Step 5: Write FinanceDomain (wiring, not logic)
- `domains/finance/domain.py` implementing Domain ABC
- Wire up: tools, concepts, methodology, parsers, benchmark
- Concept maps and keyword maps moved from extractor.py/agent.py into FinanceDomain properties
**Verification:** `FinanceDomain()` instantiates. All properties return non-empty.

### Step 6: Refactor agent.py -> core/agent.py (HIGHEST RISK)

**Changes:**
- `run()` takes `domain: Domain` param
- `_prefetch_sec_data()` (270 lines) -> `_prefetch_data(state, domain)` (~40 lines): Core iterates `filings_needed`, dispatches via `domain.tool_dispatch[type]`. Keyword fallback moves entirely to FinanceDomain.
- Context size tier logic -> `domain.context_size_tier(state)`
- ReAct tools -> `domain.react_tools + [WEB_SEARCH_TOOL]`, executor -> `domain.execute_tool`
- Extraction routing -> `domain.classify_tools(tool_log)` instead of hardcoded tool names
- Guidance key hiding -> `domain.pre_extraction_filter() / post_extraction_restore()`
- Cross-validation -> `domain.cross_validate(state)`
- `_llm_extract_remaining()` -> domain-supplied extraction hints replace finance-specific prompt text

**Verification:** Finance eval passes with `domain=FinanceDomain()`.

### Step 7: Refactor extractor.py -> core/extractor.py

**Changes:**
- `extract_from_tool_log()` takes `domain` param; uses `domain.classify_tools()` instead of hardcoded tool names
- `_parse_xbrl_output()`, `_parse_fmp_output()` -> `domains/finance/parser.py`
- `_parse_filing_text()` stays in core (dollar/percentage parsing is generally useful)
- `_CONTEXT_KEYWORD_MAP` -> `domain.keyword_map` property on FinanceDomain
- `_match_fact_to_key()` takes `concept_map` and `keyword_map` as params (from domain)
- `llm_match_facts_to_keys()` takes `extraction_hints: str` param (from domain) for prompt customization
- Finance-specific sanity checks (is_pct_key, is_small_key keyword lists) -> domain-supplied

**Verification:** Finance eval passes.

### Step 8: Refactor prompts.py -> Templates (SECOND HIGHEST RISK)

**Changes:**
- Create `core/prompts/planner_template.txt`, `react_template.txt`, `formatter_template.txt`
- Extract structural skeleton (JSON schema, output format, precision rules)
- Move domain content to `domains/finance/prompts.py`: filings_needed docs (65 lines), source selection (12 lines), SEC tool selection (14 lines), beat/miss rules (5 lines), formatting hints (4 lines)
- Rendering: `template.format(**domain.prompt_slots())` produces the final prompt

**Critical mitigation:** Write a test that renders each template with FinanceDomain content and asserts BYTE-IDENTICAL output to the current prompt strings.

**Verification:** Template render diff test passes. Finance eval passes.

### Step 9: Refactor eval.py -> core/eval_harness.py
- Load benchmark from `domain.benchmark` (replaces hardcoded FAB dataset)
- `as_of_date` per-question from `BenchmarkQuestion` (replaces global `BENCHMARK_DATE`)
- Cheap-reeval: domain classifies tools instead of hardcoded names
- Judging logic (mode-of-3, numeric pre-check): unchanged
**Verification:** Finance eval produces identical results to baseline.

### Step 10: Write FDA Stub
- `domains/fda/domain.py` with minimal placeholder content
- One stub tool returning canned `ToolResult` for K-number `K213456`
- One benchmark question: "What is the clearance date for K213456?"
**Verification:** `python main.py --domain fda --question "..."` runs end-to-end.

### Step 11: Write main.py + Domain Registry
- CLI: `--domain {finance|fda}`, `--question`, `--eval`
- `domains/__init__.py` with `DOMAINS` dict and `get_domain()`
**Verification:** `python main.py --list-domains` prints both.

### Step 12: Regression Gate
- Run `python main.py --domain finance --eval`
- Diff against `baseline_fab.json`
- Per-question pass/fail must match exactly
**Verification:** 39/50 (78%), same questions pass/fail.

### Step 13: FDA Stub Smoke Test
- Run `python main.py --domain fda --eval`
- Must return correct answer for canned question
**Verification:** Proves end-to-end plumbing works across two domains.

### Step 14: Lint Gate
- `grep -rn 'ticker\|XBRL\|EDGAR\|10-K\|financial' core/ | grep -v '#'` returns nothing
- Any hit is a finance leak in core

## Highest-Risk Steps and Mitigations

### Risk 1: Prompt Template Split (Step 8)
**What can go wrong:** Rewording during extraction changes LLM behavior, regressing FAB score.
**Mitigation:** Byte-identical render test. Write the test FIRST. Only proceed if template + FinanceDomain content produces exact same prompt strings as current code.

### Risk 2: Agent.py Prefetch Rewrite (Step 6)
**What can go wrong:** The 270-line `_prefetch_sec_data` has complex branching. Generic dispatch may miss edge cases.
**Mitigation:** For 5 questions known to exercise different prefetch paths (XBRL-only, 8-K, multi-company, 10-K section, fallback heuristic), verify identical prefetch results before and after.

### Risk 3: Extractor Tool Routing (Step 7)
**What can go wrong:** Changing how tool results are classified (structured vs prose) changes which extraction layers run.
**Mitigation:** Log extraction layer assignments before and after refactor. Diff must be empty.

### Risk 4: Planner Cache Key (Spec 12.7)
**What can go wrong:** Post-refactor cache key must include domain name. Without it, finance and FDA plans would collide.
**Mitigation:** Change key to `MD5(question + date + domain.name)`. Delete cache before regression eval.

## Verification Plan

1. **Before any code changes:** Run eval, save baseline, commit
2. **After each step:** `pytest tests/` must pass
3. **After steps 6-9:** Run full finance eval, diff against baseline
4. **Step 8 specifically:** Byte-identical prompt render test
5. **Final gate:** Full eval + lint gate + FDA smoke test
