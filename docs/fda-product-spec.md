# FDA Regulatory QA Agent — Product Spec

## Context

We have a multi-domain research agent that scores 78% (42/50 post-refactor) on the Vals AI Finance Agent Benchmark — 14 points above Opus. The core pipeline (planner → prefetch → ReAct → 4-layer extraction → calculator → formatter) is now domain-agnostic with a Domain ABC. An FDA stub proves the interface works end-to-end. Now we need to build the real FDA domain.

There is no public benchmark for FDA regulatory QA. Building one (510kQA) is a deliverable alongside the agent — it maps directly to the MTS JD's "evaluating LLMs across a diversity of life science specific tasks."

## Target User

**Regulatory affairs professional** preparing 510(k) submissions, tracking predicate devices, and monitoring adverse events for their device portfolio. Needs exact K-numbers, dates, counts, and product codes. The same precision constraint as finance — "178 days, not around 180."

## What the Agent Does

Given a natural language question about FDA-regulated medical devices, the agent returns a precise, cited answer sourced from public FDA databases.

**Input:** A question + optional as_of_date
**Output:** A precise answer with citations to specific K-numbers, MAUDE report IDs, or openFDA query results

### Example Interactions

**Predicate Lookup:**
> "List all predicate devices cited in 510(k) K213456 with their clearance dates."
> → "K203211 (cleared 2020-08-14), K192847 (cleared 2019-11-02). Source: openFDA 510(k) database."

**Clearance Timeline:**
> "Median clearance time for 510(k)s in product code DRG from 2022–2024?"
> → "178 days (n=23 submissions). Source: openFDA 510(k) clearance records filtered by product code DRG, decision date 2022-01-01 to 2024-12-31."

**Classification Reasoning:**
> "What device class and product code applies to a new cardiac ablation catheter with RF energy delivery?"
> → "Class III, product code GEI (Catheter, Ablation, Cardiac). RF energy ablation catheters require PMA, not 510(k). Source: FDA Product Classification Database."

**Adverse Event Synthesis:**
> "Count MAUDE reports for device brand 'HeartMate 3' involving patient death from Jan 2023 to Jun 2024."
> → "47 reports. Breakdown: 31 device malfunction leading to death, 16 patient death during/after use. Source: MAUDE database, filtered by brand_name='HeartMate 3', date_received 2023-01-01 to 2024-06-30, event_type='Death'."

**Multi-Source Reasoning:**
> "For product code LWS, list clearances since 2022 and any associated recalls or adverse event clusters."
> → "14 clearances since 2022 [list with K-numbers and dates]. 2 Class II recalls: [recall numbers, reasons]. MAUDE shows 89 adverse event reports in this period, 12 involving serious injury. Source: openFDA 510(k), recall, and MAUDE databases."

## Question Types (5 Categories)

| Category | Count in Benchmark | What It Tests | Data Source |
|----------|-------------------|---------------|-------------|
| **Predicate Lookup** | 6-8 | Find specific K-numbers, predicate chains, clearance dates | 510(k) database + AccessData PDFs |
| **Clearance Timeline** | 4-6 | Compute statistics (median days, counts) over filtered clearance sets | 510(k) database |
| **Classification Reasoning** | 4-5 | Map device descriptions to product codes, device classes, regulatory pathways | Product classification DB |
| **Adverse Event Synthesis** | 4-5 | Count/filter/summarize MAUDE reports by device, event type, date range | MAUDE database |
| **Multi-Source Reasoning** | 4-5 | Cross-reference clearances with recalls, adverse events, or related devices | 510(k) + MAUDE + recalls |

## Data Sources (v1 — openFDA only)

All from the openFDA API family (https://open.fda.gov/apis/). No auth required for basic access. Rate limit: 240 requests/minute with free API key, 40 without.

| Source | openFDA Endpoint | What It Provides |
|--------|-----------------|-----------------|
| **510(k) Clearances** | `/device/510k` | K-number, applicant, device name, product code, clearance date, decision, clearance type |
| **510(k) Summary PDFs** | AccessData website | Predicate device K-numbers, device descriptions, substantial equivalence arguments |
| **MAUDE Adverse Events** | `/device/event` | Event date, device brand/model, event type (death/injury/malfunction), event narrative |
| **Recalls** | `/device/recall` | Recall number, product description, reason, firm name, date |
| **Product Classification** | `/device/classification` | Product code, device class (I/II/III), regulation number, submission type |
| **Enforcement Actions** | `/device/enforcement` | Recall classification (I/II/III), not available in recall endpoint |

**Not in v1:** ClinicalTrials.gov, drug labels (FDARxBench), PMA database.

## Behavioral Requirements

### Precision
- Return exact values: K-numbers, dates (YYYY-MM-DD), counts, product codes
- Never round or approximate when the source data is exact
- "47 MAUDE reports" not "approximately 50 adverse events"

### Citation
- Every fact traced to a specific database query or record
- Include K-numbers, MAUDE event IDs, recall numbers as citations
- Include the query parameters that produced the result (date range, product code, filters)

### Temporal Awareness
- All queries are point-in-time (as_of_date parameter)
- Agent does not report data that arrived after as_of_date
- Benchmark ground truth is frozen with `ground_truth_captured_at` dates

### Graceful Degradation
- If a specific K-number doesn't exist, say so (don't hallucinate)
- If MAUDE data is sparse, report the actual count (even if zero)
- If a question requires a data source not in v1, explicitly say "this requires ClinicalTrials.gov data, which is not currently available"

### Regulatory Correctness
- Use correct terminology: "510(k) clearance" not "FDA approval" (approval is for PMA)
- Distinguish device classes correctly (Class I/II/III)
- Understand regulatory pathways: 510(k) vs PMA vs De Novo vs Exempt
- Use official product code descriptions from the classification database

## Success Metrics

### Primary: 510kQA Benchmark
- 30 questions with programmatic ground truth (each has a canonical openFDA query)
- Score against baselines: bare Sonnet, bare Opus, bare Haiku (no agent pipeline)
- Mode-of-3 judging + deterministic numeric pre-check (same infrastructure as FAB)
- Target: beat bare Sonnet by meaningful margin (>10 points), demonstrating the agent pipeline adds value over raw LLM

### Secondary: 5-10 Showcase Questions
- Curated subset that tells the best story
- Each demonstrates a different capability (lookup, computation, cross-reference, deep extraction)
- Polished answers with clear citations
- Used for demos and presentations

## Scope Boundaries

### In Scope (v1)
- openFDA device endpoints (510(k), MAUDE, recalls, classification, enforcement)
- AccessData PDF scraping for predicate device information
- 510kQA benchmark (30 questions, programmatic ground truth)
- 5-10 showcase questions
- Domain implementation plugging into existing core pipeline
- Eval harness integration (same mode-of-3, numeric pre-check, cost tracking)

### Out of Scope (v2+)
- ClinicalTrials.gov integration
- FDARxBench drug label benchmark
- Drug-related endpoints (openFDA drug/label, drug/event)
- Real-time monitoring / alerting (delta queries)
- Recursive predicate chains (predicates of predicates)
- PMA database deep integration

## Deliverables

1. **`domains/fda/`** — Real FDADomain implementation replacing the stub
2. **`domains/fda/benchmark/questions.jsonl`** — 510kQA benchmark (30 questions, frozen ground truth)
3. **Benchmark results** — Agent vs Sonnet vs Opus baselines on 510kQA
4. **5-10 showcase questions** — Curated demos with polished outputs
