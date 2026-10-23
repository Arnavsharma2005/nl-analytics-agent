"""Feature flags (app/flags.py): who gets each feature, set in the database and picked up without a redeploy."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.analyst import Analyst
from app.auth import Profile, Users
from app.config import get_settings
from app.flags import FLAGS, TTL_S, Flags
from app.main import create_app
from app.reports import Reports
from app.schedules import Notifier, ScheduleRequestV1, Schedules, run_due
from app.sessions import Sessions

from .conftest import FakeLLM

CURATOR, MEMBER = 1, 2


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def make(app_tables):
    def build(defaults=None, clock=None, engine=None) -> Flags:
        return Flags(engine or app_tables, defaults or {}, lambda user: user == CURATOR, **({"clock": clock} if clock else {}))

    return build


def test_a_flag_without_a_row_follows_its_setting(make):
    flags = make({"forecasting": True})
    assert flags.audiences()["forecasting"] == "everyone" and flags.audiences()["deep_analysis"] == "off"
    assert flags.on("forecasting", None) and not flags.on("deep_analysis", CURATOR)


@pytest.mark.parametrize(("audience", "anonymous", "member", "curator"), [
    ("off", False, False, False),
    ("curators", False, False, True),
    ("users", False, True, True),
    ("everyone", True, True, True),
])
def test_who_gets_a_flag(make, audience, anonymous, member, curator):
    flags = make({"deep_analysis": True})
    flags.set("deep_analysis", audience)
    assert (flags.on("deep_analysis", None), flags.on("deep_analysis", MEMBER),
            flags.on("deep_analysis", CURATOR)) == (anonymous, member, curator)


def test_switches_are_the_flags_on_for_a_visitor(make):
    flags = make({"check_results": True})
    flags.set("forecasting", "users")
    assert flags.switches(None) == {"check_results"}
    assert flags.switches(MEMBER) == {"check_results", "forecasting"}


def test_a_change_elsewhere_is_seen_within_a_minute(make):
    clock = Clock()
    here, elsewhere = make(clock=clock), make()
    assert here.audiences()["schedules"] == "off"
    elsewhere.set("schedules", "everyone")
    clock.now += TTL_S - 1
    assert here.audiences()["schedules"] == "off"  # read under a minute ago
    clock.now += 1
    assert here.audiences()["schedules"] == "everyone"


def test_reset_follows_the_setting_again(make):
    flags = make({"forecasting": True})
    flags.set("forecasting", "off")
    assert not flags.on("forecasting", None)
    flags.set("forecasting", None)
    assert flags.on("forecasting", None)


def test_unknown_names_and_audiences_are_refused(make):
    with pytest.raises(ValueError, match="unknown flag"):
        make().set("everything", "everyone")
    with pytest.raises(ValueError, match="unknown audience"):
        make().set("forecasting", "admins")


def test_an_unreadable_database_keeps_the_last_flags(make, app_tables):
    clock = Clock()
    make().set("forecasting", "everyone")
    flags = make(clock=clock)
    assert flags.on("forecasting", None)
    flags._engine = create_engine("postgresql+psycopg://nobody:wrong@localhost:1/none")
    clock.now += TTL_S
    assert flags.on("forecasting", None)
    gone = make(engine=create_engine("postgresql+psycopg://nobody:wrong@localhost:1/none"), defaults={"schedules": True})
    assert gone.audiences()["schedules"] == "everyone" and gone.audiences()["forecasting"] == "off"


def test_every_flag_is_a_setting():
    settings = get_settings()
    assert all(isinstance(getattr(settings, name), bool) for name in FLAGS)


@pytest.fixture
def signed_in(db, app_tables):
    """A client per GitHub login, signed in, on an app whose flags are `flags`."""
    sessions, users = Sessions("secret", 3600), Users(app_tables)
    flags = Flags(app_tables, {}, lambda user: users.get(user).login == "arnav")
    asked: list[frozenset[str] | None] = []

    def analyst(on=None):
        asked.append(on)
        return Analyst(FakeLLM(), db)

    def client(login: str | None) -> TestClient:
        app = create_app(analyst_factory=analyst, db_factory=lambda: db, sessions_factory=lambda: sessions,
                         quota_factory=lambda: None, users_factory=lambda: users, github_factory=lambda: object(),
                         transcriber_factory=lambda: None, reports_factory=lambda: Reports(app_tables),
                         flags_factory=lambda: flags)
        c = TestClient(app)
        c.post("/v1/session", json={})
        if login:
            user = users.sign_in(Profile(github_id=sum(map(ord, login)), login=login, name=None, avatar_url=None))
            c.cookies.set("aa_user", sessions.signer("user").seal(str(user.id), 3600))
        return c

    return client, flags, asked


def test_deep_analysis_goes_to_curators_first(signed_in):
    client, flags, _ = signed_in
    flags.set("deep_analysis", "curators")
    me, member = client("arnav"), client("someone")
    assert me.get("/v1/config").json()["features"]["reports"] is True
    assert member.get("/v1/config").json()["features"]["reports"] is False
    assert me.get("/v1/reports").status_code == 200
    r = member.get("/v1/reports")
    assert r.status_code == 404 and r.json()["code"] == "reports_off"

    flags.set("deep_analysis", "users")
    assert member.get("/v1/reports").status_code == 200


def test_a_question_is_answered_with_the_askers_flags(signed_in):
    client, flags, asked = signed_in
    flags.set("check_results", "everyone")
    flags.set("forecasting", "users")
    ask = {"question": "How many orders are there?"}
    assert client(None).post("/v1/conversations/00000000-0000-4000-8000-000000000001/messages", json=ask).is_success
    assert client("someone").post("/v1/conversations/00000000-0000-4000-8000-000000000002/messages",
                                  json=ask).is_success
    assert asked == [frozenset({"check_results"}), frozenset({"check_results", "forecasting"})]


def test_schedules_are_paused_for_owners_the_flag_leaves_out(db, app_tables):
    users, store = Users(app_tables), Schedules(app_tables)
    member = users.sign_in(Profile(github_id=5, login="member", name=None, avatar_url=None))
    made = store.create(member.id, ScheduleRequestV1(question="Revenue", channel="telegram", to="1"))
    with app_tables.begin() as conn:
        conn.exec_driver_sql("UPDATE app.schedules SET next_run_at = now() - interval '1 minute'")

    lines = run_due(store, Analyst(FakeLLM(), db), Notifier(None, None, None), allowed=lambda user: False)
    assert lines == [f"{made.id}: paused"]
    [paused] = store.list(member.id)
    assert paused.last_status == "paused" and paused.next_run_at > datetime.now(UTC)  # the next run still comes
