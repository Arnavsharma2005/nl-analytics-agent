"""Driver analysis: "why did it change?" answered by splitting the change in code (app/analysis/drivers.py)."""

import asyncio
from datetime import date

import pytest

from app.agents.router import WHY, route
from app.analysis import drivers as dv
from app.analyst import Analyst, Chart, Done, Table
from app.sql_guard import validate_sql

from .conftest import FakeLLM

Q2 = dv.Period(start=date(2025, 4, 1), end=date(2025, 6, 30))
Q3 = dv.Period(start=date(2025, 7, 1), end=date(2025, 9, 30))
WHY_Q3 = {"metric": "revenue", "before_start": "2025-04-01", "before_end": "2025-06-30",
          "after_start": "2025-07-01", "after_end": "2025-09-30"}


@pytest.fixture(scope="module")
def drivers():
    return dv.load()


def run_split(db, drivers, metric_id, before=Q2, after=Q3):
    metric = drivers.metric(metric_id)
    schema = db.schema()
    sql = validate_sql(dv.query(drivers, metric, before, after), set(schema.tables), schema.personal_columns(),
                       db.dialect)
    result = db.run(sql)
    return dv.split(drivers, metric, result.columns, result.rows)


@pytest.fixture
def every_group(monkeypatch):
    monkeypatch.setattr(dv, "MAX_DRIVERS", 100)


def by_dimension(rows):
    groups = {}
    for row in rows[1:]:
        groups.setdefault(row[0].split(": ")[0], []).append(row)
    return groups


def test_an_additive_change_is_split_exactly_by_every_dimension(db, drivers, every_group):
    split = run_split(db, drivers, "revenue")
    assert split.columns == ["driver", "before", "after", "change", "share_of_change_pct"] and not split.left_out
    total = split.rows[0]
    assert total[0] == "Total" and total[3] == pytest.approx(total[2] - total[1])
    groups = by_dimension(split.rows)
    assert set(groups) == {"Region", "Category", "Channel", "Customer segment"}
    for rows in groups.values():
        assert sum(r[3] for r in rows) == pytest.approx(total[3], abs=0.1)
        assert sum(r[4] for r in rows) == pytest.approx(100, abs=0.5)
    changes = [abs(r[3]) for r in split.rows[1:]]
    assert changes == sorted(changes, reverse=True)  # biggest movers first


def test_a_count_is_not_split_by_a_dimension_whose_groups_overlap(db, drivers, every_group):
    split = run_split(db, drivers, "orders")
    assert split.left_out == ["Category"]  # one order holds products from several categories
    assert "Category" not in by_dimension(split.rows)


def test_a_ratio_change_splits_into_mix_and_rate_effects_that_add_up(db, drivers, every_group):
    split = run_split(db, drivers, "aov")
    assert split.columns == ["driver", "before", "after", "contribution", "mix_effect", "rate_effect"]
    total = split.rows[0]
    for rows in by_dimension(split.rows).values():
        assert sum(r[3] for r in rows) == pytest.approx(total[3], abs=0.05)
        for r in rows:
            assert r[4] + r[5] == pytest.approx(r[3], abs=0.02)


def test_ratio_effects_on_a_worked_example(drivers):
    # Two channels. Before: web 10 orders at 100, app 10 at 200 (AOV 150). After: web 10 at 100, app 30 at 200
    # (AOV 175). All of the +25 is mix: app's share of orders rose; neither channel's own AOV moved.
    rows = [["total", None, 3000, 20, 7000, 40], ["channel", "web", 1000, 10, 1000, 10],
            ["channel", "app", 2000, 10, 6000, 30]]
    split = dv.split(drivers, drivers.metric("aov"), ["dimension", "grp", "before_0", "before_1", "after_0", "after_1"],
                     rows)
    assert split.rows[0] == ["Total", 150.0, 175.0, 25.0, None, None]
    app, web = split.rows[1], split.rows[2]
    assert app == ["Channel: app", 200.0, 200.0, 50.0, 50.0, 0.0]
    assert web == ["Channel: web", 100.0, 100.0, -25.0, -25.0, 0.0]


def test_a_group_new_in_the_later_period_contributes_at_its_own_rate(drivers):
    rows = [["total", None, 1000, 10, 3000, 20], ["channel", "web", 1000, 10, 1000, 10],
            ["channel", "app", 0, 0, 2000, 10]]
    split = dv.split(drivers, drivers.metric("aov"), ["dimension", "grp", "before_0", "before_1", "after_0", "after_1"],
                     rows)
    total, *groups = split.rows
    assert total[3] == 50.0 and sum(r[3] for r in groups) == pytest.approx(50.0)
    assert next(r for r in groups if r[0] == "Channel: app")[1] is None  # it had no orders before


def test_a_why_question_is_answered_by_driver_analysis(db, drivers):
    llm = FakeLLM(intent="why_change", why=WHY_Q3, summary="Mobile drove almost all of the growth.")
    answer = Analyst(llm, db, drivers=drivers).ask("Why did revenue grow in Q3 2025?")
    assert answer.error is None and answer.intent == "data_query" and answer.analysis == "drivers"
    assert "GROUPING SETS" in answer.sql and answer.rows[0][0] == "Total"
    assert answer.chart.x == "driver" and answer.chart.y == ["change"] and answer.chart_source == "fallback"
    assert answer.sql_reasoning == "Revenue, 01 Jul 2025 to 30 Sep 2025 compared with 01 Apr 2025 to 30 Jun 2025."
    assert answer.summary == "Mobile drove almost all of the growth." and answer.attempts == 0
    summary_prompt = next(user for name, user in llm.calls if name == "text")
    assert "Read as: Revenue, 01 Jul 2025" in summary_prompt and "Channel: mobile" in summary_prompt
    assert [name for name, _ in llm.calls] == ["RouteDecisionWithWhy", "WhyReading", "text"]  # no SQL agent


def test_the_stream_sends_the_drivers_table_and_chart(db, drivers):
    llm = FakeLLM(intent="why_change", why={**WHY_Q3, "metric": "aov"})

    async def events():
        return [e async for e in Analyst(llm, db, drivers=drivers).stream("Why did basket size fall in Q3?")]

    out = asyncio.run(events())
    table = next(e for e in out if isinstance(e, Table))
    assert table.columns[3] == "contribution"
    assert next(e for e in out if isinstance(e, Chart)).spec.y == ["contribution"]
    assert isinstance(out[-1], Done) and out[-1].answer.analysis == "drivers"


@pytest.mark.parametrize("reading, error", [
    ({**WHY_Q3, "metric": "happiness"}, "isn't about a metric that can be split"),
    ({**WHY_Q3, "after_start": "2025-10-01"}, "periods to compare don't make sense"),
])
def test_a_reading_that_makes_no_sense_is_reported(db, drivers, reading, error):
    answer = Analyst(FakeLLM(intent="why_change", why=reading), db, drivers=drivers).ask("Why did it change?")
    assert answer.analysis == "drivers" and error in answer.error and answer.rows == []


def test_the_router_offers_why_change_only_with_driver_analysis():
    llm = FakeLLM()
    route(llm, "Why did revenue drop?", ["orders"], "a shop")
    route(llm, "Why did revenue drop?", ["orders"], "a shop", why=True)
    (off_name, off), (on_name, on) = llm.systems
    assert off_name == "RouteDecision" and "why_change" not in off
    assert on_name == "RouteDecisionWithWhy" and WHY in on


def test_the_drivers_file_is_checked():
    base = {"tables": "orders o", "date": "o.order_date", "dimensions": [{"id": "c", "label": "C", "sql": "o.channel"}]}
    with pytest.raises(ValueError, match="exactly one of sum"):
        dv.Drivers.model_validate({**base, "metrics": [{"id": "m", "label": "M", "sum": "1", "count_distinct": "x"}]})
    with pytest.raises(ValueError, match="a ratio is of two"):
        dv.Drivers.model_validate({**base, "metrics": [{"id": "m", "label": "M", "ratio": ["a", "b"]}]})


def test_a_folder_without_drivers_has_no_driver_analysis(tmp_path):
    assert dv.load(tmp_path) is None
