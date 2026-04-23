# FDA Eval Pipeline Audit Report

**Methodology:** [evals-skills](https://github.com/hamelsmu/evals-skills) `eval-audit` framework
**Pipeline:** FDA Regulatory QA Agent — 510kQA benchmark (30 questions, 27/30 = 90%)
**Date:** 2026-04-23

---

## Findings Summary (ordered by impact)

| # | Finding | Severity | Diagnostic Area | Fix |
|---|---------|----------|----------------|-----|
| 1 | LLM judge not validated against human labels | **Critical** | Judge Validation | Run `validate-evaluator`: collect 50+ human labels, compute TPR/TNR |
| 2 | No systematic error analysis for FDA domain | **High** | Error Analysis | Run `error-analysis` on 30 eval traces + expand to 100 |
| 3 | Ground truth drifts with live API data | **High** | Pipeline Hygiene | Freeze canonical API responses alongside questions |
| 4 | Judge sees final answer only, not full traces | **High** | Human Review | Pass tool_log context to judge for reasoning verification |
| 5 | Only 30 labeled examples (need ~100 for error analysis) | **Medium** | Labeled Data | Expand benchmark to 50-100 questions across more edge cases |
| 6 | No negative/adversarial test cases | **Medium** | Labeled Data | Add refusal tests (fake K-numbers, unanswerable questions) |
| 7 | Proposed fixes not re-validated after implementation | **Medium** | Pipeline Hygiene | Automate re-eval after tool changes |
| 8 | No human domain expert reviewed traces | **Medium** | Human Review | Have 1 regulatory affairs expert review the 3 failures + 5 near-misses |

---

## Detailed Findings

### 1. LLM Judge Not Validated Against Human Labels

**Status:** Critical — judge deployed with zero validation data

The `judge_criterion()` function in `eval.py` uses Claude Haiku with mode-of-3 voting. Each rubric criterion gets a YES/NO judgment on whether the answer satisfies it.

**What's good:**
- Binary pass/fail at criterion level (not Likert scales)
- Deterministic numeric pre-check auto-passes when all numbers match within 2% — skips the LLM entirely for factual numeric questions
- Mode-of-3 reduces single-call variance
- Judge prompts are failure-mode-specific (separate prompts for "correctness" vs "contradiction")

**What's missing:**
- No confusion matrix (TP/FP/TN/FN) for the judge
- No TPR/TNR measurement — we don't know if the judge catches failures or just rubber-stamps
- No human labels to validate against
- Alignment measured only as aggregate accuracy (84% finance, 90% FDA), not per-operator

**Known judge issues from this eval:**
- Q10: Judge accepted 85 days vs ground truth 94 days (numeric pre-check auto-passed within 2% tolerance — but 85 vs 94 is 10% off, so the pre-check logic may have a bug)
- Q28: Judge validated 510(k) count (238) but ignored MAUDE count mismatch (119 vs 386) — rubric only checked one of the two numbers

**Fix:** Run `validate-evaluator`. Collect human pass/fail labels on 50+ criterion judgments (stratified: ~25 where judge said pass, ~25 where judge said fail). Compute TPR and TNR. Target: both > 90%.

### 2. No Systematic Error Analysis for FDA Domain

**Status:** High — failures diagnosed but no systematic catalog

The finance domain has `docs/problems.md` with 103 structured entries (P1–P103), each with observed behavior, root cause, solution, and status. The FDA domain has zero.

The 3 FDA failures are diagnosed in `docs/fda-eval-results.md` with application-grounded root causes (good), but:
- No structured error IDs (no FDA-P1, FDA-P2)
- No near-miss tracking (Q10, Q19, Q28 noted but not categorized)
- No proactive failure mode brainstorming
- No failure taxonomy

**What IS good:** The failure categories are observed from traces, not brainstormed from generic labels. "Missing tool capability" and "tool output formatting" are application-specific, not "hallucination" or "reasoning error."

**Fix:** Run `error-analysis` on the 30 eval traces. Read each trace end-to-end (not just the final answer). Build a failure catalog with structured entries. Expand to 100 traces by running the agent on more questions.

### 3. Ground Truth Drifts With Live API Data

**Status:** High — 4 of 30 questions showed drift in a 1-day window

openFDA data updates continuously. Between ground truth capture (April 21) and eval run (April 22), counts shifted:
- Q10: median clearance time 94→85 days (new clearances changed the distribution)
- Q19: HeartMate 3 events 5,188→5,189 (one new report added)
- Q28: MAUDE count mismatch (test definition issue — ground truth was all-time, agent searched with date filter)
- Q30: median clearance time 124→142.5 days (same drift as Q10)

**Fix:** For each benchmark question, save the canonical API response alongside the expected answer. The eval harness should compare against the frozen response, not re-query live data. Add `ground_truth_captured_at` dates (already present as `as_of_date`). For count-based questions, consider ±5% tolerance or freeze the exact record set.

### 4. Judge Sees Final Answer Only

**Status:** High — judge cannot verify reasoning

The judge prompt receives only `answer[:3000]` — the final formatted text. It does not see:
- What tools were called
- What the API returned
- What the planner decided
- What the extractor matched

This means the judge can only verify that the right number appears in the answer, not that it was derived correctly. A hallucinated correct number would pass.

**Where this matters:** For Q16 (DSQ life-sustaining flag), the openFDA API returned `life_sustain_support_flag: "Y"`, but the agent said "No." The judge correctly caught this because the answer contradicted the criterion. But if the agent had hallucinated "Yes" without actually checking the API, the judge would have passed it.

**Fix:** For a subset of critical questions, pass `tool_log` context to the judge so it can verify the reasoning chain, not just the final answer. This is especially important for questions where the answer is a yes/no or a single number — the judge needs to see WHY the agent reached that conclusion.

### 5. Only 30 Labeled Examples

**Status:** Medium — sufficient for a demo, insufficient for robust eval

The `error-analysis` skill recommends ~100 traces for saturation. The `validate-evaluator` skill needs ~50 pass + ~50 fail examples. With 27 pass and 3 fail, the fail class is severely underrepresented.

**Fix:** Expand the benchmark in two phases:
1. Add 20-30 more questions targeting known weak areas (median computation, multi-source cross-reference, recall lookups)
2. Include negative/adversarial examples (see finding #6)

### 6. No Negative or Adversarial Test Cases

**Status:** Medium — all 30 questions are answerable

The benchmark has zero:
- **Refusal tests:** Questions the agent should answer "I can't determine this" (e.g., "What is the clearance date for K999999?" — fake K-number)
- **Adversarial inputs:** Misspelled K-numbers, ambiguous product codes, questions requiring data not in openFDA
- **Out-of-scope questions:** Questions requiring ClinicalTrials.gov (explicitly out of v1 scope)

The agent's bias is toward finding answers, not abstaining. Without refusal tests, we don't know if it fabricates answers for unanswerable questions.

**Fix:** Add 5-10 negative test cases with rubric criteria like `{"operator": "correctness", "criteria": "States that the data is not available or the K-number does not exist"}`.

### 7. Proposed Fixes Not Re-Validated

**Status:** Medium — three fixes proposed, none verified

The eval results doc proposes:
- Q16: "Improve classification tool output to highlight life-sustaining flags"
- Q24: "Add recall number search to `search_recalls()`"
- Q30: "Freeze ground truth with exact record set"

None have been implemented and re-evaluated. There's no automated loop that re-runs the eval after tool changes.

**Fix:** After implementing each fix, re-run at minimum the affected question (e.g., `--indices 15 23 29`) to verify. The cost is ~$0.60 for 3 questions.

### 8. No Human Domain Expert Reviewed Traces

**Status:** Medium — appropriate for current stage, but needed before publishing

All evaluation is automated (LLM judge + programmatic ground truth). No regulatory affairs professional has reviewed:
- Whether the 30 questions represent realistic regulatory workflows
- Whether the ground truth answers are complete (not just correct)
- Whether the agent's reasoning process is sound (not just the final number)

**Fix:** Have one domain expert review the 3 failures + 5 near-misses (Q10, Q19, Q28 + two showcase questions). This is ~2 hours of work and would catch any ground truth errors or unrealistic question framing.

---

## What's Working Well

These areas passed the audit:

- **Binary pass/fail evaluators** — criterion-level judgments are binary YES/NO, not Likert scales
- **Code-based checks where possible** — deterministic numeric pre-check handles factual numbers without LLM variance
- **No similarity metrics** — no ROUGE/BERTScore (appropriate for fact-based QA)
- **Failure-mode-specific judge prompts** — separate prompts for correctness vs contradiction, with concrete numeric tolerance examples
- **Application-grounded failure categories** — "missing tool capability" not "hallucination"
- **Ground truth provenance** — each question has an `as_of_date` and answers are sourced from specific API queries

---

## Recommended Next Steps (in order)

| Priority | Action | Skill | Effort | Impact |
|----------|--------|-------|--------|--------|
| 1 | Implement the 3 proposed fixes (Q16, Q24, Q30) and re-run affected questions | — | 2 hours | Fixes 3 known failures |
| 2 | Collect 50 human labels on judge criterion verdicts, compute TPR/TNR | `validate-evaluator` | 3-4 hours | Validates whether the judge is trustworthy |
| 3 | Add 10 negative/adversarial test cases | — | 2 hours | Catches hallucination-on-unanswerable |
| 4 | Freeze canonical API responses for count-based ground truth | — | 1 hour | Eliminates drift false-failures |
| 5 | Run systematic error analysis on all 30 traces | `error-analysis` | 4 hours | Builds failure catalog for FDA domain |
| 6 | Have domain expert review 8 traces (3 failures + 5 near-misses) | — | 2 hours | Catches ground truth errors |
