"""Run the labelled question set through the full agent and report SQL correctness.

Usage (from the repo root, with DATABASE_URL and an LLM key set):
    python eval/run_eval.py                 # all 120 questions
    python eval/run_eval.py --split test    # the 70 held-out questions: the number to report
    python eval/run_eval.py --ids 1 5 17    # a subset
    python eval/run_eval.py --gold-only     # just check that every gold query runs (no LLM needed)
    python eval/run_eval.py --delay 4       # pause between questions to stay under free-tier rate limits

Each run is written to eval/results/<date>-<commit>.json (or --out); compare two runs with eval/compare.py.

Replay baseline: record one real run, commit both files, and CI re-runs the pipeline from the saved model answers
on every pull request, failing if any question that passed now fails:
    python eval/run_eval.py --record eval/baseline/cassette.json --out eval/baseline/results.json
    python eval/run_eval.py --replay eval/baseline/cassette.json --out replay.json   # no keys needed
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.config import get_settings  # noqa: E402
from app.db import Database, QueryError  # noqa: E402
from app.evaluation import results_match  # noqa: E402


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", default=str(ROOT / "eval" / "questions.yaml"))
    parser.add_argument("--ids", type=int, nargs="*")
    parser.add_argument("--split", choices=["dev", "test"], help="only the dev (tuning) or test (held-out) questions")
    parser.add_argument("--gold-only", action="store_true")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--out", help="where to write the results (default eval/results/<date>-<commit>.json)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--record", type=Path, help="save every model answer to this cassette")
    source.add_argument("--replay", type=Path, help="answer from this cassette instead of calling a model")
    args = parser.parse_args()

    settings = get_settings()
    db = Database(settings.database_url, settings.statement_timeout_ms, max_rows=10_000,
                  max_cost=settings.max_query_cost, personal_columns=settings.personal_data_columns)
    questions = yaml.safe_load(Path(args.questions).read_text())
    if args.ids:
        questions = [q for q in questions if q["id"] in set(args.ids)]
    if args.split:
        questions = [q for q in questions if q["split"] == args.split]

    if args.gold_only:
        bad = 0
        for q in questions:
            try:
                rows = db.run(q["gold_sql"]).rows
                print(f"#{q['id']:>2} ok   {len(rows):>3} rows  {q['question']}")
            except QueryError as e:
                bad += 1
                print(f"#{q['id']:>2} FAIL {e}")
        return 1 if bad else 0

    from app.analyst import Analyst
    from app.providers import build_llm
    from app.replay import RecordingLLM, ReplayLLM

    if args.replay:
        llm, providers = ReplayLLM(args.replay), [f"replay of {args.replay.name}"]
    else:
        real = build_llm(settings)
        providers = [p["label"] for p in real.status() if p["configured"]]
        llm = RecordingLLM(real) if args.record else real
    analyst = Analyst(llm, db, max_attempts=settings.max_sql_attempts, rows_for_llm=settings.rows_for_llm)

    results = []
    for i, q in enumerate(questions):
        if i and args.delay and not args.replay:
            time.sleep(args.delay)
        gold = db.run(q["gold_sql"]).rows
        start = time.perf_counter()
        try:
            answer = analyst.ask(q["question"])
            error = answer.error
        except Exception as e:  # noqa: BLE001 - one bad question should not stop the run
            answer, error = None, f"{type(e).__name__}: {e}"
        latency = time.perf_counter() - start
        pred = answer.rows if answer and answer.intent == "data_query" else None
        correct = pred is not None and not error and results_match(gold, pred, q.get("ordered", False))
        results.append({
            "id": q["id"], "category": q["category"], "difficulty": q["difficulty"], "split": q["split"],
            "question": q["question"], "correct": correct,
            "intent": answer.intent if answer else None, "sql": answer.sql if answer else None,
            "attempts": answer.attempts if answer else 0, "latency_s": round(latency, 2), "error": error,
            "gold_rows": len(gold), "pred_rows": None if pred is None else len(pred),
            # The provider that wrote the final SQL (fallbacks can mix models within one run).
            "sql_provider": next((c["provider"] for c in reversed(answer.llm_calls if answer else [])
                                  if c["step"] == "sql_agent" and c["ok"]), None),
        })
        mark = "PASS" if correct else "FAIL"
        print(f"#{q['id']:>2} {mark} {latency:5.1f}s  attempts={results[-1]['attempts']}  {q['question']}")
        if not correct and error:
            print(f"      error: {error[:200]}")

    if args.record:
        llm.save(args.record)
        print(f"\nRecorded model answers to {args.record}")

    def tally(field: str) -> dict[str, str]:
        groups = defaultdict(list)
        for r in results:
            groups[r[field]].append(r["correct"])
        return {k: f"{sum(v)}/{len(v)}" for k, v in groups.items()}

    run_at = datetime.now(UTC)
    commit = git_commit()
    summary = {
        "providers_configured": providers,
        "sql_written_by": dict(Counter(r["sql_provider"] for r in results if r["sql_provider"])),
        "run_at": run_at.isoformat(timespec="seconds"),
        "commit": commit,
        "questions": len(results),
        "correct": sum(r["correct"] for r in results),
        "accuracy_pct": round(100 * sum(r["correct"] for r in results) / max(len(results), 1), 1),
        "by_split": tally("split"),
        "by_difficulty": tally("difficulty"),
        "by_category": tally("category"),
        "repaired_by_retry": sum(1 for r in results if r["correct"] and r["attempts"] > 1),
        "median_latency_s": round(statistics.median(r["latency_s"] for r in results), 2) if results else None,
    }
    out = Path(args.out) if args.out else ROOT / "eval" / "results" / f"{run_at:%Y-%m-%d-%H%M}-{commit}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2) + "\n")
    print("\n" + json.dumps(summary, indent=2))
    print(f"Results written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
