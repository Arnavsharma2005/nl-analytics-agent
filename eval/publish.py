"""Build the public eval page's data from the runs in eval/results/.

Usage (from the repo root):
    python eval/publish.py           # write apps/web-next/public/eval-report.json
    python eval/publish.py --check   # exit 1 if it is out of date (CI)

The page leads with the latest real run that answered every held-out (test) question: those were never used to
tune prompts or verified queries, so that score is the one to quote. Replays of a recorded run are left out, since
they re-read old model answers rather than measure anything new. A run only counts once its results file is
committed, so the page shows what anyone can check.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"
OUT = ROOT / "apps" / "web-next" / "public" / "eval-report.json"
SETS = {"questions": ROOT / "eval" / "questions.yaml", "conversations": ROOT / "eval" / "conversations.yaml"}


def test_ids() -> dict[str, set]:
    """The ids of the held-out questions in each set, as the runs record them."""
    questions = yaml.safe_load(SETS["questions"].read_text(encoding="utf-8"))
    conversations = yaml.safe_load(SETS["conversations"].read_text(encoding="utf-8"))
    return {
        "questions": {q["id"] for q in questions if q["split"] == "test"},
        "conversations": {f"{c['id']}.{n}" for c in conversations if c["split"] == "test"
                          for n in range(1, len(c["turns"]) + 1)},
    }


def which_set(run: dict) -> str:
    # Runs record their set from v2.3 on; before that, multi-turn runs are known by their "c1.2"-style ids.
    return run["summary"].get("set") or (
        "conversations" if any(isinstance(r["id"], str) for r in run["results"]) else "questions")


def score(results: list[dict]) -> dict:
    correct = sum(r["correct"] for r in results)
    return {"correct": correct, "questions": len(results),
            "accuracy_pct": round(100 * correct / len(results), 1) if results else None}


def tally(results: list[dict], field: str) -> dict[str, dict]:
    groups = defaultdict(list)
    for r in results:
        groups[r[field]].append(r)
    return {name: score(group) for name, group in sorted(groups.items())}


def describe(path: Path, run: dict, held_out: set) -> dict:
    """One run as the page shows it: its score on the held-out questions when it answered all of them."""
    summary, results = run["summary"], run["results"]
    test = [r for r in results if r["id"] in held_out]
    complete = bool(held_out) and {r["id"] for r in test} == held_out
    return {
        "file": path.name,
        "run_at": summary["run_at"],
        "commit": summary["commit"],
        "set": which_set(run),
        "models": summary.get("providers_configured", []),
        "sql_written_by": summary.get("sql_written_by", {}),
        "features": summary.get("features", []),
        "all": score(results),
        "held_out": score(test) if complete else None,
        "median_latency_s": summary.get("median_latency_s"),
    }


def headline(run: dict, held_out: set) -> dict:
    """The held-out questions of the run the page leads with, broken down and listed one by one."""
    test = [r for r in run["results"] if r["id"] in held_out]  # in the order of the question set
    return {
        "by_difficulty": tally(test, "difficulty"),
        "by_category": tally(test, "category"),
        "repaired_by_retry": sum(1 for r in test if r["correct"] and r["attempts"] > 1),
        "questions": [{"id": r["id"], "question": r["question"], "category": r["category"],
                       "difficulty": r["difficulty"], "correct": r["correct"], "attempts": r["attempts"],
                       "sql": r["sql"], "error": (r["error"] or "")[:300] or None} for r in test],
    }


def build(results_dir: Path = RESULTS) -> dict:
    ids = test_ids()
    runs = []
    for path in sorted(results_dir.glob("*.json")) if results_dir.exists() else []:
        run = json.loads(path.read_text(encoding="utf-8"))
        if any(str(m).startswith("replay of") for m in run["summary"].get("providers_configured", [])):
            continue
        runs.append((path, run))
    runs.sort(key=lambda pr: pr[1]["summary"]["run_at"], reverse=True)

    report = {"held_out_questions": {name: len(i) for name, i in ids.items()}, "runs": [], "latest": {}}
    for path, run in runs:
        held_out = ids[which_set(run)]
        described = describe(path, run, held_out)
        report["runs"].append(described)
        if described["held_out"] and described["set"] not in report["latest"]:
            report["latest"][described["set"]] = {**described, **headline(run, held_out)}
    return report


def render(report: dict) -> str:
    return json.dumps(report, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="exit 1 if the page's data is out of date")
    args = parser.parse_args()
    report = build()
    text = render(report)
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python eval/publish.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(report['runs'])} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
