"""Evolve the SQL writer's prompt with GEPA on the dev questions, using what went wrong as feedback.

Usage (from the repo root, with DATABASE_URL and an LLM key set, and `pip install -r eval/requirements.txt`):
    python eval/optimize_prompt.py                     # start from SQL_PROMPT (default v1), 300 model calls
    python eval/optimize_prompt.py --budget 150 --delay 4

GEPA (https://github.com/gepa-ai/gepa) runs the SQL writer with a candidate prompt on a few dev questions, shows
a stronger model each wrong query with the error or how its result differed from the gold one, asks it for a
better prompt, and keeps the candidates that answer more dev questions right (a Pareto front over questions, so
a prompt that fixes a different question survives too). Only the dev split is used: the dev questions are split
into the questions it learns from and the ones it is scored on, and the test split is never read.

The best prompt is written as the next version, semantic/prompts/sql_writer.vN.md, with its dev scores beside it
in sql_writer.vN.json. It is not used until it is measured where it counts and SQL_PROMPT points at it:
    SQL_PROMPT=vN python eval/run_eval.py --split test     # compare with the same run on the current version

Each scored question is one SQL-writer call and each proposal one call to the reflecting model, all on the free
tiers' quota: 300 calls take a few days of it at most. `--delay` spaces the calls out.
"""

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.agents.sql_agent import PROMPTS_DIR, PromptError, SQLPrompt, write_sql  # noqa: E402
from app.brief import database  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Database, QueryError  # noqa: E402
from app.evaluation import results_match  # noqa: E402
from app.llm import LLM  # noqa: E402
from app.sql_guard import SQLValidationError, validate_sql  # noqa: E402

COMPONENT = "sql_writer"
SHOWN_ROWS = 5


@dataclass(frozen=True)
class Trace:
    question: str
    sql: str
    feedback: str


def _rows(rows: list[list]) -> str:
    shown = "; ".join(str(tuple(r)) for r in rows[:SHOWN_ROWS])
    return f"{len(rows)} rows: {shown}" + (" ..." if len(rows) > SHOWN_ROWS else "")


class SQLWriterAdapter:
    """GEPA's view of the SQL writer: run a candidate prompt on dev questions, score each 1 or 0 against the gold
    result, and explain each miss for the reflecting model."""

    propose_new_texts = None  # GEPA's own proposer writes the new prompt from the reflective dataset

    def __init__(self, llm: LLM, db: Database, delay: float = 0.0):
        self._llm, self._db, self._delay = llm, db, delay
        schema = db.schema()
        self._schema_text = schema.describe()
        self._tables, self._personal = set(schema.tables), schema.personal_columns()
        self._brief = database()
        self._gold: dict[int, list[list]] = {}

    def gold(self, q: dict) -> list[list]:
        if q["id"] not in self._gold:
            self._gold[q["id"]] = self._db.run(q["gold_sql"]).rows
        return self._gold[q["id"]]

    def attempt(self, prompt: SQLPrompt, q: dict) -> tuple[float, Trace]:
        """One question: 1.0 when the query's result matches the gold one, else 0.0, with why."""
        if self._delay:
            time.sleep(self._delay)
        try:
            draft = write_sql(self._llm, q["question"], self._schema_text, [], self._brief, self._db.dialect,
                              prompt=prompt)
        except Exception as e:  # noqa: BLE001 - a failed call is a miss to learn from, not a reason to stop
            return 0.0, Trace(q["question"], "", f"The model call failed: {type(e).__name__}: {e}")
        try:
            rows = self._db.run(validate_sql(draft.sql, self._tables, self._personal, self._db.dialect)).rows
        except (SQLValidationError, QueryError) as e:
            return 0.0, Trace(q["question"], draft.sql, f"The query was rejected or failed: {e}")
        gold = self.gold(q)
        if results_match(gold, rows, q.get("ordered", False)):
            return 1.0, Trace(q["question"], draft.sql, "Correct.")
        order = " Row order matters for this question." if q.get("ordered") else ""
        return 0.0, Trace(q["question"], draft.sql, f"Wrong result. Expected {_rows(gold)}. Got {_rows(rows)}.{order} "
                                                    f"A correct query: {q['gold_sql'].strip()}")

    # --- The GEPAAdapter protocol ---------------------------------------------------------------------------------

    def evaluate(self, batch: list[dict], candidate: dict[str, str], capture_traces: bool = False):
        from gepa.core.adapter import EvaluationBatch

        try:
            prompt = SQLPrompt("candidate", candidate[COMPONENT])
        except PromptError as e:  # a proposal that dropped a placeholder scores nothing, and says why
            traces = [Trace(q["question"], "", f"The prompt is unusable: {e}") for q in batch]
            return EvaluationBatch(outputs=[""] * len(batch), scores=[0.0] * len(batch),
                                   trajectories=traces if capture_traces else None)
        scored = [self.attempt(prompt, q) for q in batch]
        return EvaluationBatch(outputs=[t.sql for _, t in scored], scores=[s for s, _ in scored],
                               trajectories=[t for _, t in scored] if capture_traces else None)

    def make_reflective_dataset(self, candidate: dict[str, str], eval_batch, components_to_update: list[str]):
        records = [{"Inputs": {"question": t.question}, "Generated Outputs": t.sql or "(none)", "Feedback": t.feedback}
                   for t in eval_batch.trajectories]
        return {name: records for name in components_to_update}


REFLECTION_NOTE = """
The prompt is a template: keep the placeholders {dialect}, {schema}, {rules} and {time_column} exactly as they are,
each at least once, and write any other brace doubled ({{ or }}). Business rules come from {rules}; improve the
general instructions rather than adding rules or examples about particular questions."""


def reflection_lm(llm: LLM, delay: float):
    """GEPA's reflecting model: the app's own models, with the template's constraints added to every request."""
    def ask(prompt: str | list[dict[str, Any]]) -> str:
        if delay:
            time.sleep(delay)
        text = prompt if isinstance(prompt, str) else "\n\n".join(str(m.get("content", "")) for m in prompt)
        return llm.text("You improve the instructions given to a SQL-writing model." + REFLECTION_NOTE, text)
    return ask


def next_version(directory: Path = PROMPTS_DIR) -> str:
    taken = [int(p.name.split(".v")[1].split(".")[0]) for p in directory.glob("sql_writer.v*.md")]
    return f"v{max(taken, default=0) + 1}"


def split_dev(questions: list[dict], seed: int = 7) -> tuple[list[dict], list[dict]]:
    """Dev questions only: two thirds to learn from, a third to score candidates on, the same way every run."""
    dev = [q for q in questions if q["split"] == "dev"]
    random.Random(seed).shuffle(dev)
    cut = len(dev) * 2 // 3
    return dev[:cut], dev[cut:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", help="the version to start from (default SQL_PROMPT)")
    parser.add_argument("--budget", type=int, default=300, help="how many questions GEPA may score in all")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between model calls")
    parser.add_argument("--run-dir", type=Path, help="where GEPA keeps its state, to resume an interrupted run")
    args = parser.parse_args(argv)

    try:
        import gepa
    except ImportError:
        print("GEPA is not installed: pip install -r eval/requirements.txt")
        return 2
    from app.providers import build_llm

    settings = get_settings()
    start = SQLPrompt.load(args.start or settings.sql_prompt)
    db = Database(settings.database_url, settings.statement_timeout_ms, max_rows=10_000,
                  max_cost=settings.max_query_cost, personal_columns=settings.personal_data_columns)
    llm = build_llm(settings)
    learn, score = split_dev(yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text()))
    adapter = SQLWriterAdapter(llm, db, args.delay)

    result = gepa.optimize(seed_candidate={COMPONENT: start.text}, trainset=learn, valset=score, adapter=adapter,
                           reflection_lm=reflection_lm(llm, args.delay), max_metric_calls=args.budget,
                           run_dir=str(args.run_dir) if args.run_dir else None, seed=7)
    scores = list(result.val_aggregate_scores)
    best = max(range(len(scores)), key=scores.__getitem__)
    print(f"Started from {start.version}: {scores[0]:.0%} of the {len(score)} scoring questions. "
          f"Best of {len(scores)} candidates: {scores[best]:.0%}.")
    if best == 0 or scores[best] <= scores[0]:
        print("No candidate beat the starting prompt; nothing written.")
        return 0

    version = next_version()
    prompt = SQLPrompt(version, result.candidates[best][COMPONENT])  # checks the placeholders once more
    (PROMPTS_DIR / f"sql_writer.{version}.md").write_text(prompt.text + "\n")
    (PROMPTS_DIR / f"sql_writer.{version}.json").write_text(json.dumps({
        "version": version, "parent": start.version, "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "optimizer": f"gepa {getattr(gepa, '__version__', '')}".strip(), "budget": args.budget,
        "dev_learn": [q["id"] for q in learn], "dev_score": [q["id"] for q in score],
        "dev_score_accuracy": {"parent": round(scores[0], 4), "this": round(scores[best], 4)},
        "test_accuracy": None,
    }, indent=2) + "\n")
    print(f"Wrote semantic/prompts/sql_writer.{version}.md. Measure it on the held-out split before using it:\n"
          f"  SQL_PROMPT={version} python eval/run_eval.py --split test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
