"""The /v1 API: streamed answers, idempotent retries, feedback, explanations and voice."""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import semantic
from app.agents.chart import ChartSpec
from app.analyst import Analyst
from app.context import Context
from app.llm import AllProvidersFailed
from app.main import create_app
from app.messages import Messages
from app.sessions import Sessions
from app.transcribe import Transcriber, TranscriptionFailed
from app.usage import Counters, Quota

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION

SSE = {"Accept": "text/event-stream"}
WHISPER = "whisper-large-v3-turbo"


class FakeTranscriber:
    def __init__(self, fail=False):
        self.fail = fail
        self.seen = []

    def transcribe(self, audio, content_type, language=None):
        self.seen.append((len(audio), content_type, language))
        if self.fail:
            raise TranscriptionFailed("bad audio")
        return "Revenue by region"


@pytest.fixture
def llm():
    return FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t"),
                   summary="North leads.")


@pytest.fixture(scope="module")
def suggester(db):
    return Context(semantic.load(), db.schema())


@pytest.fixture
def make(db, app_tables, llm, suggester):
    def build(quota=None, transcriber=None, messages=None, analyst_llm=None):
        analyst = Analyst(analyst_llm or llm, db)
        store = messages or Messages(app_tables)
        app = create_app(analyst_factory=lambda: analyst, db_factory=lambda: db, llm_factory=lambda: llm,
                         sessions_factory=lambda: Sessions("secret", 3600), quota_factory=lambda: quota,
                         messages_factory=lambda: store, suggester_factory=lambda: suggester,
                         transcriber_factory=lambda: transcriber)
        client = TestClient(app)
        client.post("/v1/session", json={})
        return client

    return build


def events(response) -> list[tuple[str, dict]]:
    out = []
    for block in response.text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            out.append((fields["event"], json.loads(fields["data"])))
    return out


def ask(client, question="Revenue by region", key=None, conversation=None, stream=True):
    headers = dict(SSE) if stream else {}
    if key:
        headers["Idempotency-Key"] = str(key)
    return client.post(f"/v1/conversations/{conversation or uuid.uuid4()}/messages", json={"question": question},
                       headers=headers)


# --- asking ------------------------------------------------------------------------------------------------

def test_the_answer_streams_table_then_chart_then_summary(make):
    key, conversation = uuid.uuid4(), uuid.uuid4()
    r = ask(make(), key=key, conversation=conversation)
    got = events(r)
    kinds = [k for k, _ in got]
    assert kinds[0] == "message" and got[0][1] == {"id": str(key), "conversation_id": str(conversation)}
    assert kinds.index("table") < kinds.index("chart") < kinds.index("token") < kinds.index("done") == len(kinds) - 1
    table = dict(got)["table"]
    assert table["columns"] == ["region", "revenue"] and len(table["rows"]) == 6
    assert "".join(d["text"] for k, d in got if k == "token") == "North leads."
    done = got[-1][1]
    assert done["id"] == str(key) and done["answer"]["summary"] == "North leads."
    assert len(done["suggestions"]) == 3 and "Revenue by region" not in done["suggestions"]


def test_without_a_stream_the_message_comes_back_as_json(make):
    r = ask(make(), stream=False)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["answer"]["rows"] and body["question"] == "Revenue by region" and len(body["suggestions"]) == 3


def test_out_of_scope_questions_suggest_starters(make, db, app_tables):
    client = make(analyst_llm=FakeLLM(intent="out_of_scope"))
    body = ask(client, "What's the weather?", stream=False).json()
    starters = [q.question for q in semantic.load().queries if q.starter]
    assert body["suggestions"] == starters[:3]


def standalone_prompts(llm: FakeLLM) -> list[str]:
    return [user for name, user in llm.calls if name == "Standalone"]


def test_a_follow_up_is_read_with_the_earlier_questions_of_its_conversation(make, llm):
    client, conversation = make(), uuid.uuid4()
    ask(client, "Revenue by region", conversation=conversation, stream=False)
    assert standalone_prompts(llm) == []  # a first question is asked on its own
    llm.standalone = "Revenue by region in 2024"
    done = events(ask(client, "and only for 2024?", conversation=conversation))[-1][1]
    (prompt,) = standalone_prompts(llm)
    assert "1. Revenue by region" in prompt and "SQL: SELECT r.name AS region" in prompt and "North leads." in prompt
    assert done["question"] == "and only for 2024?"
    assert done["answer"]["standalone_question"] == "Revenue by region in 2024"
    # the next follow-up sees the earlier one as it was understood
    ask(client, "and 2023?", conversation=conversation, stream=False)
    assert "2. Revenue by region in 2024" in standalone_prompts(llm)[-1]


def test_other_conversations_and_sessions_are_not_history(make, llm):
    owner, stranger, conversation = make(), make(), uuid.uuid4()
    ask(owner, conversation=conversation, stream=False)
    ask(owner, conversation=uuid.uuid4(), stream=False)
    ask(stranger, conversation=conversation, stream=False)
    assert standalone_prompts(llm) == []


def test_a_follow_up_is_answered_on_its_own_when_history_cannot_be_read(make, llm, app_tables):
    class NoHistory(Messages):
        def history(self, *args, **kwargs):
            raise RuntimeError("database down")

    client, conversation = make(messages=NoHistory(app_tables)), uuid.uuid4()
    ask(client, conversation=conversation, stream=False)
    r = ask(client, "and only for 2024?", conversation=conversation, stream=False)
    assert r.status_code == 200 and r.json()["answer"]["standalone_question"] is None
    assert standalone_prompts(llm) == []


def test_a_retry_with_the_same_key_replays_without_asking_again(make, llm, app_tables):
    client = make(quota=Quota(Counters(app_tables), per_session_hour=1, per_ip_day=0))
    key, conversation = uuid.uuid4(), uuid.uuid4()
    first = events(ask(client, key=key, conversation=conversation))
    calls = len(llm.calls)
    again = ask(client, key=key, conversation=conversation)  # the quota (1 an hour) is not spent again
    assert again.status_code == 200 and len(llm.calls) == calls
    replay = events(again)
    assert [k for k, _ in replay] == ["message", "table", "chart", "token", "done"]
    assert replay[-1][1]["answer"]["rows"] == first[-1][1]["answer"]["rows"]
    assert ask(client, key=key, conversation=conversation, stream=False).json()["id"] == str(key)
    assert ask(client).status_code == 429  # a new question does spend it


def test_a_key_reused_for_another_question_is_refused(make):
    client, key, conversation = make(), uuid.uuid4(), uuid.uuid4()
    ask(client, key=key, conversation=conversation)
    r = ask(client, "Revenue by month", key=key, conversation=conversation)
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"


def test_a_key_still_being_answered_is_refused_until_it_goes_stale(make, app_tables):
    client, key, conversation = make(), uuid.uuid4(), uuid.uuid4()
    session = Sessions("secret", 3600).read(client.cookies.get("aa_session"))
    Messages(app_tables).begin(key, conversation, session, "Revenue by region")
    r = ask(client, key=key, conversation=conversation)
    assert r.status_code == 409 and r.json()["code"] == "request_in_progress" and r.headers["retry-after"] == "5"
    with app_tables.begin() as conn:
        conn.execute(text("UPDATE app.messages SET created_at = now() - interval '5 minutes'"))
    assert events(ask(client, key=key, conversation=conversation))[-1][0] == "done"


def test_a_failed_answer_can_be_retried_with_the_same_key(make):
    class Down(FakeLLM):
        def structured(self, output, system, user, tier="strong"):
            raise AllProvidersFailed([{"provider": "Groq", "error": "rate limited"}])

    key, conversation = uuid.uuid4(), uuid.uuid4()
    got = events(ask(make(analyst_llm=Down()), key=key, conversation=conversation))
    assert got[-1][0] == "error" and got[-1][1]["code"] == "all_providers_failed"
    assert events(ask(make(), key=key, conversation=conversation))[-1][0] == "done"


def test_a_question_over_quota_is_not_kept(make, app_tables):
    client = make(quota=Quota(Counters(app_tables), per_session_hour=0, per_ip_day=1))
    ask(client)
    r = ask(client)
    assert r.status_code == 429 and r.json()["code"] == "quota_exceeded"
    with app_tables.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM app.messages")).scalar() == 1


def test_questions_are_answered_when_the_app_database_is_down(make):
    broken = Messages(create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none"))
    got = events(ask(make(messages=broken)))
    assert got[-1][0] == "done" and got[-1][1]["answer"]["summary"] == "North leads."


# --- reading, rating and explaining -------------------------------------------------------------------------

def finished(client) -> str:
    return ask(client, stream=False).json()["id"]


def test_messages_belong_to_their_session(make):
    owner, stranger = make(), make()
    message = finished(owner)
    assert owner.get(f"/v1/messages/{message}").json()["id"] == message
    r = stranger.get(f"/v1/messages/{message}")
    assert r.status_code == 404 and r.json()["code"] == "message_not_found"
    assert stranger.post(f"/v1/messages/{message}/feedback", json={"rating": "up"}).status_code == 404
    assert stranger.post(f"/v1/messages/{message}/explain").status_code == 404


def test_feedback_is_kept_and_can_be_changed(make, app_tables):
    client = make()
    message = finished(client)
    assert client.post(f"/v1/messages/{message}/feedback", json={"rating": "up"}).status_code == 204
    r = client.post(f"/v1/messages/{message}/feedback",
                    json={"rating": "down", "category": "wrong_chart", "comment": "a line chart would be clearer"})
    assert r.status_code == 204
    with app_tables.connect() as conn:
        assert conn.execute(text("SELECT rating, category FROM app.feedback")).all() == [(-1, "wrong_chart")]
    r = client.post(f"/v1/messages/{message}/feedback", json={"rating": "down", "category": "rude"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"
    assert client.post(f"/v1/messages/{uuid.uuid4()}/feedback", json={"rating": "up"}).status_code == 404


def test_explanations_are_worked_out_once(make, llm, app_tables):
    client = make(quota=Quota(Counters(app_tables), per_session_hour=2, per_ip_day=0))
    message = finished(client)
    first = client.post(f"/v1/messages/{message}/explain").json()
    calls = len(llm.calls)
    assert first == {"steps": ["Add up the sales of completed orders.", "Split them by region."]}
    assert client.post(f"/v1/messages/{message}/explain").json() == first and len(llm.calls) == calls
    assert ask(client).status_code == 429  # the one explanation and one question used the quota of two


def test_answers_without_a_query_have_nothing_to_explain(make):
    client = make(analyst_llm=FakeLLM(intent="out_of_scope"))
    message = ask(client, "What's the weather?", stream=False).json()["id"]
    r = client.post(f"/v1/messages/{message}/explain")
    assert r.status_code == 422 and r.json()["code"] == "nothing_to_explain"


# --- voice and config --------------------------------------------------------------------------------------

def test_voice_questions_are_transcribed(make):
    whisper = FakeTranscriber()
    client = make(transcriber=whisper)
    r = client.post("/v1/transcribe", files={"audio": ("q.webm", b"\x1a\x45" * 100, "audio/webm;codecs=opus")},
                    data={"language": "en"})
    assert r.json() == {"text": "Revenue by region"} and whisper.seen == [(200, "audio/webm", "en")]


@pytest.mark.parametrize(("transcriber", "upload", "status", "code"), [
    (None, ("q.webm", b"x", "audio/webm"), 503, "transcription_unavailable"),
    (FakeTranscriber(), ("q.txt", b"x", "text/plain"), 415, "unsupported_audio"),
    (FakeTranscriber(), ("q.webm", b"x" * 2_000_001, "audio/webm"), 413, "audio_too_large"),
    (FakeTranscriber(fail=True), ("q.webm", b"x", "audio/webm"), 502, "transcription_failed"),
])
def test_voice_problems(make, transcriber, upload, status, code):
    r = make(transcriber=transcriber).post("/v1/transcribe", files={"audio": upload})
    assert r.status_code == status and r.json()["code"] == code


def test_config_lists_starters_and_features(make):
    body = make(transcriber=FakeTranscriber()).get("/v1/config").json()
    assert body["turnstile_site_key"] is None and 5 <= len(body["starters"]) <= 10
    assert body["features"] == {"voice": True, "explain": True, "feedback": True, "sign_in": False,
                                "uploads": True, "reports": False, "api_keys": False}
    assert body["upload_max_mb"] == 5
    assert make().get("/v1/config").json()["features"]["voice"] is False


def test_the_transcriber_sends_the_audio_to_whisper():
    seen = {}

    def post(url, headers, data, files, timeout):
        seen.update(url=url, data=data, file=files["file"])
        return httpx.Response(200, json={"text": " Revenue by region "}, request=httpx.Request("POST", url))

    assert Transcriber("key", WHISPER, post=post).transcribe(b"abc", "audio/x-m4a", "hi") == \
        "Revenue by region"
    assert seen["file"] == ("question.m4a", b"abc", "audio/x-m4a")
    assert seen["data"] == {"model": WHISPER, "response_format": "json", "temperature": "0",
                            "language": "hi"}

    def fails(url, headers, data, files, timeout):
        return httpx.Response(400, json={"error": "bad"}, request=httpx.Request("POST", url))

    with pytest.raises(TranscriptionFailed):
        Transcriber("key", "m", post=fails).transcribe(b"abc", "audio/webm")
