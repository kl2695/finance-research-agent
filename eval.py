"""FAB Benchmark Evaluation Harness.

Runs the agent against the Vals AI Finance Agent Benchmark public set,
scores answers against rubrics using LLM-as-judge, and outputs a report.

Usage:
    python eval.py                    # Run all 50 questions
    python eval.py --indices 0 2 9    # Run specific questions
    python eval.py --score-only FILE  # Score a previous run without re-running
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from datasets import load_dataset

from src.agent import run
from src.llm import call_claude, MODEL_HAIKU

log = logging.getLogger(__name__)

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)


def load_fab_dataset():
    """Load the FAB public validation set."""
    return load_dataset("vals-ai/finance_agent_benchmark", split="train")


# Benchmark epoch date — the FAB benchmark GT assumes answers as of this date.
# Biden blocked US Steel merger Jan 3 2025, FY2024 was latest complete fiscal year.
BENCHMARK_DATE = "2025-02-01"


def run_question(question: str, idx: int) -> dict:
    """Run a single question and return the result with metadata.

    Saves full pipeline state for offline replay testing (Approach C).
    """
    log.info(f"[{idx:2d}] Running: {question[:80]}...")
    try:
        t0 = time.time()
        result = run(question, as_of_date=BENCHMARK_DATE)
        elapsed = time.time() - t0

        # Strip internal keys that aren't JSON-serializable
        state = result.get("state", {})
        state.pop("_tool_log", None)

        return {
            "idx": idx,
            "question": question,
            "answer": result["answer"],
            "elapsed": elapsed,
            "state": state,
            "tool_log": result.get("tool_log", []),
            "research_text": result.get("research_text", "")[:2000],
            "error": None,
        }
    except Exception as e:
        log.error(f"[{idx:2d}] Error: {e}")
        return {
            "idx": idx,
            "question": question,
            "answer": "",
            "elapsed": 0,
            "state": {},
            "tool_log": [],
            "research_text": "",
            "error": str(e),
        }


def _extract_numbers(text: str) -> list[float]:
    """Extract all numbers from text for deterministic comparison."""
    import re
    numbers = []
    # Match: $X.XX, X.XX%, X,XXX, plain decimals
    for m in re.finditer(r'[\$]?\s*([\d,]+\.?\d*)\s*(%|billion|million|bps)?', text):
        try:
            val = float(m.group(1).replace(",", ""))
            unit = m.group(2) or ""
            if "billion" in unit.lower():
                val *= 1e9
            elif "million" in unit.lower():
                val *= 1e6
            numbers.append(val)
        except ValueError:
            pass
    return numbers


def _numeric_match(answer: str, criterion: str, tolerance: float = 0.02) -> bool | None:
    """Deterministic numeric check — returns True/False if numbers match, None if can't determine."""
    criterion_nums = _extract_numbers(criterion)
    if not criterion_nums:
        return None  # No numbers in criterion — can't do numeric check

    answer_nums = _extract_numbers(answer)
    if not answer_nums:
        return None  # No numbers in answer — let LLM judge

    # Check if ALL numbers in criterion have a close match in answer
    for crit_num in criterion_nums:
        if crit_num == 0:
            continue
        found_match = any(
            abs(ans_num - crit_num) / abs(crit_num) < tolerance
            for ans_num in answer_nums
        )
        if not found_match:
            return None  # At least one number not matched — let LLM decide

    return True  # All criterion numbers found in answer within tolerance


def judge_criterion(answer: str, criterion: str, operator: str) -> dict:
    """Use LLM to judge whether an answer satisfies a rubric criterion.

    Returns {"pass": bool, "reasoning": str}.
    """
    # Deterministic numeric pre-check for correctness criteria
    # If all numbers in the criterion are found in the answer (within 2%), auto-pass
    if operator == "correctness":
        numeric_result = _numeric_match(answer, criterion)
        if numeric_result is True:
            return {"pass": True, "reasoning": "Deterministic numeric match (within 2% tolerance)"}

    if operator == "correctness":
        prompt = f"""Does the following answer contain or support this claim?
Answer with ONLY "YES" or "NO" on the first line, then a brief reason.

IMPORTANT: For numeric values, accept answers within 2% tolerance.
For example, if the claim says "$467 million" and the answer says "$466 million", that is YES (within tolerance).
Similarly, "26.1 bps" and "26.08 bps" should be YES. "13.3%" and "13.29%" should be YES.
Only mark NO if the number is materially different (>5% off) or completely absent.

CLAIM: {criterion}

ANSWER: {answer[:3000]}"""
    elif operator == "contradiction":
        prompt = f"""Does the following answer contradict this statement?
Answer with ONLY "YES" or "NO" on the first line, then a brief reason.
Note: if the answer simply doesn't mention the statement, that is NOT a contradiction.

STATEMENT: {criterion}

ANSWER: {answer[:3000]}"""
    else:
        return {"pass": False, "reasoning": f"Unknown operator: {operator}"}

    # Mode-of-3 judging (from Vals AI v1.1 methodology): run 3x, take majority vote.
    # Eliminates judge non-determinism where Haiku gives different answers each call.
    votes = []
    last_reasoning = ""
    for _ in range(3):
        try:
            response = call_claude(
                system="You are a precise evaluator. Judge whether an answer meets a specific criterion.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                model=MODEL_HAIKU,
            )
            text = response.content[0].text.strip()
            first_line = text.split("\n")[0].strip().upper()

            if operator == "correctness":
                voted_pass = first_line.startswith("YES")
            else:  # contradiction
                voted_pass = not first_line.startswith("YES")

            votes.append(voted_pass)
            last_reasoning = text[:200]
        except Exception as e:
            votes.append(False)
            last_reasoning = f"Judge error: {e}"

    # Majority vote (2/3 or 3/3 = pass)
    passed = sum(votes) >= 2
    return {"pass": passed, "reasoning": f"[{sum(votes)}/3 votes] {last_reasoning}"}


def score_answer(answer: str, rubric_str: str) -> dict:
    """Score an answer against all rubric criteria.

    Returns {"score": float, "criteria_results": [...], "pass": bool}.
    """
    rubric = ast.literal_eval(rubric_str) if isinstance(rubric_str, str) else rubric_str
    criteria_results = []

    for criterion in rubric:
        op = criterion["operator"]
        crit = criterion["criteria"]
        result = judge_criterion(answer, crit, op)
        criteria_results.append({
            "operator": op,
            "criteria": crit[:100],
            "pass": result["pass"],
            "reasoning": result["reasoning"][:100],
        })

    # Score: fraction of criteria passed
    n_passed = sum(1 for r in criteria_results if r["pass"])
    n_total = len(criteria_results)
    score = n_passed / n_total if n_total > 0 else 0

    return {
        "score": score,
        "pass": score >= 0.5,  # Pass if majority of criteria met
        "criteria_passed": n_passed,
        "criteria_total": n_total,
        "criteria_results": criteria_results,
    }


def run_eval(indices: list[int] | None = None) -> dict:
    """Run the full evaluation. Returns results dict."""
    ds = load_fab_dataset()
    all_indices = indices if indices is not None else list(range(len(ds)))

    results = []
    for idx in all_indices:
        row = ds[idx]
        question = row["Question"]
        gt_answer = row["Answer"]
        rubric = row["Rubric"]
        q_type = row["Question Type"]

        # Run the agent
        agent_result = run_question(question, idx)

        # Score against rubric
        if agent_result["error"]:
            score_result = {"score": 0, "pass": False, "criteria_passed": 0,
                           "criteria_total": 0, "criteria_results": []}
        else:
            score_result = score_answer(agent_result["answer"], rubric)

        results.append({
            "idx": idx,
            "question_type": q_type,
            "question": question[:100],
            "gt_answer": gt_answer[:100],
            "agent_answer": agent_result["answer"][:200],
            "elapsed": agent_result["elapsed"],
            "error": agent_result["error"],
            # Saved for cheap re-eval (extraction replay without re-running agent)
            "tool_log": agent_result.get("tool_log", []),
            "state": agent_result.get("state", {}),
            "research_text": agent_result.get("research_text", ""),
            **score_result,
        })

        status = "PASS" if score_result["pass"] else "FAIL"
        log.info(f"[{idx:2d}] {status} ({score_result['criteria_passed']}/{score_result['criteria_total']}) "
                 f"| {agent_result['elapsed']:.0f}s | {q_type}")

    return _summarize(results)


def score_existing(filepath: str) -> dict:
    """Score a previously saved run without re-running the agent."""
    with open(filepath) as f:
        data = json.load(f)

    ds = load_fab_dataset()
    results = []

    for entry in data.get("results", []):
        idx = entry["idx"]
        row = ds[idx]
        rubric = row["Rubric"]

        score_result = score_answer(entry.get("agent_answer", ""), rubric)

        results.append({
            **entry,
            **score_result,
        })

        status = "PASS" if score_result["pass"] else "FAIL"
        log.info(f"[{idx:2d}] {status} ({score_result['criteria_passed']}/{score_result['criteria_total']})")

    return _summarize(results)


def _summarize(results: list[dict]) -> dict:
    """Generate summary statistics and save results."""
    n_total = len(results)
    n_pass = sum(1 for r in results if r["pass"])
    n_fail = n_total - n_pass
    avg_score = sum(r["score"] for r in results) / n_total if n_total > 0 else 0
    total_time = sum(r["elapsed"] for r in results)

    # Breakdown by question type
    type_stats = {}
    for r in results:
        qtype = r.get("question_type", "Unknown")
        if qtype not in type_stats:
            type_stats[qtype] = {"total": 0, "pass": 0}
        type_stats[qtype]["total"] += 1
        if r["pass"]:
            type_stats[qtype]["pass"] += 1

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_questions": n_total,
        "n_pass": n_pass,
        "n_fail": n_fail,
        "accuracy": n_pass / n_total if n_total > 0 else 0,
        "avg_criteria_score": avg_score,
        "total_time_seconds": total_time,
        "type_breakdown": type_stats,
        "results": results,
    }

    # Save to file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = RESULTS_DIR / f"eval_{timestamp}.json"
    with open(filepath, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Print report
    print("\n" + "=" * 60)
    print(f"FAB EVALUATION REPORT — {summary['timestamp'][:10]}")
    print("=" * 60)
    print(f"Questions: {n_total} | Pass: {n_pass} | Fail: {n_fail}")
    print(f"Accuracy: {summary['accuracy']:.1%} | Avg criteria score: {avg_score:.1%}")
    print(f"Total time: {total_time:.0f}s ({total_time/60:.1f} min)")
    print()
    print("BY QUESTION TYPE:")
    for qtype, stats in sorted(type_stats.items()):
        pct = stats["pass"] / stats["total"] if stats["total"] > 0 else 0
        print(f"  {qtype:30s} {stats['pass']}/{stats['total']} ({pct:.0%})")
    print()
    print("FAILURES:")
    for r in results:
        if not r["pass"]:
            print(f"  [{r['idx']:2d}] {r['question_type']:20s} | {r['question'][:60]}")
            for cr in r.get("criteria_results", []):
                if not cr["pass"]:
                    print(f"       MISS: {cr['criteria'][:80]}")
    print()
    print(f"Results saved to: {filepath}")
    print("=" * 60)

    return summary


def cheap_reeval(filepath: str) -> dict:
    """Cheap re-eval: loads saved tool_logs, runs FRESH extraction + calculation,
    then calls formatter + judge for a real score. ~$1.50 instead of $8-10.

    Use this after changing extraction/calculation code to see if the score improved.
    Requires a previous full eval run that saved tool_logs.
    """
    from src.extractor import extract_from_tool_log, _parse_filing_text, llm_match_facts_to_keys
    from src.calculator import execute_calculations
    from src.prompts import ANSWER_SYSTEM, ANSWER_PROMPT
    from src.llm import call_claude

    with open(filepath) as f:
        data = json.load(f)

    ds = load_fab_dataset()
    results = []

    for entry in data.get("results", []):
        idx = entry["idx"]
        tool_log_data = entry.get("tool_log", [])
        state = entry.get("state", {})
        row = ds[idx]
        question = row["Question"]
        rubric = row["Rubric"]
        q_type = row["Question Type"]

        if not tool_log_data or not state.get("data_needed"):
            # No tool_log saved — can't replay, use original answer
            answer = entry.get("agent_answer", "")
        else:
            # Fresh extraction on saved tool_logs
            # Clear existing values
            for dp in state.get("data_needed", {}).values():
                if isinstance(dp, dict):
                    dp["value"] = None
                    dp["source"] = None

            # Step 3a: XBRL extraction
            xbrl_log = [t for t in tool_log_data if t.get("tool") == "sec_edgar_financials"]
            if xbrl_log:
                state = extract_from_tool_log(xbrl_log, state)

            # Step 3b: Structured text extraction
            prose_log = [t for t in tool_log_data if t.get("tool") in ("sec_edgar_earnings", "sec_edgar_filing_text")]
            if prose_log:
                guidance_keys = {}
                for k, dp in list(state.get("data_needed", {}).items()):
                    if "guid" in k.lower() and isinstance(dp, dict) and dp.get("value") is None:
                        guidance_keys[k] = state["data_needed"].pop(k)
                state = extract_from_tool_log(prose_log, state)
                state["data_needed"].update(guidance_keys)

            # Step 3c: LLM fact matching (costs ~$0.004 per question)
            still_unfilled = [k for k, dp in state.get("data_needed", {}).items()
                            if isinstance(dp, dict) and dp.get("value") is None]
            if still_unfilled and prose_log:
                all_facts = []
                for source_idx, e in enumerate(prose_log):
                    facts = _parse_filing_text(e.get("output", ""))
                    for f in facts:
                        f["source_idx"] = source_idx
                    all_facts.extend(facts)
                if all_facts:
                    state = llm_match_facts_to_keys(all_facts, state)

            # Step 4: Calculator
            calc_steps = state.get("calculation_steps", [])
            if calc_steps:
                for step in calc_steps:
                    step["result"] = None
                state["answer"] = {"value": None, "formatted": None, "sources": [], "work_shown": None}
                state = execute_calculations(state)

            # Step 5: Formatter (costs ~$0.01 per question)
            research_text = entry.get("research_text", "")
            try:
                import json as _json
                fmt_response = call_claude(
                    system=ANSWER_SYSTEM,
                    messages=[{"role": "user", "content": ANSWER_PROMPT.format(
                        question=question,
                        state=_json.dumps(state, indent=2, default=str)[:4000],
                    )}],
                    max_tokens=1024,
                )
                answer = fmt_response.content[0].text
            except Exception:
                answer = entry.get("agent_answer", "")

        # Step 6: Judge (costs ~$0.02 per question)
        score_result = score_answer(answer, rubric)

        results.append({
            "idx": idx,
            "question_type": q_type,
            "question": question[:100],
            "gt_answer": row["Answer"][:100],
            "agent_answer": answer[:200],
            "elapsed": 0,
            "error": None,
            **score_result,
        })

        status = "PASS" if score_result["pass"] else "FAIL"
        log.info(f"[{idx:2d}] {status} ({score_result['criteria_passed']}/{score_result['criteria_total']}) | {q_type}")

    return _summarize(results)


def main():
    parser = argparse.ArgumentParser(description="FAB Benchmark Evaluation")
    parser.add_argument("--indices", type=int, nargs="+", help="Specific question indices to run")
    parser.add_argument("--score-only", type=str, help="Score a previous run file without re-running")
    parser.add_argument("--cheap-reeval", type=str, help="Cheap re-eval: fresh extraction + scoring on saved tool_logs (~$1.50)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(message)s")

    if args.score_only:
        score_existing(args.score_only)
    elif args.cheap_reeval:
        cheap_reeval(args.cheap_reeval)
    else:
        run_eval(args.indices)


if __name__ == "__main__":
    main()
