"""The insights feed: unusual moves in the latest complete week (app/analysis/insights.py), stored by the nightly job
and served at /v1/insights."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.analysis import drivers as dv
from app.analysis.insights import Finding, find, headlines, latest_complete_week, scan
from app.config import get_settings
from app.insights_feed import InsightsFeed
from app.jobs import insights as job
from app.main import create_app
from app.sessions import Sessions
from app.sql_guard import validate_sql

from .conftest import TEST_DB, FakeLLM

WEEKS = [date(2025, 10, 27) + timedelta(weeks=k) for k in range(9)]
COLUMNS = ["week", "dimension", "grp", "part_0"]
LABELS = {"channel": "Channel", "category": "Category"}


def finding(**overrides) -> Finding:
    base = dict(metric="Orders", dimension="Channel", group="marketplace", week_start=WEEKS[-1], value=240.0,
                usual=115.0, z=6.2, share=0.2, sql="SELECT 1")
    return Finding(**{**base, **overrides})


def test_the_latest_complete_week_runs_monday_to_sunday():
    assert latest_complete_week(date(2025, 12, 31)) == date(2025, 12, 22)  # a Wednesday: the week before
    assert latest_complete_week(date(2025, 12, 28)) == date(2025, 12, 22)  # a Sunday: its own week


def weekly(web: list[float], app: list[float]) -> list[list]:
    rows = []
    for k, week in enumerate(WEEKS):
        rows += [[week.isoformat(), "total", None, web[k] + app[k]], [week.isoformat(), "channel", "web", web[k]],
                 [week.isoformat(), "channel", "app", app[k]]]
    return rows


def test_a_spike_in_one_group_is_found_and_steady_ones_are_not():
    orders = dv.Metric(id="orders", label="Orders", count_distinct="o.order_id")
    web = [100, 104, 98, 101, 99, 103, 97, 100, 102]
    app = [50, 52, 49, 51, 48, 50, 53, 50, 110]  # app orders doubled in the last week
    found = find(orders, LABELS, weekly(web, app), COLUMNS, WEEKS, "SQL")
    app_move = next(f for f in found if f.group == "app")
    assert app_move.value == 110 and app_move.usual == 50 and app_move.z > 10 and app_move.dimension == "Channel"
    assert "web" not in {f.group for f in found}
    assert app_move.plain() == "Orders (channel: app) rose to 110 in the week of 22 Dec, 2.2x the usual 50."


def test_a_small_move_is_not_news_however_unusual():
    orders = dv.Metric(id="orders", label="Orders", count_distinct="o.order_id")
    flat = [100.0] * 8
    found = find(orders, LABELS, weekly(flat + [110], flat[:8] + [100]), COLUMNS, WEEKS, "SQL")
    assert found == []  # 10% up on a flat line: unusual, but under the 25% bar


def test_groups_that_overlap_are_not_scanned():
    orders = dv.Metric(id="orders", label="Orders", count_distinct="o.order_id")
    rows = weekly([100] * 9, [50] * 8 + [120])
    # One order holds several categories, so categories add up to more orders than there were.
    rows += [[w.isoformat(), "category", "Books", 90 if k < 8 else 200] for k, w in enumerate(WEEKS)]
    rows += [[w.isoformat(), "category", "Toys", 90] for w in WEEKS]
    found = find(orders, LABELS, rows, COLUMNS, WEEKS, "SQL")
    assert found and all(f.dimension != "Category" for f in found)


def test_the_demo_data_is_scanned_with_queries_that_pass_the_guard(db):
    week, found = scan(dv.load(), db)
    assert week == date(2025, 12, 22) and 0 < len(found) <= 5
    schema = db.schema()
    for f in found:
        assert f.week_start == week and abs(f.z) >= 3 and abs(f.value - f.usual) >= 0.25 * abs(f.usual)
        validate_sql(f.sql, set(schema.tables), schema.personal_columns(), db.dialect)
        assert not (f.metric in ("Orders", "Average order value", "Customers who ordered") and f.dimension == "Category")
    ranks = [min(abs(f.z), 10) * (0.25 + f.share) for f in found]
    assert ranks == sorted(ranks, reverse=True)


def test_headlines_come_from_the_model_or_plain_sentences():
    found = [finding(), finding(group="web", value=50.0, usual=100.0, z=-5.0)]
    written = headlines(FakeLLM(headlines=["Marketplace orders doubled.", "Web orders halved."]), found, "In rupees.")
    assert written == ["Marketplace orders doubled.", "Web orders halved."]
    plain = [f.plain() for f in found]
    assert headlines(None, found) == plain
    assert headlines(FakeLLM(headlines=["Only one."]), found) == plain  # the wrong number of headlines
    assert headlines(FakeLLM(headlines=RuntimeError("down")), found) == plain
    assert plain[1] == "Orders (channel: web) fell to 50 in the week of 22 Dec, 0.5x the usual 100."


def test_the_feed_is_replaced_whole_and_served(app_tables, db):
    feed = InsightsFeed(app_tables)
    assert feed.latest().insights == [] and feed.latest().week_start is None
    feed.replace(WEEKS[-1], [finding(), finding(group="web")], ["first", "second"])
    feed.replace(WEEKS[-1], [finding(dimension=None, group=None, metric="Revenue")], ["only"])
    latest = feed.latest()
    assert latest.week_start == WEEKS[-1] and [i.headline for i in latest.insights] == ["only"]
    assert latest.insights[0].question == "Why did revenue change in the week of 22 Dec 2025?"

    app = create_app(db_factory=lambda: db, sessions_factory=lambda: Sessions("secret", 3600),
                     quota_factory=lambda: None, github_factory=lambda: None, transcriber_factory=lambda: None,
                     insights_factory=lambda: feed)
    body = TestClient(app).get("/v1/insights").json()
    assert body["week_start"] == "2025-12-22" and body["insights"][0]["metric"] == "Revenue"


def test_the_nightly_job_stores_the_feed(app_tables, monkeypatch, capsys):
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY",
                "CLOUDFLARE_API_TOKEN"):
        monkeypatch.setenv(key, "")  # no model: plain headlines
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("APP_DATABASE_URL", TEST_DB)
    get_settings.cache_clear()
    try:
        assert job.main() == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert out.startswith("Week of 22 Dec 2025:")
    stored = InsightsFeed(app_tables).latest()
    assert stored.insights and stored.insights[0].headline in out


def test_without_drivers_the_job_has_nothing_to_scan(monkeypatch, capsys):
    monkeypatch.setattr(dv, "load", lambda: None)
    assert job.main() == 0 and "no metrics to scan" in capsys.readouterr().out


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
