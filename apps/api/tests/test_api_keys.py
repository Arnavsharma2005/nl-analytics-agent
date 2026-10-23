"""API keys (app/keys.py, app/api_keys.py): made by a signed-in user, used as a bearer token on /v1, limited to
their origins, and refused once revoked."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analyst import Analyst
from app.auth import Profile, Users
from app.config import get_settings
from app.keys import PER_USER, ApiKeys, KeyRefused, KeyRequestV1, origin_of
from app.main import create_app
from app.sessions import Sessions, Turnstile

from .conftest import FakeLLM

SQL = "SELECT COUNT(*) AS orders FROM orders"


def test_origins_are_normalised_and_checked():
    assert origin_of("https://Example.com/") == "https://example.com"
    assert origin_of("http://localhost:5173") == "http://localhost:5173"
    for bad in ("example.com", "ftp://example.com", "https://example.com/page", "https://u@example.com", "*"):
        with pytest.raises(ValueError):
            origin_of(bad)
    assert KeyRequestV1(name="w", origins=["https://b.com", "https://B.com/"]).origins == ["https://b.com"]
    with pytest.raises(ValidationError):
        KeyRequestV1(name="w", origins=["not an origin"])


def user(app_tables, login="arnav"):
    return Users(app_tables).sign_in(Profile(github_id=sum(map(ord, login)), login=login, name=None,
                                             avatar_url=None))


def test_a_key_names_its_user_until_it_is_revoked(app_tables):
    keys, me = ApiKeys(app_tables), user(app_tables)
    made = keys.create(me.id, KeyRequestV1(name="Script"))
    assert made.key.startswith("aak_") and made.hint == made.key[:8] and made.last_used_at is None
    assert keys.user_for(made.key, None) == me.id
    [listed] = keys.list(me.id)
    assert listed.id == made.id and listed.last_used_at is not None and "key" not in listed.model_dump()

    assert not keys.revoke(made.id, user(app_tables, "someone").id)  # only its owner can revoke it
    assert keys.revoke(made.id, me.id) and not keys.revoke(made.id, me.id)
    with pytest.raises(KeyRefused) as refused:
        keys.user_for(made.key, None)
    assert not refused.value.origin and keys.list(me.id) == []


def test_unknown_keys_and_the_limit(app_tables):
    keys, me = ApiKeys(app_tables), user(app_tables)
    for bad in ("aak_nope", "not-a-key", ""):
        with pytest.raises(KeyRefused):
            keys.user_for(bad, None)
    made = [keys.create(me.id, KeyRequestV1(name=f"k{n}")) for n in range(PER_USER)]
    assert keys.create(me.id, KeyRequestV1(name="one more")) is None
    keys.revoke(made[0].id, me.id)
    assert keys.create(me.id, KeyRequestV1(name="one more")) is not None


def test_an_allowlist_refuses_other_sites_but_not_scripts(app_tables):
    keys, me = ApiKeys(app_tables), user(app_tables)
    key = keys.create(me.id, KeyRequestV1(name="Widget", origins=["https://shop.example"])).key
    assert keys.user_for(key, "https://shop.example") == me.id
    assert keys.user_for(key, None) == me.id  # no Origin header: not a browser
    for other in ("https://evil.example", "https://shop.example.evil.example", "null"):
        with pytest.raises(KeyRefused) as refused:
            keys.user_for(key, other)
        assert refused.value.origin


@pytest.fixture
def make(db, app_tables, monkeypatch):
    monkeypatch.setenv("API_KEYS", "true")
    get_settings.cache_clear()
    sessions, users = Sessions("secret", 3600, Turnstile("ts", lambda *a, **k: None)), Users(app_tables)

    def build(llm=None):
        llm = llm or FakeLLM(sql=[SQL])
        app = create_app(analyst_factory=lambda *_: Analyst(llm, db), db_factory=lambda: db, llm_factory=lambda: llm,
                         sessions_factory=lambda: sessions, quota_factory=lambda: None, users_factory=lambda: users,
                         github_factory=lambda: object(), transcriber_factory=lambda: None,
                         api_keys_factory=lambda: ApiKeys(app_tables))
        return TestClient(app)

    def signed_in(client, login="arnav"):
        me = users.sign_in(Profile(github_id=sum(map(ord, login)), login=login, name=None, avatar_url=None))
        client.cookies.set("aa_user", sessions.signer("user").seal(str(me.id), 3600))
        return client

    yield build, signed_in
    get_settings.cache_clear()


def ask(client, headers):
    return client.post("/v1/conversations/7f1d2c3b-0000-4000-8000-000000000001/messages",
                       json={"question": "How many orders?"}, headers=headers)


def test_a_key_asks_as_its_user_past_the_bot_check(make):
    build, signed_in = make
    web = signed_in(build())
    made = web.post("/v1/keys", json={"name": "Script", "origins": ["https://shop.example"]})
    assert made.status_code == 201 and made.json()["origins"] == ["https://shop.example"]
    key = made.json()["key"]

    script = build()  # no cookies at all: the bot check would refuse it
    assert ask(script, {}).status_code == 403
    answered = ask(script, {"Authorization": f"Bearer {key}"})
    assert answered.status_code == 200 and answered.json()["answer"]["rows"]

    r = ask(script, {"Authorization": f"Bearer {key}", "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "origin_not_allowed"
    assert ask(script, {"Authorization": f"Bearer {key}", "Origin": "https://shop.example"}).status_code == 200

    # A key cannot make or list keys; only the signed-in web app can.
    r = script.post("/v1/keys", json={"name": "Another"}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401 and r.json()["code"] == "sign_in_required"

    assert web.delete(f"/v1/keys/{made.json()['id']}").status_code == 204
    r = ask(script, {"Authorization": f"Bearer {key}"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_api_key"
    assert web.delete(f"/v1/keys/{made.json()['id']}").status_code == 404
    assert web.get("/v1/keys").json() == []


def test_too_many_keys(make):
    build, signed_in = make
    web = signed_in(build())
    for n in range(PER_USER):
        assert web.post("/v1/keys", json={"name": f"k{n}"}).status_code == 201
    r = web.post("/v1/keys", json={"name": "one more"})
    assert r.status_code == 409 and r.json()["code"] == "too_many_keys"


def test_without_the_feature_keys_are_refused_and_ignored(db, app_tables):
    get_settings.cache_clear()
    app = create_app(db_factory=lambda: db, sessions_factory=lambda: Sessions("secret", 3600),
                     quota_factory=lambda: None, github_factory=lambda: None, transcriber_factory=lambda: None)
    client = TestClient(app)
    r = client.get("/v1/keys")
    assert r.status_code == 404 and r.json()["code"] == "api_keys_off"


def test_any_site_may_send_a_keyed_request_but_only_listed_ones_get_answers(make):
    build, signed_in = make
    web = signed_in(build())
    key = web.post("/v1/keys", json={"name": "Widget", "origins": ["https://shop.example"]}).json()["key"]
    site = build()

    pre = site.options("/v1/conversations/7f1d2c3b-0000-4000-8000-000000000001/messages", headers={
        "Origin": "https://shop.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type, idempotency-key"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "https://shop.example"
    assert "access-control-allow-credentials" not in pre.headers

    ok = ask(site, {"Authorization": f"Bearer {key}", "Origin": "https://shop.example"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "https://shop.example"
    refused = ask(site, {"Authorization": f"Bearer {key}", "Origin": "https://evil.example"})
    assert refused.status_code == 403 and refused.headers["access-control-allow-origin"] == "https://evil.example"
    assert refused.json()["code"] == "origin_not_allowed"  # readable, so the widget can say why

    # Without a key, a cookie request from another site gets no CORS answer.
    plain = site.get("/v1/config", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in plain.headers


def test_the_widget_script_is_served_to_any_site(make, tmp_path, monkeypatch):
    script = tmp_path / "nl-analyst.js"
    script.write_text("customElements.define('nl-analyst', class extends HTMLElement {});")
    monkeypatch.setattr("app.main.WIDGET", script)
    build, _ = make
    r = build().get("/widget.js")
    assert r.status_code == 200 and r.text.startswith("customElements")
    assert r.headers["content-type"].startswith("text/javascript") and r.headers["access-control-allow-origin"] == "*"
