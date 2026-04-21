# Evaluation Strategy: Multi-Domain Research Agent

**Status:** Draft
**Companion to:** `multi-domain-refactor-spec.md`
**Target branch:** `refactor/multi-domain` (eval integration lands after refactor merges)

---

## Summary

Pair an existing academic benchmark (FDARxBench, Stanford/FDA, March 2026) with a novel benchmark we build (510kQA, ~25-30 questions on device regulatory QA). Run the pipeline against both in retrieval mode and report numbers alongside frontier-model baselines. Total budget: ~2 weeks of work, ~$150-250 in inference costs. Pitch becomes "existing bar + novel contribution, both aligned with Valkai's medical device ICP."

## 1. Benchmark Landscape

| Benchmark | Domain | Status | Use |
|---|---|---|---|
| FAB (Vals AI) | Finance / SEC filings | Integrated, 78% public validation | Preserve as finance regression test through refactor |
| FDARxBench (Stanford/FDA) | Pharma / drug labels | Public March 2026, Apache-2.0 | Integrate for FDA domain |
| Feng Lab LLM-FDA-device pipeline | Medical devices | Methodology paper, code on GitHub, not a benchmark | Reference methodology, don't try to run against it |
| Device regulatory QA | Medical devices / 510(k) / MAUDE | **Does not exist** | Build (510kQA, novel contribution) |

FDARxBench filled a meaningful hole when it dropped a few weeks ago — before it, the closest regulated-industry QA benchmark to FAB was LegalBench, which isn't a close match. But it's pharma (drug labels), not devices. For Valkai specifically, where every testimonial on the homepage is from a device company, we still need a device-aligned benchmark. Building one is tractable and the result is its own pitch.

## 2. FDARxBench

### 2.1 What it is

- 17,223 expert-curated QA examples over 700 FDA prescription drug labels
- Authored in collaboration with FDA regulatory assessors (Russ Altman's group at Stanford)
- arXiv 2603.19539, Apache-2.0 license, GitHub at `xiongbetty/FDARxBench`
- Model-agnostic — you bring your own LLM for both inference and grading

### 2.2 Task and mode structure

Three task types:

| Task | Count | Shape |
|---|---|---|
| Factual | 9,888 | Answerable from a single label section |
| Multi-hop | 3,400 | Requires cross-section reasoning |
| Refusal | 3,935 | Unanswerable from the label; model should abstain |

Four evaluation modes:

| Mode | What's provided | What it tests |
|---|---|---|
| Closed-book | Question only | Parametric knowledge |
| Open-book full label | Question + entire label with passage markers | Long-context comprehension, grounding |
| Open-book oracle | Question + gold passages | Reasoning isolated from retrieval |
| Retrieval | Question + chunked label corpus | Evidence selection + reasoning |

Grading: judge LLM assigns A (correct) / B (incorrect) / C (not attempted) per prediction.

### 2.3 Cost

| Item | Cost |
|---|---|
| Benchmark access | Free (Apache-2.0, clone from GitHub) |
| Toy debug set (qa_toy.jsonl) | $1-5 |
| Focused demo subset (150-200 Qs, mode-of-3 judging) | $100-200 |
| Full run (17K Qs, all modes, mode-of-3) | $2K-8K — **do not do this** |

Drug labels are long; open-book full label mode puts a multi-page label in context every question. Stick to subsets. Reviewers don't care whether you ran the full 17K; they care that the methodology and the numbers are honest.

### 2.4 Architecture fit by mode

Our pipeline is a tool-using agent: planner emits `filings_needed`, prefetch fetches, ReAct reasons, extractor pulls facts, calculator computes. FDARxBench is document QA. These don't map 1:1.

| Mode | Fit | Why | Integration effort |
|---|---|---|---|
| Closed-book | Poor | No tools to call; pipeline overhead for no gain | Skip |
| Open-book full label | Medium | Pre-loaded document as a degenerate "filing"; pipeline still orchestrates extraction | 2-3 days |
| Open-book oracle | Medium | Same shape as full label, just shorter context | 1-2 days on top of full-label adapter |
| Retrieval | Good | Retrieval is a tool call; planner-prefetch-ReAct exercises properly | 4-5 days |

**Retrieval is the mode where this architecture has a story to tell.** The planner decides which chunks to retrieve based on the question; the ReAct loop reasons over retrieved passages; extraction pulls the specific answer. That's the agentic pattern working correctly. Full-label and oracle modes are mostly "long-context Sonnet with scaffolding" — you might not beat a well-prompted baseline, and forcing a comparison where the architecture isn't load-bearing is a reviewer red flag.

### 2.5 Pre-integration check (do this before committing)

~1 hour of work, saves a week of surprise:

1. `git clone https://github.com/xiongbetty/FDARxBench`
2. Inspect `data/qa/qa.jsonl` — confirm fields match the README (qid, question, answer, task, context, set_id, drug_name)
3. Inspect `data/labels/labels.jsonl` — look at the `chunks` array structure for 3-5 labels. Are chunks clean passages, or are they fragments with weird boundaries?
4. Run 5-10 questions from `qa_toy.jsonl` through a manual Sonnet call to sanity-check
5. Check `overview.png` to confirm the pipeline diagram matches what you think the task is

**Go/no-go:** if chunk structure is messy (multi-level headers embedded in chunks, inconsistent section markers, passages split mid-sentence), retrieval-mode integration cost balloons. Bail early and pivot to full-label mode if it's ugly.

### 2.6 Integration design

Lives in `domains/fda/benchmark/fdarxbench/`:

```
domains/fda/benchmark/fdarxbench/
├── loader.py          # Parse their JSONL into BenchmarkQuestion + label context
├── adapter.py         # Translate their prompt format → pipeline input
├── grader.py          # Wrap their A/B/C grader in mode-of-3
└── runner.py          # Orchestrate full eval run, emit predictions JSONL
```

The core pipeline stays unchanged. The adapter adds one new filing type to the FDA domain's `tool_dispatch`:

```python
# domains/fda/tools.py
def fetch_provided_passages(entry: FilingRequest, default_identifier: str) -> ToolResult:
    """For FDARxBench: returns pre-chunked label passages.
    Retrieval mode: planner specifies which chunk_ids to fetch.
    Full-label mode: returns all chunks concatenated."""
    ...
```

For retrieval mode, the planner emits `filings_needed: [{"type": "passages", "concepts": [chunk_ids]}]`. The chunk selection logic is a simple BM25 retriever over the label's chunks — don't build a vector DB for this. It's a demo.

Integration flow:

```
Their: prepare_prompts.sh      → prompts.jsonl
  ↓
Our:   adapter.py              → pipeline input per question
  ↓
Our:   core.agent.run()        → prediction
  ↓
Our:   runner.py               → predictions.jsonl (in their format)
  ↓
Their: prepare_grading.sh      → grading_prompts.jsonl
  ↓
Our:   grader.py (mode-of-3)   → A/B/C per question, majority vote
```

### 2.7 Recommended scope for the demo

- **Primary: retrieval mode, 100-150 balanced questions.** Sample evenly across factual / multi-hop / refusal tasks.
- **Secondary: open-book full label, 30-50 questions.** Contrast data point showing the pipeline works across modes.
- **Skip: closed-book and oracle modes.** Neither exercises the architecture.
- **Baselines to report alongside:** Sonnet 4.6 direct calls (no pipeline), Opus 4.7 direct calls, Haiku 4.5 direct calls. Same question subset.

### 2.8 Judging

Their grader: judge LLM reads (question, gold_answer, prediction), returns A/B/C.

Our wrapper: call their grader 3x per question, majority vote. Consistent with existing FAB infrastructure, reduces judge variance. Cost is 3x single-judge but absolute cost is small at these subset sizes (~$30-50 for judging 200 questions mode-of-3).

Note: the deterministic numeric pre-check from FAB doesn't apply here — FDARxBench answers are text, not numeric. Judge is the only grader.

### 2.9 Reporting format

Don't cherry-pick. Report:
- Score per mode attempted
- Score per task type (factual / multi-hop / refusal)
- Confusion matrix (especially refusal — does your pipeline abstain when it should?)
- Cost per run
- Explicit note about which modes were skipped and why

The "skipped closed-book because pipeline doesn't exercise" explanation is stronger than a hidden sampling choice. Reviewers detect the latter.

## 3. Novel Benchmark: 510kQA

### 3.1 Rationale

No public benchmark exists for medical device regulatory QA. The space has adjacent artifacts (Feng Lab's device pipeline, the public 510(k) database itself), but nothing with held-out questions, rubrics, and a leaderboard-style eval harness. Building it is a real contribution, not a performance.

For the Valkai pitch specifically, this is the artifact that aligns with their ICP. FDARxBench demonstrates we can hit existing bars; 510kQA demonstrates we saw a gap and filled it.

### 3.2 Scope

25-30 questions. More is a trap — ground truth research for regulatory questions is slow, and 30 well-grounded questions beats 100 questionable ones.

Balanced across categories:

| Category | Count | Example |
|---|---|---|
| Predicate lookup | 6-8 | "For device K213456, list all cited predicate devices and their clearance dates." |
| Clearance timeline | 4-6 | "Median days from submission to clearance for product code DRG submissions filed in 2023." |
| Classification reasoning | 4-5 | "What device class and product code applies to a new cardiac ablation catheter with RF energy delivery?" |
| Adverse event synthesis | 4-5 | "Count MAUDE reports for device brand X involving patient injury from Jan 2023 to Jun 2024." |
| Multi-source reasoning | 4-5 | "For product code LWS, list clearances since 2022 and any associated recalls or adverse event clusters." |

### 3.3 Ground truth strategy

Ground truth must be authoritative and reproducible. For each question, document:
- The specific openFDA query or database lookup that produces the answer
- The date the ground truth was captured (answers change as new clearances/events are added)
- Any manual verification steps

Answerable from public sources: clearance dates, predicate K-numbers, product codes, device classes, adverse event counts within a date range. These are definitive.

Harder / skip: subjective questions ("which predicate is *most* substantially equivalent?"), questions requiring non-public data, forward-looking questions. If the answer requires expert judgment to settle, drop it.

Every question gets a `ground_truth_source` field pointing to the definitive query or record, so re-verification is a script run.

### 3.4 Format

Native `BenchmarkQuestion` schema from the refactor spec. This plugs into `core.eval_harness` with no adapter.

```python
BenchmarkQuestion(
    id="510kqa_001",
    question="List all predicate devices cited in 510(k) K213456 with their clearance dates.",
    as_of_date="2024-12-01",
    rubric=[
        "Identifies all predicate K-numbers cited in K213456",
        "Provides correct clearance date for each predicate",
        "No hallucinated predicates",
    ],
    expected_answer="K203211 (cleared 2020-08-14), K192847 (cleared 2019-11-02)",
    tags=["predicate_lookup", "multi_value"],
)
```

Rubric criteria feed the existing mode-of-3 judge. Numeric questions (e.g., counts, median days) benefit from the deterministic numeric pre-check already in `core.eval_harness`.

### 3.5 Timeline (5-7 focused days)

| Day | Work | Output |
|---|---|---|
| 1 | Question design. Brainstorm ~50 candidates across categories. Cull to 30. | `questions_draft.md` |
| 2 | Category balance review. Sanity-check that each category exercises the pipeline differently. | `questions_final.md` |
| 3-4 | Ground truth research. For each question: run the canonical query, record the answer, document the source. | `benchmark.jsonl` (draft) |
| 5 | Rubric authoring. 2-3 criteria per question. Calibrate difficulty — not every question should be solvable. | `benchmark.jsonl` (with rubrics) |
| 6 | Dry run. Manually run 5 questions through the FDA domain. Verify answerable, adjust phrasing. | Updated benchmark |
| 7 | Full eval run against Sonnet / Opus baselines. Sanity check scoring. | `benchmark.jsonl` (frozen), baseline scores |

### 3.6 Reporting format

Same structure as FDARxBench reporting. Include per-category breakdown — predicate lookup is likely easier than multi-source reasoning, and that gradient tells a story.

## 4. Hybrid Approach (Recommended)

### 4.1 Why both

Running only FDARxBench: "I can hit an existing bar." Solid but not differentiated.
Running only 510kQA: "I built a benchmark and scored well on it." Easy to read as self-graded.
Running both: external validation + novel contribution, in complementary domains. Much stronger.

### 4.2 Full timeline (2 weeks, post-refactor)

| Week | Days | Work |
|---|---|---|
| 1 | 1 | Pre-integration check on FDARxBench (§2.5). Go/no-go decision. |
| 1 | 2-4 | FDARxBench retrieval-mode adapter. Full-label mode if time permits. |
| 1 | 5 | Spike test: 50 questions end-to-end. Validate scores are in a reasonable range vs. Sonnet baseline. |
| 2 | 6-8 | 510kQA question design + ground truth research |
| 2 | 9-10 | 510kQA rubrics + dry run + full eval |
| 2 | 11-12 | Full reporting pass. Both benchmarks, all modes, with baselines. |

Buffer: 2 days for surprises. Real timeline: ~14 calendar days if part-time, ~10 if full-time.

### 4.3 Full cost estimate

| Item | Cost |
|---|---|
| FDARxBench toy/spike runs | $5-15 |
| FDARxBench focused subset (200 Qs, 2 modes, mode-of-3) | $100-150 |
| 510kQA full run (30 Qs, 3-5 models, mode-of-3) | $30-50 |
| Misc iteration | $20-40 |
| **Total** | **$155-255** |

Keep within a $300 ceiling. If you're blowing past this, something's wrong with the integration.

## 5. Architecture Fit — Honest Analysis

The pipeline was built for tool-using precision QA over structured public filings. That's a specific shape. FDARxBench drug-label QA doesn't fit perfectly; forcing it is dishonest.

**Where our architecture has a real edge:**
- Retrieval mode (planner decides what to fetch)
- 510kQA (tool-using over 510(k) DB + openFDA)
- Any question where the answer lives across multiple structured sources
- Any question with a precise numeric answer

**Where it doesn't:**
- Closed-book QA (parametric knowledge tests)
- Single-document reading comprehension (Sonnet long-context is strong here without orchestration)
- Summarization or open-ended generation
- Refusal tasks (pipeline's tool-using bias may cause over-engagement on unanswerable questions — worth watching)

The refusal task category is worth specific attention. Our architecture is optimized to find answers, not to abstain. If the FDARxBench refusal numbers come in low, that's actionable signal — either add an abstention check before the formatter, or flag it as a known limitation in the reporting.

## 6. Risks

### 6.1 Chunk structure blocks retrieval integration

If `labels.jsonl` chunks are messy (inconsistent markers, mid-sentence splits, embedded headers), BM25 retrieval quality tanks and the demo is weak. Pre-integration check (§2.5) catches this; if it fires, pivot to full-label mode and don't burn a week on retrieval.

### 6.2 Baselines beat our pipeline on non-retrieval modes

Possible outcome. If Sonnet long-context beats the pipeline on open-book full label, don't hide it — report it and explain why (architecture isn't optimized for this mode). The integrity of the reporting is more valuable than any single number.

### 6.3 510kQA ground truth rot

Regulatory data changes. New 510(k)s are cleared weekly. Adverse events are continuously reported. Freeze a `ground_truth_captured_at` date per question. Don't re-run the benchmark across the date boundary without re-verification.

### 6.4 510kQA ambiguity

If >20% of questions have ground truth that requires judgment calls, the benchmark isn't robust. Stick to definitive answers from structured public sources. Skip anything that needs "expert consensus."

### 6.5 Scope creep

The pull is to run more modes, more questions, more models. Resist. 200 FDARxBench questions + 30 510kQA questions is the target. More data doesn't strengthen the pitch; cleaner methodology does.

### 6.6 Judge LLM drift

Anthropic model updates can shift judge behavior. Pin model version for judging (e.g., `claude-haiku-4-5-20251001` specifically), record it in the output, don't mix versions across a run.

## 7. Success Criteria

Benchmark integration is complete when:

- [ ] Pre-integration check on FDARxBench passed (chunk structure is clean enough for retrieval)
- [ ] FDARxBench retrieval mode runs end-to-end with a 100-150 question subset
- [ ] Scores reported per task type (factual / multi-hop / refusal) alongside Sonnet / Opus / Haiku baselines
- [ ] 510kQA benchmark is frozen (30 questions, all with documented ground truth sources and `ground_truth_captured_at` dates)
- [ ] 510kQA runs end-to-end via `python main.py --domain fda --eval`
- [ ] Full reporting document exists with all modes, all task types, all baselines, and explicit notes on what was skipped and why
- [ ] Total inference spend under $300
- [ ] Reproducibility check: all runs re-runnable from logged tool_logs via `--cheap-reeval`

## 8. Open Questions

To resolve before starting:

1. **Retrieval backend:** BM25 vs. sentence-transformer embeddings? Recommendation: BM25 for the demo. It's 30 lines of code, no infrastructure, and "good enough for a benchmark run" beats "production retrieval system" here. Document as a future improvement.

2. **Judge LLM choice for mode-of-3:** Haiku for cost, Sonnet for rigor? Recommendation: Haiku, consistent with FAB. Cheap enough to run all three graders on all questions.

3. **510kQA public release:** Ship it alongside the code as `domains/fda/benchmark/questions.jsonl`? Or keep private as a held-out set? Recommendation: public. The value is the contribution, not the secrecy.

4. **Baseline model selection:** Report against Sonnet 4.6 + Opus 4.7, or also include GPT-5 / Gemini 2.5 Pro? Recommendation: Claude family only for the demo. Adding other vendors triples inference cost and complicates the story. "Compared against Anthropic frontier models" is honest and sufficient.

5. **FDARxBench refusal task handling:** Do we attempt refusal questions, or skip them because the architecture isn't built for abstention? Recommendation: attempt, report honestly. A low refusal score is useful signal for the pitch — "here's a known limitation, here's how I'd fix it."

## 9. Reporting Artifact

End state: a single markdown `benchmark_results.md` covering both benchmarks. Structure:

```
# Multi-Domain Research Agent: Benchmark Results

## FAB (Finance)
- Score: 78% public / [X]% private
- 14 points ahead of Opus 4.7

## FDARxBench (Pharma)
- Retrieval mode: [X]% vs Opus [Y]%, Sonnet [Z]%
- Open-book full label: [A]% vs Opus [B]%, Sonnet [C]%
- Per-task breakdown (factual / multi-hop / refusal)
- Cost: $[X]
- Notes on skipped modes

## 510kQA (Medical Devices — novel)
- [X]% overall vs Opus [Y]%, Sonnet [Z]%
- Per-category breakdown
- Ground truth captured as of [date]
- Cost: $[X]
- Public: github.com/[you]/research-agent
```

This doc is the deliverable reviewers see. Write it as if the reader has 10 minutes.
