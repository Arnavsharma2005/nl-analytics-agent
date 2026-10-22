"""Follow-up questions: rewritten with the earlier turns, while a first question goes through exactly as before."""

import asyncio

from app.agents.chart import ChartSpec
from app.agents.followup import Turn, standalone
from app.analyst import Analyst
from app.answer_cache import AnswerCache

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION

EARLIER = [Turn("Revenue by region", sql=REVENUE_BY_REGION, summary="North leads.")]
ASKED = "and only for 2024?"
REWRITTEN = "Revenue by region in 2024"


def llm(**kw):
    return FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t"), **kw)


def asked_models(fake: FakeLLM) -> list[str]:
    return [name for name, _ in fake.calls]


# --- the rewrite -----------------------------------------------------------------------------------------------

def test_without_history_the_question_is_kept_and_no_model_is_asked():
    fake = FakeLLM()
    assert standalone(fake, ASKED, []) == ASKED and fake.calls == []


def test_the_rewrite_sees_the_last_three_turns_with_their_sql_and_answers():
    fake = FakeLLM(standalone=REWRITTEN)
    turns = [Turn(f"question {i}", sql=f"SELECT {i}", summary=f"answer {i}") for i in range(5)]
    assert standalone(fake, ASKED, turns) == REWRITTEN
    (name, user), = fake.calls
    assert name == "Standalone" and fake.tiers == [("Standalone", "fast")]
    assert "question 1" not in user and all(f"question {i}" in user for i in (2, 3, 4))
    assert "SQL: SELECT 4" in user and "Answer: answer 4" in user and user.endswith(f"Latest message: {ASKED}")


def test_an_empty_rewrite_keeps_the_question():
    assert standalone(FakeLLM(standalone="   "), ASKED, EARLIER) == ASKED


# --- in the pipeline -------------------------------------------------------------------------------------------

def test_a_first_question_skips_the_rewrite(db):
    fake = llm()
    out = Analyst(fake, db).ask("Revenue by region", history=[])
    assert "Standalone" not in asked_models(fake) and "followup" not in out.timings
    assert out.question == "Revenue by region" and out.standalone_question is None


def test_a_follow_up_is_answered_as_it_was_understood(db):
    fake = llm(standalone=REWRITTEN)
    out = Analyst(fake, db).ask(ASKED, history=EARLIER)
    assert asked_models(fake)[0] == "Standalone" and "followup" in out.timings
    assert out.question == ASKED and out.standalone_question == REWRITTEN
    later = [user for name, user in fake.calls[1:] if name != "text"]
    assert later and all(ASKED not in user for user in later)  # the router and SQL agent see only the rewrite


def test_a_follow_up_that_already_stands_alone_is_not_marked(db):
    out = Analyst(llm(), db).ask("Top 5 customers by revenue", history=EARLIER)
    assert out.question == "Top 5 customers by revenue" and out.standalone_question is None


def test_streaming_a_follow_up_names_the_rewrite_step(db):
    analyst = Analyst(llm(standalone=REWRITTEN), db)

    async def collect():
        return [e async for e in analyst.stream(ASKED, history=EARLIER)]

    events = asyncio.run(collect())
    assert events[0].node == "followup" and events[0].label == "Reading it with the earlier questions"
    assert events[-1].answer.standalone_question == REWRITTEN


# --- with the answer cache -------------------------------------------------------------------------------------

def test_a_follow_up_is_cached_under_the_question_it_was_understood_as(db, app_tables):
    cache = AnswerCache(app_tables, "v1", 3600)
    Analyst(llm(standalone=REWRITTEN), db, cache=cache).ask(ASKED, history=EARLIER)
    assert cache.get(ASKED) is None
    hit = cache.get(REWRITTEN)
    assert hit and hit.question == REWRITTEN and hit.standalone_question is None


def test_a_follow_up_never_reads_the_cache(db, app_tables):
    cache = AnswerCache(app_tables, "v1", 3600)
    Analyst(llm(), db, cache=cache).ask(ASKED)  # cached as a question on its own
    fake = llm(standalone=REWRITTEN)
    out = Analyst(fake, db, cache=cache).ask(ASKED, history=EARLIER)
    assert not out.cached and out.standalone_question == REWRITTEN
