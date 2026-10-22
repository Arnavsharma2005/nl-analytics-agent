"""Checks before an answer is shown: a query that found nothing, and summary figures that aren't in the result."""

import pytest

from app.analyst import Analyst
from app.checks import looks_empty, unsupported_numbers

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION

NOTHING = "SELECT r.name AS region FROM regions r WHERE r.name = 'Atlantis'"
NULL_TOTAL = "SELECT SUM(oi.quantity) AS units FROM order_items oi WHERE oi.quantity < 0"


def test_looks_empty():
    assert looks_empty([]) and looks_empty([[None]]) and looks_empty([[None, None]])
    assert not looks_empty([[0]]) and not looks_empty([[None, 1]])


ROWS = [["North", 12345678.9, 23.5], ["South", 4530000.0, 18.2], ["West", 845.0, 5.0]]


@pytest.mark.parametrize("summary", [
    "North led with ₹1.23 Cr, ahead of South at ₹45.3 L.",  # Indian units, rounded
    "North earned about 2.7 times as much as South.",  # a ratio
    "West made just ₹845 and North's margin was 23.5%, 5.3 points above South.",  # a value and a difference
    "North took 73.2% of revenue across the 3 regions.",  # a share of the column
    "In Q4 2025 the top 3 regions, from 2025-01-01, ...",  # years, dates, quarters and small counts are not figures
    "South's margin is 22.6% below North's.",  # growth between two values
])
def test_figures_worked_out_from_the_result_are_supported(summary):
    assert unsupported_numbers(summary, ROWS, 3) == []


def test_figures_not_in_the_result_are_reported():
    assert unsupported_numbers("Revenue was ₹9.9 Cr, margin 40% and 1,234 orders.", ROWS, 3) == ["9.9 Cr", "40%", "1,234"]


def test_a_query_that_finds_nothing_gets_one_more_try(db):
    llm = FakeLLM(sql=[NOTHING, REVENUE_BY_REGION])
    out = Analyst(llm, db, check_results=True).ask("Revenue by region")
    assert len(out.rows) == 6 and out.attempts == 2
    assert out.failures[0].stage == "result" and out.failures[0].sql == NOTHING
    retry = [u for n, u in llm.calls if n == "SQLDraft"][1]
    assert "found nothing" in retry


def test_finding_nothing_twice_is_accepted_as_the_answer(db):
    out = Analyst(FakeLLM(sql=[NULL_TOTAL]), db, check_results=True).ask("Units returned as negative quantities")
    assert out.error is None and out.rows == [[None]] and out.attempts == 2
    assert [f.stage for f in out.failures] == ["result"]


def test_finding_nothing_is_accepted_without_the_check(db):
    out = Analyst(FakeLLM(sql=[NOTHING]), db).ask("Revenue in Atlantis")
    assert out.error is None and out.rows == [] and out.attempts == 1


def test_a_summary_with_made_up_figures_is_rewritten_once(db):
    llm = FakeLLM(sql=[REVENUE_BY_REGION], summary=["Revenue reached ₹99.9 Cr.", "Six regions sold."])
    out = Analyst(llm, db, check_summaries=True).ask("Revenue by region")
    rewrites = [u for n, u in llm.calls if n == "text"]
    assert out.summary == "Six regions sold." and out.summary_verified is True
    assert len(rewrites) == 2 and "99.9 Cr" in rewrites[1]


def test_a_summary_that_stays_wrong_is_marked(db):
    llm = FakeLLM(sql=[REVENUE_BY_REGION], summary=["Revenue reached ₹99.9 Cr."])
    out = Analyst(llm, db, check_summaries=True).ask("Revenue by region")
    assert out.summary == "Revenue reached ₹99.9 Cr." and out.summary_verified is False


def test_summaries_are_not_checked_by_default(db):
    llm = FakeLLM(sql=[REVENUE_BY_REGION], summary=["Revenue reached ₹99.9 Cr."])
    out = Analyst(llm, db).ask("Revenue by region")
    assert out.summary_verified is None and len([n for n, _ in llm.calls if n == "text"]) == 1
