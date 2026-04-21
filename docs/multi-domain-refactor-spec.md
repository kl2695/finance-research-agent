# Tech Spec: Multi-Domain Research Agent Refactor

**Status:** Draft for plan-mode review
**Author:** Kevin
**Target branch:** `refactor/multi-domain` (off `main`)
**Related docs:** `architecture.md` (current finance agent architecture)

---

## 1. Context

The finance research agent is currently a single-purpose system tightly coupled to SEC EDGAR filings. It scores 78% on FAB (vs. Opus 4.7 at 64%) and has a clean pipeline: planner → prefetch → ReAct → 4-layer extraction → deterministic calculator → formatter.

We want to generalize this pipeline into a **domain-agnostic research runtime** with pluggable domain modules. Initial second domain: FDA regulatory QA (510(k) database, openFDA APIs, ClinicalTrials.gov). The FDA domain itself is **out of scope** for this refactor — we'll ship a stub implementation to prove the interface holds, and build the real FDA domain in a follow-up.

The user-visible API becomes:

```bash
python main.py --domain finance --question "What was Lyft's Q4 2024 revenue?"
python main.py --domain fda --question "Median 510(k) clearance time for product code DRG in 2024?"
```

---

## 2. Goals

1. Extract a domain-agnostic core from the existing finance agent into `core/`.
2. Define a `Domain` interface (ABC) that encapsulates all domain-specific logic.
3. Move all finance-specific logic into `domains/finance/` implementing the interface.
4. Ship a stub `domains/fda/` implementing the interface with placeholder content (canned responses, minimal concept map, empty benchmark) to prove the abstraction works across two domains.
5. Add `--domain {finance|fda}` CLI flag selecting the active domain at runtime.
6. **Preserve finance behavior exactly**: post-refactor FAB score must match pre-refactor (78%, 39/50).

## 3. Non-goals

- Build real FDA tools, prompts, methodology, or benchmark. Stub only.
- Change any algorithmic behavior — planner caching, ReAct mechanics, 4-layer extraction, mode-of-3 judging, numeric pre-check all stay identical.
- Support dynamic plugin discovery (setuptools entry points, etc.). A static registry is sufficient for 2 domains.
- Over-generalize date handling. If calendar/fiscal-year logic can stay inside the finance domain module, keep it there. We only generalize what the stub FDA domain actually forces.
- Refactor the extractor's internal algorithms. Only its domain-facing boundary changes.

## 4. Current Architecture (as-is)

```
finance_agent/
├── agent.py                    # Orchestrator: run() → plan/prefetch/ReAct/extract/calc/format
├── extractor.py                # 4-layer extraction: XBRL → regex → Haiku → Sonnet
├── calculator.py               # Python eval of formulas from state dict
├── ticker.py                   # Ticker extraction with 4 strategies, excluded words
├── llm.py                      # Anthropic API client, prompt caching, rate limits
├── prompts.py                  # Planner, ReAct, formatter prompt templates
├── financial_methodology.py    # CAGR, turnover, beat/miss conventions
├── financial_concepts.py       # XBRL concept reference for planner
├── eval.py                     # FAB eval harness, mode-of-3, numeric pre-check
└── tools/
    └── sec_edgar.py            # get_company_facts, get_filing_text, get_earnings_press_release
```

## 5. Target Architecture

```
research_agent/
├── main.py                     # CLI entry: --domain, --question, --eval
├── core/
│   ├── __init__.py
│   ├── agent.py                # Orchestrator (domain-agnostic)
│   ├── extractor.py            # 4-layer extraction (domain-agnostic algorithm)
│   ├── calculator.py           # Unchanged
│   ├── react.py                # ReAct loop mechanics (extracted from agent.py)
│   ├── llm.py                  # Unchanged
│   ├── eval_harness.py         # mode-of-3, numeric pre-check (domain-agnostic)
│   └── types.py                # Fact, BenchmarkQuestion, FilingRequest, ToolResult
├── domains/
│   ├── __init__.py             # Registry: DOMAINS = {"finance": ..., "fda": ...}
│   ├── base.py                 # Domain ABC + shared types
│   ├── finance/
│   │   ├── __init__.py         # exports FinanceDomain
│   │   ├── domain.py           # FinanceDomain(Domain) — wires everything together
│   │   ├── tools.py            # Moved from tools/sec_edgar.py (+ registered in tool_dispatch)
│   │   ├── concepts.py         # Moved from financial_concepts.py (concept_map, markers)
│   │   ├── prompts.py          # Finance-specific planner/ReAct/formatter fragments
│   │   ├── methodology.py      # Moved from financial_methodology.py
│   │   ├── parser.py           # XBRL output parser (moved from extractor.py)
│   │   ├── identifier.py       # Moved from ticker.py
│   │   └── benchmark.py        # FAB questions + rubrics
│   └── fda/
│       ├── __init__.py         # exports FDADomain
│       ├── domain.py           # FDADomain(Domain) — stub with placeholder content
│       ├── tools.py            # Stub: canned responses for one filing type
│       ├── concepts.py         # Minimal concept map (2-3 entries)
│       ├── prompts.py          # Minimal placeholder prompts
│       ├── methodology.py      # Empty string stub
│       ├── parser.py           # Stub JSON parser
│       ├── identifier.py       # K-number regex (K + 6 digits)
│       └── benchmark.py        # Empty list (no benchmark yet)
└── results/
    └── planner_cache.json      # Unchanged location
```

## 6. Domain Interface

Defined in `domains/base.py`. Every domain implements this ABC.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional, Any

# --- Shared types (also defined in core/types.py, re-exported here) ---

@dataclass
class Fact:
    """Parsed fact from a domain tool output. Used by extraction layers 3a/3b."""
    concept: str                                    # e.g., "Revenues", "clearance_date"
    value: float | str                              # precise number OR raw string for qualitative
    unit: Optional[str] = None                      # "USD", "days", "count", "percent", None
    period: Optional[tuple[date, date]] = None      # (start, end) inclusive
    source_ref: Optional[str] = None                # "10-K 2024 item 7" / "510(k) K213456"
    metadata: dict = field(default_factory=dict)

@dataclass
class FilingRequest:
    """One entry in the planner's filings_needed list. Dispatched by prefetch."""
    type: str                                       # "xbrl" | "10-K" | "510k" | "maude" | ...
    identifier: Optional[str] = None                # override default_identifier
    period: Optional[str] = None                    # "Q4 2024", "2024", "2020-2024"
    section: Optional[str] = None                   # "tax", "risk", "substantial_equivalence"
    concepts: Optional[list[str]] = None            # for XBRL-style lookups
    reason: Optional[str] = None                    # planner's rationale (debug only)
    extra: dict = field(default_factory=dict)       # domain-specific escape hatch

@dataclass
class ToolResult:
    """Uniform return type from every domain fetcher."""
    raw: str                                        # raw text/JSON output (what went to ReAct prompt)
    facts: list[Fact]                               # pre-parsed facts (layer 3a feeds from here)
    tool_name: str                                  # "xbrl", "510k", etc.
    success: bool = True
    error: Optional[str] = None

@dataclass
class BenchmarkQuestion:
    id: str
    question: str
    as_of_date: str                                 # ISO date — overrides planner's date context
    rubric: list[str]                               # criteria strings for mode-of-3 judging
    expected_answer: Optional[str] = None           # human-readable ground truth
    tags: list[str] = field(default_factory=list)   # "qualitative", "multi-company", etc.


# --- Domain ABC ---

class Domain(ABC):
    """Contract for a research domain. Implementations: FinanceDomain, FDADomain."""

    # ----- Identity -----
    @property
    @abstractmethod
    def name(self) -> str: ...

    # ----- Planner inputs (all injected into the core planner prompt template) -----
    @property
    @abstractmethod
    def filing_types_reference(self) -> str:
        """Markdown bullet list of valid `type` values for FilingRequest,
        with when to use each. Injected into planner prompt."""

    @property
    @abstractmethod
    def concept_reference(self) -> str:
        """Markdown reference of common concepts/fields the planner should know about.
        Finance: XBRL concept names grouped by category.
        FDA: openFDA field names grouped by database."""

    @property
    @abstractmethod
    def methodology(self) -> str:
        """Domain-specific calculation conventions, period rules, procedures.
        Injected into planner prompt. Finance: CAGR formula, beat/miss rules.
        FDA: clearance-time definition, adverse-event rate formula."""

    @property
    @abstractmethod
    def planner_examples(self) -> list[dict]:
        """Few-shot examples for planner. Each dict contains keys:
        {question, data_needed, filings_needed, calculation_steps, clarifications}."""

    # ----- Prefetch / tools -----
    @property
    @abstractmethod
    def tool_dispatch(self) -> dict[str, Callable[[FilingRequest, str], ToolResult]]:
        """Map from FilingRequest.type string → fetcher callable.
        Fetcher signature: (entry: FilingRequest, default_identifier: str) -> ToolResult.
        Core prefetch iterates filings_needed and dispatches by entry.type."""

    # ----- ReAct -----
    @property
    @abstractmethod
    def react_examples(self) -> list[str]:
        """Worked research patterns injected into the ReAct system prompt.
        Finance currently has 3: TJX beat/miss, KO peer comparison, Shift4 deep-section."""

    @property
    @abstractmethod
    def react_tools(self) -> list[dict]:
        """Tool schemas (Anthropic tool-use format) available to ReAct for additional
        calls beyond prefetched data. Web search is added by core — domain provides
        its own tools (e.g., get_filing_text for finance)."""

    # ----- Extraction (layers 3a + 3b) -----
    @abstractmethod
    def parse_structured_output(self, tool_result: ToolResult) -> list[Fact]:
        """Layer 3a: parse domain-specific tool output into Facts.
        Finance: parse XBRL text format 'ConceptName (USD): value (period: ... filed: ...)'.
        FDA: parse openFDA JSON responses into Facts."""

    @property
    @abstractmethod
    def concept_map(self) -> dict[str, list[str]]:
        """Layer 3a/3b matching: keyword in data_needed key → concept name candidates.
        Order = preference. E.g., 'revenue' → ['Revenues', 'RevenueFromContractWithCustomer...']."""

    @property
    @abstractmethod
    def section_markers(self) -> dict[str, list[str]]:
        """Section name → text markers for jumping within long filings.
        Markers tried in order; first match wins (with skip-ToC heuristic from core)."""

    # ----- Cross-validation (step 3.5) -----
    @abstractmethod
    def cross_validate(self, state: dict) -> list[str]:
        """Domain-specific sanity checks. Returns human-readable warnings.
        Finance: detect duplicate values across keys, absurd ratios.
        FDA stub: return []."""

    # ----- Identifier handling -----
    @property
    @abstractmethod
    def identifier_regex(self) -> str:
        """Regex for finding the primary entity identifier in planner output/question.
        Finance: stock ticker. FDA: K-number (K\\d{6}) or NCT ID (NCT\\d{8})."""

    @property
    @abstractmethod
    def identifier_exclusions(self) -> set[str]:
        """Words that look like identifiers but aren't (e.g., 'USA', 'GAAP', 'FDA')."""

    def normalize_identifier(self, raw: str) -> str:
        """Default: strip + uppercase. Override if domain needs different behavior."""
        return raw.strip().upper()

    # ----- Formatter -----
    @property
    def formatter_hints(self) -> str:
        """Optional extra context injected into formatter prompt (units, precision, convention)."""
        return ""

    # ----- Benchmark -----
    @property
    @abstractmethod
    def benchmark(self) -> list[BenchmarkQuestion]:
        """Benchmark questions for eval harness. FDA stub returns []."""
```

## 7. Domain Registry

`domains/__init__.py`:

```python
from .finance import FinanceDomain
from .fda import FDADomain
from .base import Domain

DOMAINS: dict[str, Domain] = {
    "finance": FinanceDomain(),
    "fda": FDADomain(),
}

def get_domain(name: str) -> Domain:
    if name not in DOMAINS:
        raise ValueError(
            f"Unknown domain: {name!r}. Available: {list(DOMAINS.keys())}"
        )
    return DOMAINS[name]

def list_domains() -> list[str]:
    return list(DOMAINS.keys())
```

## 8. Core Pipeline Changes

Each stage's before/after.

### 8.1 `core/agent.py` (was `agent.py`)

**Before:** `run(question, as_of_date=None)` — hardcoded finance pipeline.

**After:** `run(question, domain: Domain, as_of_date=None)`. The orchestrator:
- Loads planner prompt template from `core/prompts/planner_template.txt` (see §9).
- Renders template with `domain.filing_types_reference`, `domain.concept_reference`, `domain.methodology`, `domain.planner_examples`.
- Calls LLM for plan. Caches by `MD5(question + as_of_date + domain.name)` — note domain in hash.
- Prefetch: iterates `plan.filings_needed`, dispatches each via `domain.tool_dispatch[entry.type](entry, default_id)`.
- ReAct: uses `domain.react_examples`, `domain.react_tools`, plus core's web_search.
- Extraction: calls `core.extractor.extract(tool_results, state, domain)`.
- Cross-validation: `domain.cross_validate(state)`.
- Calculator: unchanged.
- Formatter: renders formatter template with `domain.formatter_hints`.

### 8.2 `core/extractor.py`

**4 layers stay, but:**
- Layer 3a now calls `domain.parse_structured_output(tool_result)` instead of hardcoded XBRL regex.
- Layer 3b (keyword scoring on structured text) uses `domain.concept_map` and `domain.section_markers`.
- Layers 3c (Haiku fact matching) and 3d (Sonnet raw extraction) are already domain-agnostic — they just use domain's concept_map to know what keys mean.
- The "no overwrites" rule is a core invariant and stays in core.

### 8.3 `core/react.py` (new file)

Extracted from `agent.py`'s ReAct block. Takes `domain.react_examples`, `domain.react_tools`, plus core-provided tools (web search). Max-turns scaling stays in core (based on `len(filings_needed)`).

### 8.4 `core/eval_harness.py` (was `eval.py`)

- Loads benchmark from `domain.benchmark` (replaces hardcoded FAB path).
- Mode-of-3 judging: unchanged.
- Numeric pre-check: unchanged (generic regex on numeric tokens).
- `as_of_date` comes per-question from `BenchmarkQuestion.as_of_date`, not a global constant.
- `--cheap-reeval` mode: unchanged.

### 8.5 `core/types.py`

New file holding `Fact`, `FilingRequest`, `ToolResult`, `BenchmarkQuestion` dataclasses. Imported by both core and domains. Single source of truth for shared shapes.

## 9. Prompt Template Strategy

The planner, ReAct, and formatter prompts are the **highest-risk domain-coupling surface**. They are rewritten as templates with explicit slots.

### Planner template (`core/prompts/planner_template.txt`)

```
You are a research planner for the {domain_name} domain.

Your job is to decompose a research question into:
  1. data_needed — specific values/facts to find
  2. filings_needed — which sources to fetch (types listed below)
  3. calculation_steps — formulas to compute the answer
  4. clarifications — assumptions about the question (entity, period, methodology)

Today's date: {as_of_date}

-- AVAILABLE SOURCE TYPES --
{filing_types_reference}

-- COMMON CONCEPTS --
{concept_reference}

-- METHODOLOGY --
{methodology}

-- EXAMPLES --
{planner_examples_rendered}

-- QUESTION --
{question}

Respond in JSON with keys: data_needed, filings_needed, calculation_steps, clarifications.
```

**Review rule:** read the template without the `{...}` slots filled in. If any sentence mentions SEC, EDGAR, XBRL, ticker, filing, 10-K, or any other finance concept — that's a leak. Fix before merge.

### ReAct template

Same pattern. Domain contributes `react_examples` (worked patterns) and `react_tools` (tool schemas). Core contributes web search tool and max-turns logic.

### Formatter template

Mostly domain-neutral. Domain contributes `formatter_hints` (units, precision conventions).

## 10. CLI Design

`main.py`:

```python
import argparse
from core.agent import run
from core.eval_harness import run_benchmark
from domains import get_domain, list_domains

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--domain", choices=list_domains(),
                   default=os.environ.get("RESEARCH_DOMAIN", "finance"))
    p.add_argument("--question", type=str)
    p.add_argument("--as-of-date", type=str, default=None)
    p.add_argument("--eval", action="store_true")
    p.add_argument("--indices", type=int, nargs="*", default=None)
    p.add_argument("--cheap-reeval", type=str, default=None)
    p.add_argument("--list-domains", action="store_true")
    args = p.parse_args()

    if args.list_domains:
        for d in list_domains(): print(d)
        return

    domain = get_domain(args.domain)
    if args.eval:
        run_benchmark(domain, indices=args.indices, cheap_reeval=args.cheap_reeval)
    else:
        result = run(args.question, domain=domain, as_of_date=args.as_of_date)
        print(result)
```

## 11. Migration Plan

Ordered so finance keeps working at every checkpoint. Each step is independently committable.

| # | Step | Verification |
|---|------|--------------|
| 0 | **Baseline pin**: run `python eval.py` on main, save results JSON to `baseline_fab.json`. | Record FAB score and per-question outcomes. |
| 1 | **Create directory structure** (`core/`, `domains/base.py`, `domains/finance/`, `domains/fda/`) without moving code yet. Add empty `__init__.py` files. | Imports don't break. |
| 2 | **Move shared types** (`Fact`, `FilingRequest`, `ToolResult`, `BenchmarkQuestion`) into `core/types.py`. Update all import sites. | Finance eval still passes. |
| 3 | **Define `Domain` ABC** in `domains/base.py`. No implementations yet. | File imports cleanly. |
| 4 | **Move finance files into `domains/finance/`**: sec_edgar → tools.py, financial_concepts → concepts.py, financial_methodology → methodology.py, ticker → identifier.py. Update imports. | Finance eval still passes. |
| 5 | **Extract XBRL parser** from `extractor.py` into `domains/finance/parser.py`. Keep calling it from extractor for now (not yet going through Domain interface). | Finance eval still passes. |
| 6 | **Write `FinanceDomain`** in `domains/finance/domain.py` implementing the ABC. Wires up tools, concepts, methodology, parser, benchmark. | `FinanceDomain()` instantiates without error. |
| 7 | **Rewrite prompt templates**: extract `core/prompts/planner_template.txt`, `react_template.txt`, `formatter_template.txt` with `{slot}` placeholders. Move domain-specific content into `domains/finance/prompts.py`. | Run template rendering on its own — manual diff vs. original prompt to confirm semantic equivalence. |
| 8 | **Refactor `agent.py` → `core/agent.py`**: take `domain: Domain` param, use domain throughout. | Finance eval passes with `domain=FinanceDomain()`. |
| 9 | **Refactor `extractor.py` → `core/extractor.py`**: layer 3a calls `domain.parse_structured_output`, 3b uses `domain.concept_map` and `domain.section_markers`. | Finance eval passes. |
| 10 | **Refactor `eval.py` → `core/eval_harness.py`**: load benchmark from `domain.benchmark`, per-question `as_of_date`. | Finance eval passes — same score as baseline. |
| 11 | **Write `FDADomain` stub** in `domains/fda/domain.py`. Minimal placeholder content. One stub tool that returns canned data for one test question. | `python main.py --domain fda --question "..."` runs end-to-end without crashing. |
| 12 | **Write `main.py`** with CLI. Wire up domain registry. | `python main.py --list-domains` prints both. |
| 13 | **Regression gate**: re-run `python main.py --domain finance --eval`. Compare to `baseline_fab.json`. | Scores and per-question outcomes must match exactly. |
| 14 | **Smoke test FDA stub**: `python main.py --domain fda --eval` with stub benchmark of 1 question. | Returns an answer (even if canned). Proves the interface works end-to-end. |

## 12. Known Vulnerabilities (focus of plan mode)

These are where the refactor is most likely to produce subtle breakage or leaky abstractions. Plan mode should explicitly probe each.

### 12.1 Hidden finance assumptions in "generic" code

Pre-refactor audit: grep the to-be-core files for these strings. Each hit is either a legitimate move-to-domain or a leak to fix.

```
ticker | USD | fiscal | calendar | XBRL | 10-K | 10-Q | 8-K | SEC | EDGAR | CIK | FY\d | Q[1-4]
```

**Specific suspects** from `architecture.md`:
- `agent.py`: likely calls `ticker.py` directly. Move to `domain.normalize_identifier`.
- Period parsing: "Q4 2024" format is finance-idiomatic. If core parses periods, it's a leak. Decision: **periods stay domain-parsed**, core just passes the string through in `FilingRequest.period`.
- `_parse_xbrl_output` in `extractor.py`: clearly finance. Move to `domains/finance/parser.py`.
- `eval.py` hardcoded `BENCHMARK_DATE = "2025-02-01"`: replace with per-question date on `BenchmarkQuestion`.
- Currency regex in structured-text layer 3b: if core has `r'\$[\d,]+'` anywhere, that's finance. Move to domain's parser.

### 12.2 Planner prompt leak

The planner prompt has the most domain-coupled content after the tools themselves. High risk of leaving finance phrasing in what should be a domain-neutral template.

**Mitigation:** after extraction, read `planner_template.txt` end-to-end with `{slot}` placeholders visible. Any reference to a specific filing type, concept name, company identifier, or methodology = leak.

**Plan mode probe:** "Read `planner_template.txt`. Flag every sentence that refers to a concrete finance concept rather than an abstract research concept."

### 12.3 `Fact` dataclass fit for FDA

Finance facts: numeric values with USD currency and quarterly/annual periods. FDA facts: dates (clearance date), counts (adverse event count), classifications (device class I/II/III), categorical strings (product code, applicant name).

**Question to resolve:** is `value: float | str` sufficient, or do we need typed variants? Current proposal: **yes, str fallback is fine**. Rationale: the calculator Python-evals formulas from the state dict; if a value is a string, arithmetic fails loudly. Typed variants add complexity without solving a real problem yet.

**Plan mode probe:** "Walk through the 3 most common FDA question types. For each, write out what `Fact` instances would need to hold. Does the current `Fact` shape work?"

### 12.4 Cross-validation unit awareness

Finance's absurd-ratio check assumes dollar values. FDA's equivalent (if any) would be different — e.g., "clearance time > 5 years is absurd" or "adverse event count > total device shipments is impossible."

**Mitigation:** cross-validation is fully domain-owned via `domain.cross_validate(state) -> list[str]`. No shared logic in core.

**Plan mode probe:** "Is there any cross-validation logic in core that uses unit-specific thresholds? If so, move to domain."

### 12.5 Discover-then-plan vs. plan-then-fetch

Current architecture assumes the planner can enumerate `filings_needed` upfront. Some FDA questions require discovery first (e.g., "Which product codes map to interventional cardiology?" before you can list the 510(k)s to fetch).

**Decision for this refactor:** out of scope. Current ReAct loop can handle some discovery via additional tool calls. If FDA build reveals this is a hard limit, address in a follow-up architecture change.

**Plan mode probe:** "Given the current `filings_needed` → prefetch flow, what FDA questions would this architecture fail on? List 3."

### 12.6 Concept-name substring matching

Finance's layer 3a does `if metric.lower() in concept_name.lower()` — known to cause false matches (P42: "tax" matches "pretax"). This is a pre-existing bug, but the refactor is a reasonable time to fix it.

**Decision:** leave as-is for this refactor (preserving exact finance behavior). File a follow-up ticket: "Tighten concept-name matching to word boundaries or exact match."

### 12.7 Planner cache key

Current cache key: `MD5(question + date)`. Post-refactor must be: `MD5(question + date + domain_name)`. Otherwise running FDA and finance on the same question would collide.

**Verification:** delete `results/planner_cache.json` before running the regression eval in step 13.

### 12.8 Prompts.py is a hotspot

Contains planner, ReAct, formatter prompts — and likely finance methodology mixed into each. Needs careful dissection. The risk is pulling out the "template" part but leaving finance phrasing embedded in what looks like structural content.

**Plan mode probe:** "Show me the current `prompts.py`. For each of the three prompts, identify which lines are structural (go to core template) vs. domain-specific (go to `domains/finance/prompts.py`)."

## 13. Testing & Regression Strategy

### 13.1 Exact-match regression test

After step 13 of the migration plan:

```bash
# Before refactor (on main, baseline)
python eval.py > baseline_fab.txt

# After refactor (on refactor/multi-domain)
python main.py --domain finance --eval > refactored_fab.txt

# Must match exactly
diff baseline_fab.txt refactored_fab.txt
```

If the diff is non-empty, the refactor changed behavior. Not acceptable for merge.

### 13.2 Cheap-reeval sanity check

`--cheap-reeval` replays extraction + calculator + formatter on saved tool_logs. Post-refactor, cheap-reeval of a pre-refactor run must produce identical answers. This specifically validates that layers 3a–3d weren't subtly changed.

### 13.3 FDA stub smoke test

`domains/fda/` contains one stub tool (e.g., a `fetch_510k_stub` that returns a canned `ToolResult` for K-number `K213456`). One stub benchmark question: "What is the clearance date for K213456?" with answer baked into the stub. 

```bash
python main.py --domain fda --eval
```

Must return a correct answer end-to-end. This proves:
- Domain registry works
- Domain selection plumbing works
- Planner template renders with FDA content
- Prefetch dispatches to FDA tools
- Extraction pulls Facts from FDA ToolResult
- Calculator + formatter pass through
- Eval harness loads FDA benchmark

If this passes, the abstraction is validated across two domains. If it doesn't, the interface has a real leak.

### 13.4 Lint gate

Pre-merge: `grep -rn 'ticker\|XBRL\|EDGAR\|10-K\|financial' core/ | grep -v '#'` returns nothing. Any hit is a finance leak in core.

## 14. Open Questions (for plan mode)

1. **Tool schemas**: ReAct's `tools` list (for Anthropic tool-use) needs dispatching. Who owns the dispatcher — core (generic handler that calls `domain.tool_dispatch[name]`) or domain (domain provides its own dispatcher)? Proposal: core, with domain providing just the callables.

2. **Web search**: currently invoked from ReAct. Does it stay in core as a universally-available tool, or does each domain declare whether it wants web search? Proposal: core always provides it — regulated domains can have a `disable_web_search: bool` property if needed later.

3. **Section marker skip-ToC heuristic**: currently in `extractor.py`. Is this logic (skip first match if no digits nearby) domain-agnostic or finance-specific? Proposal: domain-agnostic, stays in core. Markers are domain-supplied.

4. **LLM model routing**: `llm.py` currently has hardcoded Sonnet-for-reasoning, Haiku-for-formatting. Should domains be able to override? Proposal: no — model choice is a core concern, not a domain one. Kick down the road.

5. **Naming**: `domains/` or `verticals/` or `packs/`? Proposal: `domains/`. "Domain" is the term used throughout this spec and is standard in ML/AI contexts.

6. **Error handling**: when a domain fetcher raises, does the pipeline halt or continue with partial data? Current finance behavior: log + continue. Preserve this.

## 15. Success Criteria

Merge-ready when **all** of the following hold:

- [ ] `python main.py --domain finance --eval` produces identical output to pre-refactor baseline (step 13 regression gate passes).
- [ ] `python main.py --domain fda --eval` (stub benchmark) returns a correct answer end-to-end.
- [ ] `grep` lint gate (§13.4) returns no hits.
- [ ] Planner template (§9) has no finance-specific words outside `{slot}` placeholders.
- [ ] All unit tests pass. (If the existing repo has tests — unclear from `architecture.md`. If not, add one integration test per stage.)
- [ ] `cheap-reeval` of a pre-refactor tool_log produces the same final answer it did pre-refactor.

## 16. Rollout

1. Develop on `refactor/multi-domain` branch off `main`.
2. Plan mode review against this spec before any code change.
3. Migration proceeds commit-by-commit per §11. Each commit verified against finance eval.
4. Open PR only after §15 criteria all hold.
5. Merge strategy: squash to a single commit. Rationale: the intermediate commits reference old paths and will be confusing in `git log` six months later; the refactor is conceptually one change.

---

## Appendix A: Imports to sanity-check

Files likely needing import updates (non-exhaustive, confirm via grep):

- Anything importing from `tools.sec_edgar` → `domains.finance.tools`
- Anything importing from `financial_concepts` or `financial_methodology` → `domains.finance.concepts` / `domains.finance.methodology`
- Anything importing from `ticker` → `domains.finance.identifier`
- Test files, eval scripts, any notebooks in the repo
