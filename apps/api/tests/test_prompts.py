"""Versioned SQL prompts (semantic/prompts/) and the GEPA run that proposes new ones (eval/optimize_prompt.py)."""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from app.agents.sql_agent import PromptError, SQLDraft, SQLPrompt, default_prompt
from app.analyst import Analyst

from .conftest import FakeLLM

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text())
TEMPLATE = "Write {dialect} SQL for:\n{schema}\n{rules}\nTime series use {time_column}."


def optimizer():
    spec = importlib.util.spec_from_file_location("optimize_prompt", ROOT / "eval" / "optimize_prompt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["optimize_prompt"] = module
    spec.loader.exec_module(module)
    return module


def test_the_first_version_is_the_default():
    v1 = SQLPrompt.load("v1")
    assert v1 == default_prompt() and v1.text.startswith("You are an expert {dialect} analyst.")


@pytest.mark.parametrize("version, problem", [("1", "looks like v1"), ("../x", "looks like v1"),
                                              ("v999", "there is no SQL prompt v999")])
def test_only_prompt_files_that_exist_load(version, problem):
    with pytest.raises(PromptError, match=problem):
        SQLPrompt.load(version)


def test_a_prompt_must_keep_its_placeholders_and_no_other_braces():
    with pytest.raises(PromptError, match=r"lacks \{rules\}"):
        SQLPrompt("v9", TEMPLATE.replace("{rules}", ""))
    with pytest.raises(PromptError, match="braces other than its placeholders"):
        SQLPrompt("v9", TEMPLATE + " Return {result}.")
    assert SQLPrompt("v9", TEMPLATE + " Literal {{braces}} are fine.")


def test_the_version_that_wrote_the_sql_is_recorded(db, tmp_path):
    (tmp_path / "sql_writer.v2.md").write_text(TEMPLATE + "\n")
    llm = FakeLLM(sql=["SELECT 1 AS one"])
    answer = Analyst(llm, db, sql_prompt=SQLPrompt.load("v2", tmp_path)).ask("Revenue by region")
    assert answer.sql_prompt == "v2"
    assert next(s for name, s in llm.systems if name == "SQLDraft").startswith("Write PostgreSQL SQL for:")
    assert Analyst(FakeLLM(sql=["SELECT 1 AS one"]), db).ask("Revenue by region").sql_prompt == "v1"
    assert Analyst(FakeLLM(intent="out_of_scope"), db).ask("Hi").sql_prompt is None


def test_the_optimizer_learns_only_from_dev_questions():
    learn, score = optimizer().split_dev(QUESTIONS)
    assert {q["split"] for q in learn + score} == {"dev"}
    assert len(learn) + len(score) == sum(q["split"] == "dev" for q in QUESTIONS)
    assert not {q["id"] for q in learn} & {q["id"] for q in score}


class Student(FakeLLM):
    """A SQL writer that gets questions right only when its prompt says to use the gold queries' rules, and a
    reflecting model that proposes exactly that once it has seen the feedback."""

    MARKER = "Follow the business rules exactly."

    def __init__(self, gold: dict[str, str]):
        super().__init__()
        self.gold = gold
        self.reflections: list[str] = []

    def structured(self, output, system, user, tier="strong"):
        self.calls.append((output.__name__, user))
        question = user.removeprefix("Question: ").split("\n")[0]
        sql = self.gold[question] if self.MARKER in system else "SELECT 1 AS wrong"
        return SQLDraft(reasoning="", sql=sql)

    def text(self, system, user, tier="strong"):
        self.reflections.append(user)
        assert "{rules}" in system  # the reflecting model is told to keep the placeholders
        return f"```\n{TEMPLATE}\n{self.MARKER}\n```"


def test_gepa_evolves_a_prompt_from_the_feedback(db):
    gepa = pytest.importorskip("gepa")
    opt = optimizer()
    learn, score = opt.split_dev(QUESTIONS)
    learn, score = learn[:6], score[:4]
    student = Student({q["question"]: q["gold_sql"] for q in learn + score})
    result = gepa.optimize(seed_candidate={opt.COMPONENT: TEMPLATE}, trainset=learn, valset=score,
                           adapter=opt.SQLWriterAdapter(student, db), reflection_lm=opt.reflection_lm(student, 0),
                           max_metric_calls=40, seed=7)
    scores = list(result.val_aggregate_scores)
    assert scores[0] == 0.0 and max(scores) == 1.0
    best = result.candidates[scores.index(max(scores))][opt.COMPONENT]
    assert Student.MARKER in best and SQLPrompt("v9", best)
    feedback = "\n".join(student.reflections)
    assert "Wrong result. Expected" in feedback and "A correct query:" in feedback


def test_a_proposal_without_its_placeholders_scores_nothing(db):
    opt = optimizer()
    q = next(q for q in QUESTIONS if q["split"] == "dev")
    adapter = opt.SQLWriterAdapter(Student({q["question"]: q["gold_sql"]}), db)
    out = adapter.evaluate([q], {opt.COMPONENT: "Write good SQL. " + Student.MARKER}, capture_traces=True)
    assert out.scores == [0.0] and "lacks {dialect}" in out.trajectories[0].feedback


def test_new_versions_are_numbered_after_the_last(tmp_path):
    opt = optimizer()
    assert opt.next_version(tmp_path) == "v1"
    for n in (1, 2, 10):
        (tmp_path / f"sql_writer.v{n}.md").write_text(TEMPLATE)
    assert opt.next_version(tmp_path) == "v11"
