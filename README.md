# Multi-Domain Research Agent

A domain-agnostic AI agent that answers precise research questions using structured public databases. Pluggable domain modules handle domain-specific tools, prompts, extraction, and benchmarks. The core pipeline (planner → prefetch → ReAct → extraction → calculator → formatter) is shared across all domains.

## Domains

### Finance (SEC EDGAR)
Answers quantitative financial questions using SEC filings. Scores **84% on the Vals AI Finance Agent Benchmark** — 20 points above the leaderboard leader (Claude Opus 4.7 at 64%).

### FDA Regulatory (openFDA)
Answers regulatory QA questions about medical devices using openFDA APIs and AccessData PDF scraping. Scores **90% on 510kQA** — a novel benchmark of 30 regulatory questions we built (no public benchmark existed).

## Results

| Benchmark | Domain | Score | Leaderboard #1 | Bare Claude Sonnet |
|-----------|--------|-------|-----------------|-------------------|
| FAB (Vals AI) | Finance | **84%** (42/50) | 64% (Opus 4.7) | 63% (Sonnet 4.6) |
| 510kQA (novel) | FDA | **90%** (27/30) | N/A (first benchmark) | N/A |

Built by one person. Documented via 103 problems encountered and solved, 15 agent design principles, and full architecture docs for both domains.

## Architecture

```
research_agent/
├── core/                    # Domain-agnostic pipeline
│   ├── agent.py             # Orchestrator: plan → prefetch → ReAct → extract → calc → format
│   ├── extractor.py         # Multi-layer extraction framework
│   ├── calculator.py        # Deterministic Python formula evaluation
│   ├── llm.py               # Anthropic API client with cost tracking
│   └── types.py             # Shared types: Fact, FilingRequest, ToolResult, BenchmarkQuestion
├── domains/
│   ├── base.py              # Domain ABC — contract for all domains
│   ├── finance/             # SEC EDGAR, XBRL, earnings press releases
│   │   ├── domain.py        # FinanceDomain implementation
│   │   ├── tools.py         # SEC EDGAR API client
│   │   ├── concepts.py      # Financial concepts (CAGR, margins, ratios)
│   │   └── methodology.py   # Financial methodology (beat/miss, turnover)
│   └── fda/                 # openFDA, MAUDE, AccessData PDFs
│       ├── domain.py        # FDADomain implementation
│       ├── tools.py         # openFDA API client + PDF scraper
│       ├── concepts.py      # Regulatory concepts (510(k), PMA, device classes)
│       ├── methodology.py   # Regulatory methodology (clearance timelines, predicates)
│       └── benchmark/       # 510kQA benchmark (30 questions)
├── main.py                  # CLI: --domain {finance|fda} --question "..." --eval
└── eval.py                  # Finance eval harness (FAB benchmark)
```

### Core Pipeline

```
Question → Planner (LLM) → Prefetch (domain tools) → ReAct Agent → Extraction → Calculator → Answer
```

**Key design decisions:**

- **Domain ABC** — Every domain implements a single interface (~15 methods): prompts, tool dispatch, concept maps, extraction config, cross-validation, benchmark. Core pipeline depends only on this interface.

- **Structured source selection** — The LLM planner outputs a `filings_needed` list specifying exactly which data sources to fetch. The code dispatches to the domain's tool_dispatch map. No hardcoded source selection in core.

- **Multi-layer extraction** — Structured data exact matching → regex text parsing → LLM fact matching (Haiku) → LLM raw extraction (Sonnet). Each layer fills what the previous couldn't. Domains configure which layers run and what concept maps to use.

- **Programmatic over probabilistic** — For data with deterministic structure (XBRL, JSON APIs, counts), extract with code — not an LLM. LLMs round. `$4,278.9 million` becomes `$4.3 billion` when an LLM summarizes. Code doesn't round.

- **Every value carries provenance** — Answers cite specific database records (K-numbers, SEC filing accession numbers, MAUDE report IDs). The reviewer can verify any claim.

## Usage

```bash
# Clone and install
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=your-key" > .env

# Run a finance question
python main.py --domain finance --question "Calculate the inventory turnover for US Steel in FY2024."

# Run an FDA question
python main.py --domain fda --question "What is the clearance date for 510(k) K213456?"

# Run benchmarks
python main.py --domain finance --eval
python main.py --domain fda --eval

# List available domains
python main.py --list-domains
```

## Question Types

### Finance (FAB Benchmark)

| Type | Accuracy | Example |
|------|----------|---------|
| Numerical Reasoning | 88% | "Calculate the 3-year revenue CAGR for Palantir" |
| Quantitative Retrieval | 89% | "What was FND same-store sales growth in Q4 2024?" |
| Beat or Miss | 71% | "How did Lyft's Q4 EBITDA margin compare to guidance?" |
| Qualitative Retrieval | 67% | "Summarize regulatory risks from Paylocity's 10-K" |
| Complex Retrieval | 100% | "Of AMZN, META, GOOG — who plans most capex in 2025?" |

### FDA (510kQA Benchmark)

| Type | Accuracy | Example |
|------|----------|---------|
| Predicate Lookup | 100% | "List all predicate devices cited in 510(k) K213456" |
| Adverse Event Synthesis | 100% | "Count MAUDE death events for HeartMate 3 devices" |
| Clearance Timeline | 86% | "Median clearance time for product code MAX, 2022-2024" |
| Classification Reasoning | 80% | "What device class and pathway for product code DSQ?" |
| Multi-Source Reasoning | 83% | "For product code LWS, list clearances + recalls since 2022" |

## Tech Stack

- **LLM**: Claude Sonnet 4.6 (reasoning) + Haiku 4.5 (formatting, judging)
- **Finance data**: SEC EDGAR XBRL API, filing text, earnings press releases
- **FDA data**: openFDA device APIs (510(k), MAUDE, recalls, classification), AccessData PDFs
- **Language**: Python 3.13
- **Evaluation**: Mode-of-3 LLM judging, deterministic numeric pre-check, API cost tracking

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — Full pipeline architecture, domain interface, extraction flow
- [`docs/fda-product-spec.md`](docs/fda-product-spec.md) — FDA domain product specification
- [`docs/fda-tech-spec.md`](docs/fda-tech-spec.md) — FDA domain technical specification
- [`docs/fda-eval-results.md`](docs/fda-eval-results.md) — 510kQA benchmark results (90%)
- [`docs/problems.md`](docs/problems.md) — 103 problems encountered, each with root cause and solution
- [`docs/agent_principles.md`](docs/agent_principles.md) — 15 design principles distilled from the problems
- [`docs/multi-domain-refactor-spec.md`](docs/multi-domain-refactor-spec.md) — Refactor specification
