"""Forecasting: exponential smoothing chosen by backtest (app/analysis/forecast.py), and questions about the future
answered through the pipeline."""

import asyncio
import math
from datetime import date, timedelta

import pytest

from app.agents.router import FORECAST, route
from app.analysis.forecast import CannotForecast, forecast, frequency, next_dates
from app.analyst import Analyst, Done, Table

from .conftest import FakeLLM

MONTHLY_REVENUE = """
SELECT date_trunc('month', o.order_date)::date AS month, ROUND(SUM(oi.quantity * oi.unit_price)::numeric, 2) AS revenue
FROM orders o JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.status = 'completed' GROUP BY 1 ORDER BY 1
"""
NEXT_QUARTER = {"history_question": "Monthly revenue", "periods_ahead": 3}


def months(n: int, start: date = date(2023, 1, 1)) -> list[date]:
    return [start] + next_dates(start, "month", n - 1)


def test_the_spacing_of_dates_is_recognised():
    assert frequency(months(12)) == "month"
    assert frequency([date(2025, 1, 1) + timedelta(weeks=k) for k in range(5)]) == "week"
    assert frequency([date(2025, 1, 1) + timedelta(days=k) for k in range(5)]) == "day"
    assert frequency([date(2024, 1, 1), date(2024, 4, 1), date(2024, 7, 1)]) == "quarter"
    with pytest.raises(CannotForecast, match="not evenly spaced"):
        frequency([date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 25)])


def test_month_ends_stay_month_ends():
    assert next_dates(date(2025, 1, 31), "month", 2) == [date(2025, 2, 28), date(2025, 3, 31)]


def test_a_steady_trend_is_carried_forward():
    values = [100 + 10 * k for k in range(24)]
    f = forecast(months(24), values, 3)
    assert f.model == "damped trend" and f.dates[0] == date(2025, 1, 1)
    assert values[-1] < f.values[0] < f.values[1] < f.values[2] < values[-1] + 40
    assert f.backtest_mape < 2


def test_a_season_is_found_given_two_years_and_more():
    values = [1000 + 20 * k + 300 * math.sin(2 * math.pi * k / 12) for k in range(36)]
    f = forecast(months(36), values, 12)
    assert f.model == "seasonal" and f.backtest_mape < 5
    peak = max(range(12), key=lambda k: f.values[k])
    assert peak == 3  # the season peaks in the fourth month of each year, as in the history


def test_the_range_widens_with_the_horizon_and_holds_the_forecast():
    values = [500 + (37 * k % 11) * 10 for k in range(18)]  # noisy, no trend
    f = forecast(months(18), values, 6)
    widths = [hi - lo for lo, hi in zip(f.lower, f.upper, strict=True)]
    assert widths == sorted(widths) and widths[0] > 0
    assert all(lo <= v <= hi for lo, v, hi in zip(f.lower, f.values, f.upper, strict=True))


def test_a_series_that_never_went_negative_is_not_forecast_below_zero():
    values = [1000 - 85 * k for k in range(12)]  # falling fast, still positive
    f = forecast(months(12), values, 12)
    assert min(f.lower) >= 0 and min(f.values) >= 0


def test_a_short_history_is_refused():
    with pytest.raises(CannotForecast, match="at least 12 periods"):
        forecast(months(8), list(range(8)), 3)


def test_a_question_about_the_future_is_forecast_from_its_history(db):
    llm = FakeLLM(intent="forecast", projection=NEXT_QUARTER, sql=[MONTHLY_REVENUE], summary="Revenue should grow.")
    answer = Analyst(llm, db, forecasting=True).ask("What will revenue be next quarter?")
    assert answer.error is None and answer.analysis == "forecast"
    assert answer.columns == ["month", "revenue", "forecast", "lower", "upper"]
    history, future = answer.rows[:24], answer.rows[24:]
    assert [r[0] for r in future] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert all(r[1] is None and r[3] <= r[2] <= r[4] for r in future)
    assert history[-1][1] == history[-1][2] and history[0][2] is None  # the forecast line starts at the last actual
    assert answer.chart.type == "line" and answer.chart.band == ["lower", "upper"]
    assert answer.chart.y == ["revenue", "forecast"]
    assert "80% interval" in answer.sql_reasoning and "off by" in answer.sql_reasoning

    sql_prompt = next(user for name, user in llm.calls if name == "SQLDraft")
    assert sql_prompt.startswith("Question: Monthly revenue")  # the SQL agent is asked for the history
    summary_prompt = next(user for name, user in llm.calls if name == "text")
    assert "Read as: Forecast of revenue for the next 3 months" in summary_prompt
    assert "ChartChoice" not in [name for name, _ in llm.calls]  # the chart is drawn up in code


def test_too_short_a_history_is_shown_and_explained(db):
    sql = MONTHLY_REVENUE.replace("WHERE", "WHERE o.order_date >= DATE '2025-07-01' AND")
    llm = FakeLLM(intent="forecast", projection=NEXT_QUARTER, sql=[sql])
    answer = Analyst(llm, db, forecasting=True).ask("What will revenue be next quarter?")
    assert answer.error is None and answer.analysis == "forecast" and len(answer.rows) == 6
    assert answer.columns == ["month", "revenue"] and answer.chart.band is None
    summary_prompt = next(user for name, user in llm.calls if name == "text")
    assert "No forecast was made: it needs at least 12 periods" in summary_prompt


def test_the_stream_replaces_the_history_with_the_forecast(db):
    llm = FakeLLM(intent="forecast", projection=NEXT_QUARTER, sql=[MONTHLY_REVENUE])

    async def events():
        return [e async for e in Analyst(llm, db, forecasting=True).stream("Forecast revenue")]

    out = asyncio.run(events())
    tables = [e for e in out if isinstance(e, Table)]
    assert [len(t.rows) for t in tables] == [24, 27] and tables[-1].columns[-1] == "upper"
    assert isinstance(out[-1], Done) and out[-1].answer.rows == tables[-1].rows


def test_the_router_offers_forecast_only_when_it_is_on():
    llm = FakeLLM()
    route(llm, "Revenue next quarter?", ["orders"], "a shop")
    route(llm, "Revenue next quarter?", ["orders"], "a shop", forecast=True)
    route(llm, "Revenue next quarter?", ["orders"], "a shop", why=True, forecast=True)
    (off_name, off), (on_name, on), (both_name, both) = llm.systems
    assert off_name == "RouteDecision" and "forecast:" not in off
    assert on_name == "RouteDecisionWithForecast" and FORECAST in on
    assert both_name == "RouteDecisionWithWhyForecast" and FORECAST in both and "why_change" in both
