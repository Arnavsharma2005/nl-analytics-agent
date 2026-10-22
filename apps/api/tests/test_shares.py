"""Share links: a read-only snapshot of an answer, that anyone with the link can open."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agents.chart import ChartSpec
from app.analyst import Analyst, Answer
from app.datasets.store import Datasets
from app.main import create_app
from app.messages import Messages
from app.sessions import Sessions
from app.shares import Shares

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION


@pytest.fixture
def make(db, app_tables):
    llm = FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t"),
                  summary="North leads.")
    analyst, messages, shares = Analyst(llm, db), Messages(app_tables), Shares(app_tables)

    def build(session=True):
        app = create_app(analyst_factory=lambda: analyst, db_factory=lambda: db, llm_factory=lambda: llm,
                         sessions_factory=lambda: Sessions("secret", 3600), quota_factory=lambda: None,
                         messages_factory=lambda: messages, shares_factory=lambda: shares,
                         datasets_factory=lambda: Datasets(app_tables), github_factory=lambda: None,
                         transcriber_factory=lambda: None)
        client = TestClient(app)
        if session:
            client.post("/v1/session", json={})
        return client

    build.llm = llm
    return build


def ask(client, question="Revenue by region") -> dict:
    r = client.post(f"/v1/conversations/{uuid.uuid4()}/messages", json={"question": question},
                    headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 200
    return r.json()


def test_a_shared_answer_opens_for_anyone_without_running_again(make):
    owner = make()
    message = ask(owner)
    r = owner.post(f"/v1/messages/{message['id']}/share")
    assert r.status_code == 200
    share = r.json()
    assert len(share["id"]) == 16 and share["question"] == "Revenue by region" and share["about"] is None

    calls = len(make.llm.calls)
    seen = make(session=False).get(f"/v1/shares/{share['id']}")  # a stranger, with no session
    assert seen.status_code == 200 and len(make.llm.calls) == calls
    answer = seen.json()["answer"]
    assert answer["summary"] == "North leads." and answer["rows"] == message["answer"]["rows"]
    assert answer["sql"] == message["answer"]["sql"] and answer["chart"]["type"] == "bar"
    assert answer["llm_calls"] == [] and answer["failures"] == [] and answer["timings"] == {}


def test_sharing_again_gives_the_same_link(make):
    owner = make()
    message = ask(owner)
    first = owner.post(f"/v1/messages/{message['id']}/share").json()
    assert owner.post(f"/v1/messages/{message['id']}/share").json()["id"] == first["id"]


def test_only_the_owner_can_share_or_unshare(make):
    owner, stranger = make(), make()
    message = ask(owner)
    r = stranger.post(f"/v1/messages/{message['id']}/share")
    assert r.status_code == 404 and r.json()["code"] == "message_not_found"
    share = owner.post(f"/v1/messages/{message['id']}/share").json()
    assert stranger.delete(f"/v1/shares/{share['id']}").status_code == 404
    assert owner.delete(f"/v1/shares/{share['id']}").status_code == 204
    r = stranger.get(f"/v1/shares/{share['id']}")
    assert r.status_code == 404 and r.json()["code"] == "share_not_found"


def test_a_failed_answer_cant_be_shared(make):
    make.llm.sql = ["SELECT nope FROM orders"]
    owner = make()
    message = ask(owner)
    assert message["answer"]["error"]
    r = owner.post(f"/v1/messages/{message['id']}/share")
    assert r.status_code == 422 and r.json()["code"] == "nothing_to_share"


def test_expired_and_malformed_links_are_not_found(make, app_tables):
    owner = make()
    share = owner.post(f"/v1/messages/{ask(owner)['id']}/share").json()
    assert owner.get("/v1/shares/not-a-token").status_code == 422
    with app_tables.begin() as conn:
        conn.execute(text("UPDATE app.shares SET expires_at = now() - interval '1 minute'"))
    assert owner.get(f"/v1/shares/{share['id']}").status_code == 404


def test_shares_follow_their_owner_when_they_sign_in(app_tables):
    shares = Shares(app_tables)
    message = Messages(app_tables)
    key, conversation = uuid.uuid4(), uuid.uuid4()
    message.begin(key, conversation, "sid-1", "Revenue by region")
    saved = message.finish(key, Answer(question="Revenue by region", intent="data_query", summary="x"), [])
    share = shares.share("sid-1", saved)
    shares.adopt("sid-1", "user:7")
    assert not shares.delete(share.id, "sid-1") and shares.delete(share.id, "user:7")

