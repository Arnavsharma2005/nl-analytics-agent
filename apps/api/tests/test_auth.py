"""Signing in with GitHub: the OAuth round trip, who is signed in, their limits and their history."""

import base64
import hashlib
import uuid
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.agents.chart import ChartSpec
from app.analyst import Analyst
from app.auth import PROFILE_URL, TOKEN_URL, GitHub, Users, safe_next
from app.config import get_settings
from app.main import create_app
from app.messages import Messages
from app.sessions import Sessions, Signer, Turnstile
from app.usage import Counters, Quota, Visitor

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION

SITE = "https://app.example"


class FakeGitHub:
    """GitHub's token and profile endpoints, checking the PKCE verifier against the challenge it was sent."""

    def __init__(self):
        self.challenge = None
        self.profile = {"id": 4242, "login": "arnav", "name": "Arnav", "avatar_url": "https://avatars.example/a"}
        self.token_error = None

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == TOKEN_URL:
            form = parse_qs(request.content.decode())
            verifier = form["code_verifier"][0]
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            if self.token_error or form["code"] != ["good-code"] or digest != self.challenge:
                return httpx2.Response(200, json={"error": self.token_error or "bad_verification_code"})
            return httpx2.Response(200, json={"access_token": "gho_test", "token_type": "bearer", "scope": ""})
        if str(request.url) == PROFILE_URL and request.headers["authorization"] == "Bearer gho_test":
            return httpx2.Response(200, json=self.profile)
        return httpx2.Response(401, json={"message": "Bad credentials"})


@pytest.fixture
def github():
    return FakeGitHub()


@pytest.fixture
def make(db, app_tables, github, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", SITE)
    get_settings.cache_clear()
    llm = FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t"))
    analyst, store, users = Analyst(llm, db), Messages(app_tables), Users(app_tables)
    oauth = GitHub("client-id", "client-secret", transport=httpx2.MockTransport(github.handle))

    def build(sessions=None, quota=None, sign_in=True):
        sessions = sessions or Sessions("secret", 3600)
        app = create_app(analyst_factory=lambda: analyst, db_factory=lambda: db, llm_factory=lambda: llm,
                         sessions_factory=lambda: sessions, quota_factory=lambda: quota,
                         messages_factory=lambda: store, users_factory=lambda: users,
                         github_factory=lambda: oauth if sign_in else None, transcriber_factory=lambda: None)
        return TestClient(app, base_url="https://testserver", follow_redirects=False)

    yield build
    get_settings.cache_clear()


def leave_for_github(client: TestClient, github: FakeGitHub, next="/") -> dict[str, str]:
    r = client.get("/v1/auth/github", params={"next": next})
    assert r.status_code == 303
    query = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    github.challenge = query["code_challenge"]
    return query


def sign_in(client: TestClient, github: FakeGitHub, next="/"):
    state = leave_for_github(client, github, next)["state"]
    return client.get("/v1/auth/github/callback", params={"code": "good-code", "state": state})


def ask(client: TestClient, conversation: uuid.UUID, question="Revenue by region"):
    return client.post(f"/v1/conversations/{conversation}/messages", json={"question": question},
                       headers={"Idempotency-Key": str(uuid.uuid4())})


# --- the round trip --------------------------------------------------------------------------------------------

def test_signing_in_goes_to_github_with_pkce_and_comes_back_signed_in(make, github):
    client = make()
    query = leave_for_github(client, github, next="/?q=1")
    assert query["client_id"] == "client-id" and query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == f"{SITE}/v1/auth/github/callback" and "scope" not in query
    r = client.get("/v1/auth/github/callback", params={"code": "good-code", "state": query["state"]})
    assert r.status_code == 303 and r.headers["location"] == f"{SITE}/?q=1"
    assert "aa_user" in r.cookies
    assert client.get("/v1/me").json() == {"user": {"login": "arnav", "name": "Arnav",
                                                    "avatar_url": "https://avatars.example/a"}}


def test_a_callback_that_does_not_match_the_attempt_signs_no_one_in(make, github):
    client = make()
    leave_for_github(client, github)
    r = client.get("/v1/auth/github/callback", params={"code": "good-code", "state": "forged"})
    assert r.headers["location"] == f"{SITE}/?signin=failed" and "aa_user" not in r.cookies
    stranger = make()  # never started a sign-in, so has no attempt cookie
    r = stranger.get("/v1/auth/github/callback", params={"code": "good-code", "state": "anything"})
    assert r.headers["location"] == f"{SITE}/?signin=failed"
    assert client.get("/v1/me").json() == {"user": None}


def test_github_refusing_the_code_signs_no_one_in(make, github):
    github.token_error = "bad_verification_code"
    r = sign_in(make(), github)
    assert r.headers["location"] == f"{SITE}/?signin=failed" and "aa_user" not in r.cookies


def test_saying_no_on_github_goes_back_quietly(make, github):
    client = make()
    state = leave_for_github(client, github, next="/somewhere")["state"]
    r = client.get("/v1/auth/github/callback", params={"error": "access_denied", "state": state})
    assert r.headers["location"] == f"{SITE}/somewhere" and "aa_user" not in r.cookies


def test_after_sign_in_the_visitor_only_ever_lands_on_this_site(make, github):
    for target in ("https://evil.example", "//evil.example", "/\\evil.example", "evil"):
        assert sign_in(make(), github, next=target).headers["location"] == f"{SITE}/"
    assert safe_next("/history?x=1") == "/history?x=1"


def test_signing_in_again_updates_the_profile_and_keeps_the_user(make, github, app_tables):
    sign_in(make(), github)
    github.profile = {**github.profile, "login": "arnav-renamed", "name": None}
    client = make()
    sign_in(client, github)
    assert client.get("/v1/me").json()["user"]["login"] == "arnav-renamed"
    with app_tables.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM app.users").scalar() == 1


def test_signing_out_forgets_the_user(make, github):
    client = make()
    sign_in(client, github)
    assert client.post("/v1/auth/logout").status_code == 204
    assert client.get("/v1/me").json() == {"user": None}


def test_without_github_keys_sign_in_is_off(make):
    client = make(sign_in=False)
    assert client.get("/v1/config").json()["features"]["sign_in"] is False
    r = client.get("/v1/auth/github")
    assert r.status_code == 404 and r.json()["code"] == "sign_in_off"
    assert make().get("/v1/config").json()["features"]["sign_in"] is True


# --- cookies ---------------------------------------------------------------------------------------------------

def test_a_cookie_signed_for_one_purpose_is_not_accepted_for_another():
    key = b"secret"
    user = Signer(key, "user").seal("42", 60)
    assert Signer(key, "user").open(user) == "42"
    assert Signer(key, "signin").open(user) is None and Signer(key).open(user) is None
    assert Signer(key, "user").open(Signer(key).seal("42", 60)) is None
    with pytest.raises(ValueError):
        Signer(key).seal("a.b", 60)


# --- what signing in changes -----------------------------------------------------------------------------------

def test_the_conversation_from_before_signing_in_carries_on(make, github):
    client = make()
    client.post("/v1/session", json={})
    conversation = uuid.uuid4()
    assert ask(client, conversation).status_code == 200
    sign_in(client, github)
    (listed,) = client.get("/v1/conversations").json()
    assert listed["id"] == str(conversation) and listed["title"] == "Revenue by region" and listed["messages"] == 1
    # and it follows the user to another device
    elsewhere = make()
    sign_in(elsewhere, github)
    assert [m["question"] for m in elsewhere.get(f"/v1/conversations/{conversation}/messages").json()] == [
        "Revenue by region"]


def test_history_belongs_to_its_owner(make, github):
    owner = make()
    sign_in(owner, github)
    conversation = uuid.uuid4()
    ask(owner, conversation)
    ask(owner, conversation, "Top 5 customers by revenue")
    messages = owner.get(f"/v1/conversations/{conversation}/messages").json()
    assert [m["question"] for m in messages] == ["Revenue by region", "Top 5 customers by revenue"]
    stranger = make()
    stranger.post("/v1/session", json={})
    assert stranger.get("/v1/conversations").json() == []
    assert stranger.get(f"/v1/conversations/{conversation}/messages").json() == []
    assert make().get("/v1/conversations").json() == []  # no session and not signed in: no history at all


def test_a_signed_in_user_needs_no_bot_check(make, github):
    turnstile = Turnstile("ts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("not asked")))
    client = make(sessions=Sessions("secret", 3600, turnstile))
    assert ask(client, uuid.uuid4()).status_code == 403
    sign_in(client, github)
    assert ask(client, uuid.uuid4()).status_code == 200


def test_signed_in_users_have_their_own_limits_and_no_ip_limit(app_tables):
    quota = Quota(Counters(app_tables), per_session_hour=1, per_ip_day=1, per_user_hour=3, per_user_day=0)
    anonymous = Visitor("sid", "10.0.0.1")
    assert quota.spend(anonymous) is None and quota.spend(anonymous) is not None
    user = Visitor("sid", "10.0.0.1", user=7)  # same session and address, now signed in
    assert [quota.spend(user) is None for _ in range(4)] == [True, True, True, False]
    assert quota.spend(Visitor(None, "10.0.0.1", user=8)) is None  # another user on the same address


def test_a_users_messages_are_kept_longer_than_a_sessions(app_tables, monkeypatch):
    store = Messages(app_tables, retention_days=7, user_retention_days=90)
    old_session, old_user, older_user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for id, owner, age in [(old_session, "sid", 10), (old_user, "user:1", 10), (older_user, "user:1", 100)]:
        store.begin(id, uuid.uuid4(), owner, "q")
        with app_tables.begin() as conn:
            conn.exec_driver_sql(f"UPDATE app.messages SET created_at = now() - interval '{age} days' WHERE id = '{id}'")
    monkeypatch.setattr("app.messages.random.random", lambda: 0.0)  # clean up on the next claim
    store.begin(uuid.uuid4(), uuid.uuid4(), "sid", "q")
    with app_tables.connect() as conn:
        kept = {row[0] for row in conn.exec_driver_sql("SELECT id FROM app.messages")}
    assert old_user in kept and old_session not in kept and older_user not in kept
