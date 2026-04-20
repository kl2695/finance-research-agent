# Finance Research Agent — Project Plan

## Goal

Best possible score on the Vals AI Finance Agent Benchmark (FAB). Top of leaderboard: o3 at 46.8% on 337-question test set. Our current result: 18/18 on public validation set (with iteration).

## Current State (2026-04-18)

- **Architecture:** Planner → Structured Prefetch → ReAct → Hybrid Extraction → Calculator → Formatter
- **Key innovation:** Structured `filings_needed` — LLM specifies exact SEC filings, code fetches them
- **Test coverage:** 18/50 public questions tested, all passing
- **Docs:** 67 problems, 14 agent principles, full architecture doc, test results tracker
- **Known gaps:** Financial Modeling (0/3 tested), Market Analysis (0/3), Complex Retrieval (0/3)

## Plan

### Phase 1: Automated Evaluation Harness [CURRENT]
**Goal:** Score all 50 public questions automatically so we can iterate fast.
**Tasks:**
- [ ] Build evaluation script that runs all 50 questions and scores against rubrics
- [ ] Use the FAB rubric format (correctness + contradiction operators)
- [ ] Use LLM-as-judge (Sonnet) to evaluate each rubric criterion against our answer
- [ ] Output: per-question pass/fail, overall accuracy, failure breakdown by type
- [ ] Save results to `results/` with timestamp for comparison across runs

**Success criteria:** Can run full 50-question eval and get a scored report in <45 min.

### Phase 1.5: Scalability Refactor [COMPLETE]
**Goal:** Remove hardcoded keyword maps that limit the system to known question types. Replace with LLM-driven matching that scales to arbitrary metrics.
**Tasks:**
- [x] P72: Separate parsing from matching — structured regex finds values, LLM assigns to keys
- [x] P70/P74: Let planner specify XBRL concepts in `filings_needed` instead of keyword map
- [x] P71: Make `_extract_section()` handle arbitrary section names as search terms
- [x] P73: Remove old heuristic prefetch once `filings_needed` is confirmed sufficient

**Success criteria:** No hardcoded keyword maps needed for new question types. A question about "days payable outstanding" or "goodwill impairment" works without code changes.

### Phase 2: Baseline Run on Full Public Set [COMPLETE — baseline: 68%]
**Goal:** Get real accuracy without cherry-picking.
**Tasks:**
- [x] Run all 50 questions through the harness
- [x] Document baseline accuracy by question type
- [x] Identify new failure modes from the 32 untested questions
- [x] Log new problems to problems.md
- [ ] Prioritize fixes by frequency and question type impact

**Expected outcome:** ~70-80% accuracy. Financial Modeling and Complex Retrieval likely to be the weakest.

### Phase 3: Fix and Iterate
**Goal:** Maximize accuracy on the public set.
**Tasks:**
- [ ] Fix failures from Phase 2 (expect 2-3 iterations)
- [ ] Each iteration: diagnose failures → fix → re-run → measure
- [ ] Focus on systemic fixes (not per-question hacks)
- [ ] Update problems doc, agent principles, and architecture doc with each fix
- [ ] Stop when accuracy plateaus (diminishing returns threshold)

**Target:** >85% on full 50-question public set.

### Phase 4: Non-Determinism
**Goal:** Ensure consistent results across runs.
**Tasks:**
- [ ] Run full 50-question set 3 times
- [ ] Identify questions that pass <2/3 runs
- [ ] Root-cause non-determinism (usually: ReAct agent tool choices, web search variability)
- [ ] Fix: stronger prefetch coverage, better prompt constraints, structured extraction over narrative
- [ ] Target: >90% of questions pass 3/3 runs

### Phase 5: Private Test Set (Leaderboard Submission)
**Goal:** Submit to FAB leaderboard with a competitive score.
**Tasks:**
- [ ] Estimate cost: ~$45 per run (337 questions at ~$0.13/q)
- [ ] Investigate Vals AI submission process (their harness vs. self-hosted)
- [ ] Run against private test set
- [ ] Compare to leaderboard (o3: 46.8%, Gemini 2.5 Pro: ~50%)
- [ ] If score is competitive, submit

**Note:** The public set has 50 "easier" questions. The private set is harder and larger. Expect 10-20% accuracy drop from public to private.

## Cost Budget

| Phase | Estimated Cost | Time |
|-------|---------------|------|
| Phase 1 (harness) | ~$0 (code only) | 1-2 hours |
| Phase 2 (baseline) | ~$6 | 30 min |
| Phase 3 (3 iterations) | ~$18 | 2 hours |
| Phase 4 (3 stability runs) | ~$18 | 1.5 hours |
| Phase 5 (private set) | ~$45 | 3 hours |
| **Total** | **~$87** | **~8 hours** |

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-16 | Started with 3 hand-picked questions | Build confidence in pipeline before broad testing |
| 2026-04-17 | Expanded to 18 questions, iterated | Each failure revealed systemic issues worth fixing |
| 2026-04-17 | Refactored to structured filings_needed | Regex-parsing free text was the root cause of 5+ filing selection bugs |
| 2026-04-18 | Added supplementary filing access | Older filings (pre-2023) weren't in SEC EDGAR recent array |
| 2026-04-18 | Build evaluation harness before running more | Manual answer checking doesn't scale; need automated scoring for fast iteration |
| 2026-04-18 | Built evaluation harness (eval.py) | Need automated scoring to iterate on 50 questions |
| 2026-04-18 | Fixed Airbnb CFO formal name, Netflix ARPU, TKO, supplementary filing access | 18/18 passing but non-determinism observed on Lyft |
| 2026-04-18 | Identified non-determinism as Phase 4 priority | Lyft passes some runs, fails others — need stability testing |
| 2026-04-19 | Scalability review before full run | Identified 5 hardcoded maps that limit system to known question types. Must refactor before running 50 questions to avoid per-question patches. |
| 2026-04-19 | Completed Phase 1.5 scalability refactor | All 5 hardcoded maps eliminated from hot path. System scales to arbitrary question types without code changes. |
| 2026-04-19 | Phase 2 baseline: 34/50 (68%) | 5 pts above bare Sonnet. Qualitative context (5 failures) is highest-priority fix for Phase 3. |
| 2026-04-20 | Phase 3 fixes: P75-P89 | Qualitative context, multi-quarter truncation, JSON parsing, multi-company tickers, calculator safety, timeout, date context, delisted tickers, section guidance. Expected lift: 68%→78-80%. |
