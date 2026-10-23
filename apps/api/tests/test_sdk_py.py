"""The Python SDK (packages/sdk-py) against the real app: its generated models read what the API sends, a key asks
as its user, and refusals arrive as ApiError with the API's code."""

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.analyst import Analyst as AppAnalyst
from app.auth import Profile, Users
from app.config import get_settings
from app.keys import ApiKeys, KeyRequestV1
from app.main import create_app
from app.reports import Reports
from app.sessions import Sessions, Turnstile

from .test_deep_analysis import Writer, analysis

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "sdk-py" / "src"))
from nl_analytics import Analyst, ApiError  # noqa: E402

SQL = "SELECT COUNT(*) AS orders FROM orders"


@pytest.fixture
def sdk(db, app_tables, monkeypatch):
    monkeypatch.setenv("API_KEYS", "true")
    monkeypatch.setenv("DEEP_ANALYSIS", "true")
    get_settings.cache_clear()
    llm = Writer(sections=[])
    llm.sql = [SQL]
    users = Users(app_tables)
    me = users.sign_in(Profile(github_id=1, login="arnav", name=None, avatar_url=None))
    key = ApiKeys(app_tables).create(me.id, KeyRequestV1(name="SDK")).key
    sessions = Sessions("secret", 3600, Turnstile("ts", lambda *a, **k: None))  # the bot check is on
    app = create_app(analyst_factory=lambda: AppAnalyst(llm, db), db_factory=lambda: db, llm_factory=lambda: llm,
                     sessions_factory=lambda: sessions, quota_factory=lambda: None, users_factory=lambda: users,
                     github_factory=lambda: object(), transcriber_factory=lambda: None,
                     api_keys_factory=lambda: ApiKeys(app_tables), reports_factory=lambda: Reports(app_tables),
                     deep_analysis_factory=lambda: analysis(db, llm))
    http = TestClient(app)
    yield lambda api_key=key: Analyst("http://testserver", api_key, http=http)
    get_settings.cache_clear()


def test_asking_and_following_up(sdk, db):
    analyst = sdk()
    chat = analyst.conversation()
    first = chat.ask("How many orders are there?")
    assert first.conversation_id == chat.id and first.answer.columns == ["orders"]
    assert first.answer.rows == db.run(SQL).rows and first.answer.sql
    again = chat.ask("And completed ones?")
    assert again.conversation_id == chat.id and again.id != first.id


def test_the_same_idempotency_key_returns_the_first_answer(sdk):
    analyst, key, chat = sdk(), uuid4(), uuid4()
    first = analyst.ask("How many orders are there?", conversation_id=chat, idempotency_key=key)
    assert analyst.ask("How many orders are there?", conversation_id=chat, idempotency_key=key).id == first.id


def test_insights_and_reports(sdk):
    analyst = sdk()
    assert analyst.insights().insights == []
    started = analyst.start_report("How are sales going?")
    assert started.status == "running"
    done = analyst.wait_for_report(started.id, every=0)
    assert done.status == "done" and done.report.steps


def test_refusals_carry_the_api_code(sdk):
    with pytest.raises(ApiError) as refused:
        sdk(api_key="aak_not_a_key").ask("How many orders are there?")
    assert refused.value.status == 401 and refused.value.code == "invalid_api_key"
    with pytest.raises(ApiError) as refused:
        sdk(api_key=None).ask("How many orders are there?")  # no key and no session: the bot check refuses
    assert refused.value.code == "bot_check_required"
