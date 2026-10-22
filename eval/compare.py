"""Compare two eval runs question by question.

Usage (from the repo root):
    python eval/compare.py eval/results/before.json eval/results/after.json
    python eval/compare.py before.json after.json --fail-on-regression   # exit 1 if any question went PASS -> FAIL

Only questions present in both runs are compared.
"""

import argparse
import json
from pathlib import Path


def load(path: str) -> tuple[dict, dict[int, dict]]:
    data = json.loads(Path(path).read_text())
    return data["summary"], {r["id"]: r for r in data["results"]}


def compare(before: dict[int, dict], after: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    """Return (fixed, regressed): questions that went FAIL -> PASS and PASS -> FAIL."""
    shared = sorted(before.keys() & after.keys())
    fixed = [after[i] for i in shared if not before[i]["correct"] and after[i]["correct"]]
    regressed = [after[i] for i in shared if before[i]["correct"] and not after[i]["correct"]]
    return fixed, regressed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args()

    (sb, before), (sa, after) = load(args.before), load(args.after)
    shared = before.keys() & after.keys()
    acc_b = sum(before[i]["correct"] for i in shared)
    acc_a = sum(after[i]["correct"] for i in shared)
    print(f"before  {acc_b}/{len(shared)}  ({sb.get('commit', '?')}, {sb.get('run_at', '?')})")
    print(f"after   {acc_a}/{len(shared)}  ({sa.get('commit', '?')}, {sa.get('run_at', '?')})")

    fixed, regressed = compare(before, after)
    for title, rows in (("Fixed", fixed), ("Regressed", regressed)):
        if rows:
            print(f"\n{title}:")
            for r in rows:
                print(f"  #{r['id']:>2} [{r['category']}] {r['question']}")
                if r.get("error"):
                    print(f"       error: {r['error'][:200]}")
    if not fixed and not regressed:
        print("\nNo question changed outcome.")
    return 1 if args.fail_on_regression and regressed else 0


if __name__ == "__main__":
    raise SystemExit(main())
