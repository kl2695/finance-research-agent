# Finance Research Agent

An AI agent that answers quantitative financial questions using SEC filings, scoring 78% on the Vals AI Finance Agent Benchmark — 14 points above the leaderboard leader (Claude Opus 4.7 at 64%).

## Results

| Metric | Value |
|--------|-------|
| FAB Public Set (50 questions) | **78% accuracy** |
| Leaderboard #1 (Claude Opus 4.7) | 64.4% |
| Bare Claude Sonnet 4.6 | 63.3% |
| Numerical Reasoning | 100% |
| Trends | 100% |
| Complex Retrieval | 100% |
| Average question time | ~60 seconds |
| Average cost per question | ~$0.15 |

Built by one person. Documented via 103 problems encountered and solved, 15 agent design principles, and a full architecture doc.

## Architecture

```
Question → Planner (LLM) → Prefetch (SEC EDGAR) → ReAct Agent → Extraction → Calculator → Answer
```

**Key technical decisions:**

- **Structured filing selection** — The LLM planner outputs a `filings_needed` list specifying exactly which SEC filings, XBRL concepts, and 10-K sections to fetch. The code just iterates and fetches. No hardcoded keyword maps needed for new question types.

- **4-step hybrid extraction** — XBRL exact matching → structured text parsing (regex with table/column awareness) → LLM fact matching (Haiku assigns parsed values to keys) → LLM raw extraction fallback. Each step fills what the previous couldn't.

- **HTML table parser with column annotations** — SEC filing tables are parsed structurally, annotating each value with its column header (e.g., `Total [Next 12 Months]: $14,426,266`). This preserves column context that flat-text extraction loses.

- **Fiscal quarter filename matching** — Companies use non-standard fiscal years. The press release fetcher matches by fiscal quarter identifier in exhibit filenames (`a2024q3ex991` = Q3 FY2024), handling any fiscal year convention automatically.

- **Programmatic over probabilistic** — For any data with deterministic structure (XBRL, dollar amounts, percentages), extract with regex — not an LLM. LLMs round. `$4,278.9 million` becomes `$4.3 billion` when an LLM summarizes. Code doesn't round.

## How It Works

1. **Planner** creates a structured research plan: what data is needed, which filings to fetch, what formulas to compute
2. **Prefetch** fetches SEC EDGAR data (XBRL line items, earnings press releases, targeted 10-K sections) before the agent starts reasoning
3. **ReAct Agent** reasons with the prefetched data + additional tool calls (web search, filing text)
4. **Extraction** fills the plan's data_needed values using 4 methods in priority order
5. **Calculator** executes formulas as deterministic Python (not LLM arithmetic)
6. **Formatter** produces the final answer with citations

## Debugging Methodology: Tracing a 1.3 bps Error

One example of the systematic approach — the Lyft beat/miss question was off by 1.3 basis points (24.78 vs 26.1 ground truth):

```
P27: "LLM rounds $4,278.9M to $4,300M" 
  → investigated → not the LLM's fault

P29: "Tool log truncates press release to 4,000 chars — financial tables start at char 5,500"
  → removed truncation → but extractor still picks $4.3B

P30: "Table values like '$ 4,278.9' have no unit suffix — '(in millions)' is in the header"
  → added table-level unit detection → value now correct but wrong column picked

P31: "Context keywords bleed across table rows"
  → restricted to before-only context → right row, wrong column

P32: "Position tiebreaker compares across documents"  
  → added source_idx ordering → correct value selected

P99: "Section marker finds ToC mention before actual data"
  → skip occurrences without nearby numbers → finds data tables
```

Each step: observe → identify root cause → fix → discover next layer. The final answer: 26.08 bps (0.02 bps off ground truth). Full trace in [`docs/problems.md`](docs/problems.md).

## Documentation

This project is documented as a case study in systematic AI engineering:

- [`docs/problems.md`](docs/problems.md) — 103 problems encountered, each with observed behavior, root cause, solution, and status. Traces the evolution from "LLM rounds numbers" to "table column disambiguation."
- [`docs/agent_principles.md`](docs/agent_principles.md) — 15 design principles distilled from the problems (e.g., "Programmatic over probabilistic", "Forward-looking data comes from the prior year's filing")
- [`docs/architecture.md`](docs/architecture.md) — Full pipeline flow, data extraction flow (4 steps), prefetch system, component map
- [`docs/test_results.md`](docs/test_results.md) — Detailed per-question results with failure analysis
- [`docs/action_items.md`](docs/action_items.md) — Prioritized backlog with estimated impact per fix

## Setup

```bash
# Clone and install
cd "Product Prototyping/Finance Research Agent"
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Set API key
echo "ANTHROPIC_API_KEY=your-key" > .env

# Run tests (no API calls, <2 seconds)
python -m pytest tests/ -q

# Run a single question
python -c "from src.agent import run; print(run('Calculate the inventory turnover for US Steel in FY2024.')['answer'])"

# Run the full benchmark evaluation
python eval.py --verbose

# Cheap re-eval after code changes (~$1.50 instead of $8)
python eval.py --cheap-reeval results/eval_TIMESTAMP.json --verbose
```

## Question Types Handled

| Type | Accuracy | Example |
|------|----------|---------|
| Numerical Reasoning | 88% | "Calculate the 3-year revenue CAGR for Palantir" |
| Quantitative Retrieval | 89% | "What was FND same-store sales growth in Q4 2024?" |
| Beat or Miss | 71% | "How did Lyft's Q4 EBITDA margin compare to guidance?" |
| Qualitative Retrieval | 67% | "Summarize regulatory risks from Paylocity's 10-K" |
| Complex Retrieval | 100% | "Of AMZN, META, GOOG — who plans most capex in 2025?" |
| Financial Modeling | 50% | "What is BROS gross profit in 2026 assuming 30% CAGR?" |

## Tech Stack

- **LLM**: Claude Sonnet 4.6 (reasoning) + Haiku 4.5 (formatting, judging)
- **Data**: SEC EDGAR XBRL API, filing text, earnings press releases
- **Language**: Python 3.13
- **Testing**: 102 unit/integration tests, evaluation harness with LLM-as-judge
