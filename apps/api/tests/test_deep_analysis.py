"""Deep Analysis (app/deep_analysis.py): a plan of specific questions, each answered by the analyst, and a report
whose figures are checked against the steps it cites; stored and served by app/reports.py and app/api_reports.py."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.analyst import Analyst
from app.auth import Profile, Users
from app.brief import database
from app.config import get_settings
from app.deep_analysis import Capabilities, DeepAnalysis, Draft, DraftSection, Plan, plan
from app.main import create_app
from app.reports import INTERRUPTED, Reports
from app.sessions import Sessions

from .conftest import FakeLLM

BY_REGION = """SELECT r.name AS region, ROUND(SUM(oi.quantity * oi.unit_price)::numeric, 2) AS revenue
FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN customers c ON c.customer_id = o.customer_id
JOIN regions r ON r.region_id = c.region_id WHERE o.status = 'completed' GROUP BY r.name ORDER BY revenue DESC"""
BY_YEAR = """SELECT EXTRACT(YEAR FROM order_date)::int AS year, COUNT(*) AS orders FROM orders
WHERE status = 'completed' GROUP BY 1 ORDER BY 1"""
STEPS = [{"question": "Revenue by region", "purpose": "where revenue comes from"},
         {"question": "Completed orders by year", "purpose": "the trend"}]


class Writer(FakeLLM):
    """FakeLLM that also plans and writes reports: `sections` are the first draft's, `fixes` each rewrite's."""

    def __init__(self, steps=STEPS, sections=None, fixes=(), summary="Revenue is concentrated in a few regions.",
                 **kw):
        super().__init__(sql=[BY_REGION, BY_YEAR], **kw)
        self.steps, self.sections, self.fixes, self.summary = steps, sections or [], list(fixes), summary

    def structured(self, output, system, user, tier="strong"):
        if output is Plan:
            self.calls.append(("Plan", user))
            self.systems.append(("Plan", system))
            return Plan.model_validate({"steps": self.steps})
        if output is Draft:
            self.calls.append(("Draft", user))
            return Draft(title="Sales review", summary=self.summary,
                         sections=[DraftSection.model_validate(s) for s in self.sections])
        if output is DraftSection:
            self.calls.append(("DraftSection", user))
            return DraftSection.model_validate(self.fixes.pop(0))
        return super().structured(output, system, user, tier)


def figures(db):
    regions, years = db.run(BY_REGION).rows, db.run(BY_YEAR).rows
    return regions, years


def analysis(db, llm):
    return DeepAnalysis(llm, Analyst(llm, db), database(), sorted(db.schema().tables))


def test_each_step_is_answered_and_every_figure_is_traced(db):
    regions, years = figures(db)
    top, first = regions[0], years[0]
    llm = Writer(sections=[
        {"heading": "Where revenue comes from", "text": f"{top[0]} led with {top[1]:,.2f} in revenue.", "steps": [1]},
        {"heading": "Orders", "text": f"There were {first[1]:,} completed orders in {first[0]}.", "steps": [2]},
    ])
    seen = []
    report = analysis(db, llm).run("How are sales going?", lambda *p: seen.append(p))
    assert [s.question for s in report.steps] == ["Revenue by region", "Completed orders by year"]
    assert report.steps[0].answer.rows == regions and report.steps[1].answer.columns == ["year", "orders"]
    assert report.steps[0].answer.llm_calls == []  # which models answered stays private
    assert [(s.steps, s.unsupported) for s in report.sections] == [([1], []), ([2], [])]
    assert report.title == "Sales review" and report.summary.startswith("Revenue is concentrated")
    assert seen == [("planned", 0, 2), ("step", 1, 2), ("step", 2, 2), ("writing", 2, 2)]
    writer_prompt = next(u for name, u in llm.calls if name == "Draft")
    assert "Step 1: Revenue by region" in writer_prompt and top[0] in writer_prompt


def test_an_invented_figure_is_sent_back_once_and_flagged_if_it_stays(db):
    regions, _ = figures(db)
    made_up = {"heading": "Regions", "text": "Revenue grew by 987,654.32 in every region.", "steps": [1]}
    fixed = {"heading": "Regions", "text": f"{regions[0][0]} brought in {regions[0][1]:,.2f}.", "steps": [1]}

    llm = Writer(sections=[made_up], fixes=[fixed])
    [section] = analysis(db, llm).run("How are sales going?").sections
    assert section.unsupported == [] and section.text == fixed["text"]
    assert "987,654.32" in next(u for name, u in llm.calls if name == "DraftSection")

    [section] = analysis(db, Writer(sections=[made_up], fixes=[made_up])).run("How are sales going?").sections
    assert section.unsupported == ["987,654.32"]


def test_a_figure_from_another_step_does_not_count(db):
    _, years = figures(db)
    wrong_step = {"heading": "Orders", "text": f"There were {years[0][1]:,} orders.", "steps": [1]}
    llm = Writer(sections=[wrong_step], fixes=[wrong_step])
    [section] = analysis(db, llm).run("How are sales going?").sections
    assert section.unsupported == [f"{years[0][1]:,}"]


def test_failed_and_unknown_steps_are_not_cited(db):
    llm = Writer(sections=[{"heading": "All", "text": "Sales are steady.", "steps": [1, 2, 9]}])
    llm.sql = [BY_REGION, "SELECT nope FROM nowhere"]  # the second step never finds a working query
    report = analysis(db, llm).run("How are sales going?")
    assert report.steps[1].answer.error and report.sections[0].steps == [1]
    assert "Step 2: Completed orders by year\nFailed:" in next(u for name, u in llm.calls if name == "Draft")


def test_a_summary_with_an_invented_figure_is_dropped(db):
    report = analysis(db, Writer(summary="Revenue reached 123,456,789.")).run("How are sales going?")
    assert report.summary == ""


def test_with_no_step_answered_there_is_no_report(db):
    llm = Writer()
    llm.sql = ["SELECT nope FROM nowhere"]
    with pytest.raises(ValueError, match="none of the steps"):
        analysis(db, llm).run("How are sales going?")


def test_the_plan_is_deduplicated_capped_and_offers_only_tools_that_are_on(db):
    many = [{"question": f"Question {n % 10}", "purpose": "x"} for n in range(12)]
    llm = Writer(steps=many)
    steps = plan(llm, "Everything?", database(), ["orders"], Capabilities())
    assert [s.question for s in steps] == [f"Question {n}" for n in range(8)]
    plan(llm, "Everything?", database(), ["orders"], Capabilities(why=True, forecast=True))
    off, on = (s for name, s in llm.systems if name == "Plan")
    assert "why a metric rose or fell" not in off and "why a metric rose or fell" in on and "forecast" in on


# --- Storage and the API -------------------------------------------------------------------------------------------


def test_a_report_cut_off_mid_run_reads_as_failed(app_tables):
    store = Reports(app_tables)
    started = store.start("user:1", "How are sales going?")
    assert store.running("user:1") == 1 and store.get(started.id, "user:2") is None
    with app_tables.begin() as conn:
        conn.execute(text("UPDATE app.reports SET updated_at = now() - :ago"), {"ago": timedelta(minutes=11)})
    gone = store.get(started.id, "user:1")
    assert gone.status == "failed" and gone.error == INTERRUPTED and store.running("user:1") == 0


@pytest.fixture
def make(db, app_tables, monkeypatch):
    monkeypatch.setenv("DEEP_ANALYSIS", "true")
    monkeypatch.setenv("REPORTS_PER_DAY", "2")
    get_settings.cache_clear()
    sessions, users, reports = Sessions("secret", 3600), Users(app_tables), Reports(app_tables)

    def build(llm, login: str | None = "arnav"):
        app = create_app(db_factory=lambda: db, sessions_factory=lambda: sessions, quota_factory=lambda: None,
                         users_factory=lambda: users, github_factory=lambda: object(), transcriber_factory=lambda: None,
                         reports_factory=lambda: reports, deep_analysis_factory=lambda: analysis(db, llm))
        client = TestClient(app)
        client.post("/v1/session", json={})
        if login:
            user = users.sign_in(Profile(github_id=sum(map(ord, login)), login=login, name=None, avatar_url=None))
            client.cookies.set("aa_user", sessions.signer("user").seal(str(user.id), 3600))
        return client

    yield build
    get_settings.cache_clear()


def test_a_signed_in_user_starts_a_report_and_reads_it(make, db):
    regions, _ = figures(db)
    llm = Writer(sections=[{"heading": "Regions", "text": f"{regions[0][0]} led.", "steps": [1]}])
    client = make(llm)
    assert client.get("/v1/config").json()["features"]["reports"] is True

    started = client.post("/v1/reports", json={"question": "How are sales going?"})
    assert started.status_code == 202 and started.json()["status"] == "running"
    done = client.get(f"/v1/reports/{started.json()['id']}").json()  # the background task ran after the response
    assert done["status"] == "done" and done["steps_done"] == 2 and done["steps_total"] == 2
    assert done["report"]["sections"][0]["steps"] == [1] and done["report"]["steps"][0]["answer"]["rows"]
    [listed] = client.get("/v1/reports").json()
    assert listed["id"] == started.json()["id"] and listed["report"] is None

    other = make(llm, login="someone")
    assert other.get(f"/v1/reports/{started.json()['id']}").status_code == 404


def test_reports_need_a_sign_in_and_are_limited_per_day(make):
    anonymous = make(Writer(), login=None)
    r = anonymous.post("/v1/reports", json={"question": "How are sales going?"})
    assert r.status_code == 401 and r.json()["code"] == "sign_in_required"

    client = make(Writer(sections=[]))
    for _ in range(2):
        assert client.post("/v1/reports", json={"question": "How are sales going?"}).status_code == 202
    r = client.post("/v1/reports", json={"question": "How are sales going?"})
    assert r.status_code == 429 and r.json()["code"] == "report_quota_exceeded" and r.headers["retry-after"]


def test_one_report_at_a_time(make, app_tables):
    client = make(Writer())
    user = Users(app_tables).sign_in(Profile(github_id=sum(map(ord, "arnav")), login="arnav", name=None,
                                             avatar_url=None))
    Reports(app_tables).start(f"user:{user.id}", "Still running")
    r = client.post("/v1/reports", json={"question": "How are sales going?"})
    assert r.status_code == 409 and r.json()["code"] == "report_running"


def test_a_failed_report_says_why(make):
    llm = Writer()
    llm.sql = ["SELECT nope FROM nowhere"]
    client = make(llm)
    id = client.post("/v1/reports", json={"question": "How are sales going?"}).json()["id"]
    failed = client.get(f"/v1/reports/{id}").json()
    assert failed["status"] == "failed" and "none of the steps could be answered" in failed["error"]


def test_without_the_feature_reports_are_refused(db, app_tables):
    get_settings.cache_clear()
    app = create_app(db_factory=lambda: db, sessions_factory=lambda: Sessions("secret", 3600),
                     quota_factory=lambda: None, github_factory=lambda: None, transcriber_factory=lambda: None)
    client = TestClient(app)
    r = client.post("/v1/reports", json={"question": "How are sales going?"})
    assert r.status_code == 404 and r.json()["code"] == "reports_off"
    assert client.get("/v1/config").json()["features"]["reports"] is False
