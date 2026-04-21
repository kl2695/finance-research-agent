"""Multi-domain research agent CLI.

Usage:
    python main.py --domain finance --question "What was Lyft's Q4 2024 revenue?"
    python main.py --domain fda --question "What is the clearance date for K213456?"
    python main.py --domain finance --eval
    python main.py --list-domains
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from core.agent import run
from domains import get_domain, list_domains


def main():
    p = argparse.ArgumentParser(description="Multi-domain research agent")
    p.add_argument("--domain", type=str,
                   default=os.environ.get("RESEARCH_DOMAIN", "finance"),
                   help=f"Research domain (available: {', '.join(list_domains())})")
    p.add_argument("--question", type=str, help="Question to research")
    p.add_argument("--as-of-date", type=str, default=None,
                   help="Date override (YYYY-MM-DD) for the planner")
    p.add_argument("--eval", action="store_true", help="Run benchmark evaluation")
    p.add_argument("--indices", type=int, nargs="*", default=None,
                   help="Run specific benchmark question indices")
    p.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    p.add_argument("--list-domains", action="store_true", help="List available domains")

    args = p.parse_args()

    if args.list_domains:
        for d in list_domains():
            print(d)
        return

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    domain = get_domain(args.domain)

    if args.eval:
        _run_eval(domain, indices=args.indices, verbose=args.verbose)
    elif args.question:
        result = run(args.question, domain=domain, as_of_date=args.as_of_date)
        print(result["answer"])
    else:
        p.print_help()


def _run_eval(domain, indices=None, verbose=False):
    """Run benchmark evaluation for a domain."""
    from core.agent import run as agent_run

    questions = domain.benchmark_questions
    if not questions:
        print(f"No benchmark questions for domain '{domain.name}'")
        return

    if indices is not None:
        questions = [q for i, q in enumerate(questions) if i in indices]

    # Import eval harness (still in eval.py for now)
    # The eval logic (mode-of-3, numeric pre-check) is already generic
    try:
        from eval import score_answer
    except ImportError:
        print("eval.py not found — using simple eval")
        score_answer = None

    results = []
    passed = 0
    for q in questions:
        as_of_date = q.as_of_date or domain.benchmark_date
        print(f"[{q.id}] {q.question[:70]}...")

        try:
            t0 = time.time()
            result = agent_run(q.question, domain=domain, as_of_date=as_of_date)
            elapsed = time.time() - t0

            if score_answer and q.rubric:
                score_result = score_answer(result["answer"], q.rubric)
                q_passed = score_result["pass"]
            else:
                q_passed = q.answer.lower() in result["answer"].lower() if q.answer else None

            status = "PASS" if q_passed else "FAIL"
            if q_passed:
                passed += 1
            print(f"  [{status}] {elapsed:.1f}s")

            results.append({
                "id": q.id,
                "question": q.question,
                "answer": result["answer"],
                "expected": q.answer,
                "pass": q_passed,
                "elapsed": elapsed,
            })
        except Exception as e:
            print(f"  [ERROR] {e}")
            results.append({"id": q.id, "question": q.question, "error": str(e), "pass": False})

    total = len(results)
    print(f"\n{domain.name} benchmark: {passed}/{total} ({100*passed/total:.0f}%)")

    # Save results
    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    outfile = results_dir / f"eval_{domain.name}_{ts}.json"
    with open(outfile, "w") as f:
        json.dump({"domain": domain.name, "passed": passed, "total": total, "results": results},
                  f, indent=2)
    print(f"Results saved to {outfile}")


if __name__ == "__main__":
    main()
