"""Run the labelled question set through the full agent and report SQL correctness.

Usage (from the repo root, with DATABASE_URL and an LLM key set):
    python eval/run_eval.py                 # all 30 questions
    python eval/run_eval.py --ids 1 5 17    # a subset
    python eval/run_eval.py --gold-only     # just check that every gold query runs (no LLM needed)
    python eval/run_eval.py --delay 4       # pause between questions to stay under free-tier rate limits
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import Database, QueryError  # noqa: E402
from app.evaluation import results_match  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", default=str(ROOT / "eval" / "questions.yaml"))
    parser.add_argument("--ids", type=int, nargs="*")
    parser.add_argument("--gold-only", action="store_true")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--out", default=str(ROOT / "eval" / "results.json"))
    args = parser.parse_args()

    settings = get_settings()
    db = Database(settings.database_url, settings.statement_timeout_ms, max_rows=10_000)
    questions = yaml.safe_load(Path(args.questions).read_text())
    if args.ids:
        questions = [q for q in questions if q["id"] in set(args.ids)]

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

    from app.graph import build_graph
    from app.providers import build_llm

    llm = build_llm(settings)
    graph = build_graph(llm, db, settings.max_sql_attempts, settings.rows_for_llm)
    results = []
    for i, q in enumerate(questions):
        if i and args.delay:
            time.sleep(args.delay)
        gold = db.run(q["gold_sql"]).rows
        start = time.perf_counter()
        try:
            state = graph.invoke({"question": q["question"]})
            error = state.get("error")
        except Exception as e:  # noqa: BLE001 - one bad question should not stop the run
            state, error = {}, f"{type(e).__name__}: {e}"
        latency = time.perf_counter() - start
        pred = state.get("rows")
        correct = pred is not None and not error and results_match(gold, pred, q.get("ordered", False))
        results.append({
            "id": q["id"], "category": q["category"], "question": q["question"], "correct": correct,
            "intent": state.get("intent"), "sql": state.get("sql"), "attempts": state.get("sql_attempts", 0),
            "latency_s": round(latency, 2), "error": error,
            "gold_rows": len(gold), "pred_rows": None if pred is None else len(pred),
            # The provider that wrote the final SQL (fallbacks can mix models within one run).
            "sql_provider": next((c["provider"] for c in reversed(state.get("llm_calls", []))
                                  if c["step"] == "sql_agent" and c["ok"]), None),
        })
        mark = "PASS" if correct else "FAIL"
        print(f"#{q['id']:>2} {mark} {latency:5.1f}s  attempts={results[-1]['attempts']}  {q['question']}")
        if not correct and error:
            print(f"      error: {error[:200]}")

    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r["correct"])
    summary = {
        "providers_configured": [p["label"] for p in llm.status() if p["configured"]],
        "sql_written_by": dict(Counter(r["sql_provider"] for r in results if r["sql_provider"])),
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "questions": len(results),
        "correct": sum(r["correct"] for r in results),
        "accuracy_pct": round(100 * sum(r["correct"] for r in results) / max(len(results), 1), 1),
        "by_category": {c: f"{sum(v)}/{len(v)}" for c, v in by_cat.items()},
        "repaired_by_retry": sum(1 for r in results if r["correct"] and r["attempts"] > 1),
        "median_latency_s": round(statistics.median(r["latency_s"] for r in results), 2) if results else None,
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print("\n" + json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
