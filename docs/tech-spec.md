# Finance Research Agent — Technical Specification

## Overview

A financial research agent optimized for the Vals AI Finance Agent Benchmark (FAB). The agent uses structured working memory (a persisted state dict) to track multi-step financial reasoning without losing intermediate results. Evaluated against the same benchmark Anthropic reports in their Claude system cards — Claude Sonnet 4.6 scores 63.3%.

## Design Philosophy

**The core insight:** Claude fails on complex financial questions not because it can't reason, but because it doesn't plan before acting and loses track of intermediate results across multiple tool calls. Our agent adds two things: a planning step that identifies all required data points upfront, and a structured state object that persists across turns so nothing gets lost.

**What we're NOT building:** A general-purpose research system with recursive decomposition, belief stores, or assumption tracking. Those features add value for open-ended research but hurt performance on specific financial Q&A. Simplicity is the product.

## Architecture

```
USER QUESTION
    │
    ▼
┌──────────────┐
│   PLANNER     │  Creates state dict: clarifications + data_needed + calculation_steps
│   (Sonnet)    │  Has financial concepts reference for common formulas/definitions
│               │  Resolves ambiguity upfront (period, metric definition, fiscal year)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  TOOL LOOP    │  ReAct loop: read state → find next null → call tool → update state
│  (Sonnet)     │  Tools: web_search, sec_edgar_financials, sec_edgar_filing_text, fmp
│  + STATE      │  Tracks attempts per data point — switches strategy after 2 failures
│               │  Resolves conflicts: prefer SEC filing > institutional > web
│               │  Max 10 turns
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  CALCULATOR   │  Executes calculation_steps as Python code
│  (Code exec)  │  Deterministic — no LLM arithmetic
│               │  Validates results (sanity checks on magnitude)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   ANSWER      │  Formats per FAB conventions
│   (Haiku)     │  2 decimal places, correct units, show work, cite sources
└──────────────┘
```

### Step Details

**1. Planner (1 LLM call)**
- Input: user question + financial concepts reference
- Output: initial state dict
- Model: Sonnet (needs strong financial reasoning to identify the right formula)
- Resolves: what company, what period, what metric definition, what formula, what data points needed

**2. Tool Loop (3-8 LLM calls)**
- Input: current state dict + available tools
- Each turn: agent reads state, identifies next `null` value, calls a tool, updates the state dict
- Agent updates the dict itself (it understands what the tool result means and where it fits)
- Tracks failed attempts per data point — after 2 failures, tries a different approach
- Handles conflicting data: stores multiple values with source attribution, resolves by source quality
- Model: Sonnet (needs tool use + reasoning)
- Exit conditions: all data filled, max turns reached, or agent determines question is unanswerable

**3. Calculator (0 LLM calls)**
- Input: filled state dict with calculation_steps
- Executes each step as Python code using the data values
- Deterministic — LLMs are bad at arithmetic, Python is not
- Validates: checks magnitude (revenue shouldn't be negative), checks units match
- Output: fills in calculated results in the state dict

**4. Answer Formatter (1 LLM call)**
- Input: completed state dict
- Output: formatted answer string with sources
- Model: Haiku (simple formatting task)
- FAB conventions: 2 decimal places, both bounds for ranges, correct units, show calculation

## Persisted State Structure

The state dict is the central data structure. It persists across tool loop turns and serves as compressed context, todo list, and output format all in one.

```python
{
    # What we're solving
    "plan": "Calculate inventory turnover for US Steel FY2024",
    
    # Resolved ambiguities
    "clarifications": {
        "company": "United States Steel Corp, ticker X, CIK 0001163302",
        "period": "FY2024 ending Dec 31, 2024",
        "formula": "Inventory Turnover = COGS / Average Inventory",
        "definitions": {
            "average_inventory": "(inventory_start + inventory_end) / 2",
            "cogs": "Cost of Goods Sold, GAAP basis"
        }
    },
    
    # Data points needed — null means not yet found
    "data_needed": {
        "cogs_2024": {
            "value": null,        # Filled by agent after tool call
            "unit": "USD millions",
            "source": null,       # Source attribution
            "confidence": null,   # "high" (filing) / "medium" (web) / "low" (estimated)
            "attempts": []        # Track failed searches
        },
        "inventory_2023_end": { ... },
        "inventory_2024_end": { ... }
    },
    
    # For cross-company comparisons — nested by entity
    "entities": {
        "META": {
            "revenue": {
                "2022": {"value": 116.6, "unit": "B", "source": "10-K"},
                "2023": {"value": 134.9, "unit": "B", "source": "10-K"},
                "2024": null  # Still needed
            }
        }
    },
    
    # Ordered calculation steps — executed as Python
    "calculation_steps": [
        {
            "step": "avg_inventory",
            "formula": "(inventory_2023_end + inventory_2024_end) / 2",
            "inputs": ["inventory_2023_end", "inventory_2024_end"],
            "result": null  # Filled by calculator
        },
        {
            "step": "turnover",
            "formula": "cogs_2024 / avg_inventory",
            "inputs": ["cogs_2024", "avg_inventory"],
            "result": null
        }
    ],
    
    # Final answer
    "answer": {
        "value": null,
        "formatted": null,   # "6.49" or "Beat by 26.1bps"
        "sources": [],
        "work_shown": null   # "COGS $14,060M / Avg Inventory $2,168M = 6.49"
    }
}
```

### State Design Principles

1. **Nulls are the todo list.** The agent scans for null values and knows exactly what to research next.
2. **Nesting mirrors problem structure.** Cross-company → company → metric → period. Multi-step calc → step → inputs → result.
3. **Every value carries provenance.** Source and confidence travel with the data, not in a separate store.
4. **Attempts prevent infinite loops.** After 2-3 failed searches for the same data point, the agent must try a fundamentally different approach or mark as unavailable.
5. **The completed dict IS the answer.** No separate extraction step — the answer fields are filled as the final state.

## Tools

Reused from the research-agent project with minimal changes:

| Tool | Source | Purpose |
|------|--------|---------|
| `web_search` | Anthropic server-side | General financial data, news, analyst estimates |
| `sec_edgar_financials` | Custom (XBRL API) | Structured financial data: revenue, COGS, inventory, etc. |
| `sec_edgar_filing_text` | Custom (filing HTML) | Read actual 10-K/10-Q text: MD&A, notes, deal terms |
| `sec_edgar_segments` | Custom (XBRL API) | Segment-level breakdowns |
| `fmp_financials` | Financial Modeling Prep API | Quick financial snapshots, ratios, quotes |

### Tool Selection Guidance (in planner prompt)

- Specific financial line items → `sec_edgar_financials` first
- Filing narrative (MD&A, deal terms, risk factors) → `sec_edgar_filing_text`
- Quick company profile or ratio → `fmp_financials`
- Analyst estimates, news, market data → `web_search`
- Always specify period when accessing historical data

## Financial Concepts Reference

Embedded in the planner prompt. Covers:

- **Profitability:** gross margin, operating margin, EBITDA margin, net margin — formulas and which line items
- **Efficiency:** inventory turnover, receivables turnover, asset turnover
- **Leverage:** debt/equity, debt/EBITDA, interest coverage, net debt
- **Growth:** revenue CAGR, EPS growth, same-store sales growth
- **Valuation:** P/E, EV/EBITDA, EV/Revenue, PEG ratio
- **M&A:** M&A firepower (cash + revolver + leverage capacity - net debt)
- **GAAP vs Non-GAAP:** common adjustments (SBC, amortization, restructuring)
- **Guidance comparison:** beat/miss calculation (actual - midpoint of guidance, expressed in bps for margins)

## FAB Benchmark Integration

### Question Categories and Expected Behavior

| Category | Planner Behavior | Expected Turns | Notes |
|----------|-----------------|---------------|-------|
| Quantitative Retrieval | 1 data point needed | 1-2 | Simplest case |
| Qualitative Retrieval | No calculation, just find info | 1-2 | May skip calculator |
| Beat or Miss | Need guidance + actual, compare | 3-5 | Two lookups + comparison |
| Numerical Reasoning | Formula identification + data + calc | 3-6 | Calculator step critical |
| GAAP vs Non-GAAP | Find reconciliation table | 2-4 | SEC filing text tool |
| Trends | Multi-period data collection | 4-8 | Scratchpad prevents data loss |
| Complex Numerical | Multi-step formulas | 5-8 | Planner quality is key |
| Cross-company | Matrix structure | 6-10 | Entity nesting critical |
| Market Analysis | Open-ended synthesis | 4-8 | Closest to research agent |

### Cost and Time Targets

- Per question: $0.05-0.30, 30-120 seconds
- Full 50-question validation set: $5-15, 1-2 hours
- Target score: 65%+ (above Claude Sonnet 63.3% baseline)

## File Structure

```
Finance Research Agent/
├── docs/
│   ├── tech-spec.md          # This file
│   ├── problems.md           # Accumulated problems and solutions
│   └── changelog.md          # All changes and reasoning
├── src/
│   ├── agent.py              # Main agent: planner + tool loop + calculator + answer
│   ├── state.py              # State dict schema and helpers
│   ├── planner.py            # Planner prompt and state initialization
│   ├── tools/                # Reused from research-agent
│   │   ├── sec_edgar.py
│   │   ├── fmp.py
│   │   └── registry.py
│   ├── calculator.py         # Python code execution for calculations
│   ├── prompts.py            # All prompt templates
│   └── financial_concepts.py # Financial formulas reference
├── tests/
│   ├── test_state.py         # State dict manipulation
│   ├── test_calculator.py    # Calculation execution
│   ├── test_planner.py       # Plan generation (mocked)
│   └── test_e2e.py           # End-to-end with recorded fixtures
├── evaluation/
│   ├── run_fab.py            # Run against FAB dataset
│   ├── score_fab.py          # Score results
│   └── results/              # Saved outputs
├── .env
└── pyproject.toml
```
