"""CLI entry point for the finance research agent.

Usage:
    python run.py "What was Apple's total revenue for FY2024?"
    python run.py --file questions.txt
"""

import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")

from src.agent import run


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print('  python run.py "Your financial question here"')
        print("  python run.py --file questions.txt")
        sys.exit(1)

    if sys.argv[1] == "--file":
        with open(sys.argv[2]) as f:
            questions = [line.strip() for line in f if line.strip()]
    else:
        questions = [" ".join(sys.argv[1:])]

    results = []
    for i, q in enumerate(questions):
        print(f"\n{'='*60}")
        print(f"Question {i+1}/{len(questions)}: {q[:80]}...")
        print(f"{'='*60}")

        result = run(q)
        results.append(result)

        print(f"\nANSWER: {result['answer'][:500]}")
        print(f"\nTime: {result['elapsed']:.1f}s | Tools: {len(result['tool_log'])}")

    # Save results
    Path("results").mkdir(exist_ok=True)
    out = Path("results") / "latest.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nResults saved to {out}")

    # Save detailed action log
    log_path = Path("results") / "latest_actions.log"
    with open(log_path, "w") as f:
        for r in results:
            f.write(f"{'='*60}\n")
            f.write(f"Q: {r['question']}\n")
            f.write(f"Time: {r['elapsed']:.1f}s\n")
            f.write(f"Plan: {r.get('state', {}).get('plan', '?')}\n\n")

            f.write("TOOL CALLS:\n")
            for i, t in enumerate(r.get("tool_log", [])):
                tool = t.get("tool", "?")
                inp = json.dumps(t.get("input", {}))[:100]
                out_preview = t.get("output", "")[:150]
                f.write(f"  [{i}] {tool}: {inp}\n")
                if tool != "web_search_result":
                    f.write(f"       → {out_preview}\n")

            f.write(f"\nSTATE AFTER EXTRACTION:\n")
            for k, dp in r.get("state", {}).get("data_needed", {}).items():
                if isinstance(dp, dict):
                    v = dp.get("value")
                    src = dp.get("source", "?")
                    f.write(f"  {k}: {v} (source: {src})\n")

            f.write(f"\nCALCULATIONS:\n")
            for s in r.get("state", {}).get("calculation_steps", []):
                f.write(f"  {s.get('step')}: {s.get('formula')} = {s.get('result')}\n")

            f.write(f"\nANSWER: {r.get('answer', '')[:300]}\n\n")

    print(f"Action log saved to {log_path}")


if __name__ == "__main__":
    main()
