"""eval/publish.py: the public eval page leads with the latest real run over every held-out question."""

import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("publish", ROOT / "eval" / "publish.py")
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)

QUESTIONS = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text())


def run(tmp_path: Path, name: str, run_at: str, questions: list[dict], *, wrong: set = frozenset(),
        models: tuple = ("groq",), **summary) -> None:
    results = [{"id": q["id"], "category": q["category"], "difficulty": q["difficulty"], "split": q["split"],
                "question": q["question"], "correct": q["id"] not in wrong, "attempts": 2 if q["id"] == 51 else 1,
                "sql": "SELECT 1", "error": "no such column" if q["id"] in wrong else None} for q in questions]
    body = {"summary": {"run_at": run_at, "commit": name, "providers_configured": list(models), **summary},
            "results": results}
    (tmp_path / f"{name}.json").write_text(json.dumps(body))


def test_no_runs_give_an_empty_page(tmp_path):
    report = publish.build(tmp_path)
    assert report["runs"] == [] and report["latest"] == {}
    assert report["held_out_questions"]["questions"] == 70


def test_the_latest_run_over_every_held_out_question_leads(tmp_path):
    test = [q for q in QUESTIONS if q["split"] == "test"]
    run(tmp_path, "full", "2026-10-20T10:00:00+00:00", QUESTIONS, wrong={52, 60})
    run(tmp_path, "subset", "2026-10-21T10:00:00+00:00", test[:10])  # newer, but not every held-out question
    run(tmp_path, "replay", "2026-10-22T10:00:00+00:00", QUESTIONS, models=("replay of cassette.json",))

    report = publish.build(tmp_path)
    assert [r["commit"] for r in report["runs"]] == ["subset", "full"]  # newest first; replays left out
    assert report["runs"][0]["held_out"] is None
    latest = report["latest"]["questions"]
    assert latest["commit"] == "full" and latest["all"]["questions"] == 120
    assert latest["held_out"] == {"correct": 68, "questions": 70, "accuracy_pct": 97.1}
    assert [q["id"] for q in latest["questions"]] == [q["id"] for q in test]  # only held-out, in set order
    assert latest["repaired_by_retry"] == 1
    assert sum(d["questions"] for d in latest["by_difficulty"].values()) == 70
    assert next(q for q in latest["questions"] if q["id"] == 52)["error"] == "no such column"


def test_multi_turn_runs_are_reported_on_their_own(tmp_path):
    conversations = yaml.safe_load((ROOT / "eval" / "conversations.yaml").read_text())
    turns = [{"id": f"{c['id']}.{n}", "category": t.get("kind", "first"), "split": c["split"],
              "difficulty": "follow-up" if n > 1 else "first turn", "question": t["question"]}
             for c in conversations for n, t in enumerate(c["turns"], 1)]
    run(tmp_path, "multi", "2026-10-21T10:00:00+00:00", turns)  # an older run, before runs recorded their set

    report = publish.build(tmp_path)
    assert set(report["latest"]) == {"conversations"}
    assert report["latest"]["conversations"]["held_out"]["questions"] == report["held_out_questions"]["conversations"]


def test_the_committed_page_data_is_up_to_date():
    assert publish.OUT.read_text(encoding="utf-8") == publish.render(publish.build())
