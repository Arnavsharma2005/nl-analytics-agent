"""The Curator: answers rated thumbs up are reviewed by a curator, and approved ones exported to the library as a PR."""

import base64
import json
import uuid
from datetime import date

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import semantic
from app.auth import Profile, Users
from app.config import get_settings
from app.curation import AlreadyVerified, CuratedV1, Curation, ExportFailed, LibraryPR, add_to_library, library_entries
from app.main import create_app
from app.messages import Messages
from app.sessions import Sessions

LIBRARY = semantic.load()
QUERIES_YML = (semantic.SEMANTIC_DIR / "queries.yml").read_text(encoding="utf-8")
SQL = "SELECT r.name AS region, COUNT(*) AS customers\nFROM customers c JOIN regions r USING (region_id)\nGROUP BY r.name"


def rate(engine, question="Which region signed up the most customers last year?", rating=1, comment=None, dataset=None,
         **answer) -> uuid.UUID:
    """A message with an answer, and a rating of it."""
    body = {"question": question, "intent": "data_query", "sql": SQL, "summary": "North has the most.", **answer}
    id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO app.messages (id, conversation_id, owner, question, answer, dataset_id)
            VALUES (:id, :conversation, 'someone', :question, CAST(:answer AS jsonb), :dataset)
        """), {"id": id, "conversation": uuid.uuid4(), "question": question, "dataset": dataset,
               "answer": json.dumps({"answer": body, "suggestions": []})})
        conn.execute(text("INSERT INTO app.feedback (message_id, rating, comment) VALUES (:id, :rating, :comment)"),
                     {"id": id, "rating": rating, "comment": comment})
    return id


@pytest.fixture
def curation(app_tables):
    return Curation(app_tables, LIBRARY)


# --- the queue -------------------------------------------------------------------------------------------------

def test_an_answer_rated_thumbs_up_waits_for_review(curation, app_tables):
    id = rate(app_tables, "and by region?", comment="spot on", standalone_question="How many customers per region?")
    [candidate] = curation.queue()
    assert candidate.message_id == id and candidate.question == "How many customers per region?"
    assert candidate.asked == "and by region?" and candidate.sql == SQL and candidate.comment == "spot on"


def test_only_working_answers_about_the_demo_data_that_wrote_new_sql_are_queued(curation, app_tables):
    rate(app_tables, "Rated down", rating=-1)
    rate(app_tables, "Failed", error="no working query")
    rate(app_tables, "About an upload", dataset=uuid.uuid4())
    rate(app_tables, "Used a verified query", verified_question=LIBRARY.queries[0].question)
    rate(app_tables, "Out of scope", intent="out_of_scope", sql=None)
    rate(app_tables, LIBRARY.queries[0].question.upper())  # in the library already
    rate(app_tables, "Asked twice")
    rate(app_tables, "asked TWICE")
    assert [c.question for c in curation.queue()] == ["asked TWICE"]


def test_a_reviewed_answer_leaves_the_queue_and_an_approval_is_kept_for_export(curation, app_tables):
    approved, rejected = rate(app_tables, "Customers by region"), rate(app_tables, "Something wrong")
    done = curation.review(approved, "approved", "arnav", "How many customers are there in each region?")
    assert done.status == "approved" and done.question == "How many customers are there in each region?"
    assert done.reviewer == "arnav" and done.pr_url is None
    assert curation.review(rejected, "rejected", "arnav").status == "rejected"
    assert curation.queue() == []
    assert [c.message_id for c in curation.approved()] == [approved]
    assert curation.review(approved, "rejected", "arnav") is None  # reviewed already
    assert curation.review(uuid.uuid4(), "approved", "arnav") is None


def test_an_approval_survives_the_message_expiring(curation, app_tables):
    id = rate(app_tables)
    curation.review(id, "approved", "arnav")
    with app_tables.begin() as conn:
        conn.execute(text("DELETE FROM app.messages"))
    assert [c.message_id for c in curation.approved()] == [id]


def test_a_question_cannot_be_approved_twice_or_over_the_library(curation, app_tables):
    curation.review(rate(app_tables, "Customers by region"), "approved", "arnav")
    with pytest.raises(AlreadyVerified):
        curation.review(rate(app_tables, "Other"), "approved", "arnav", "customers BY region")
    with pytest.raises(AlreadyVerified):
        curation.review(rate(app_tables, "Another"), "approved", "arnav", LIBRARY.queries[0].question)


def test_a_claim_is_released_after_a_failed_export_and_kept_after_one_that_worked(curation, app_tables):
    id = rate(app_tables)
    curation.review(id, "approved", "arnav")
    claim, taken = curation.claim()
    assert [c.message_id for c in taken] == [id] and curation.approved() == []
    assert curation.claim()[1] == []  # a second export at the same time gets nothing
    curation.release(claim)
    claim, _ = curation.claim()
    curation.exported(claim, "https://github.com/o/r/pull/7")
    assert curation.approved() == [] and curation.claim()[1] == []


# --- the library file ------------------------------------------------------------------------------------------

def curated(question="How many customers are there in each region?", sql=SQL, reviewer="arnav") -> CuratedV1:
    return CuratedV1(message_id=uuid.uuid4(), status="approved", question=question, sql=sql, reviewer=reviewer,
                     reviewed_at="2026-10-22T10:00:00Z", pr_url=None)


@pytest.mark.parametrize("question", [
    "How many customers are there in each region?",
    "What's revenue: by region?",
    "Revenue #1 product",
    "- top 5 products",
    "'quoted' question",
    "Umsatz für März?",
    "true",
])
def test_approved_answers_are_added_to_the_library_as_written(question):
    entries = library_entries([curated(question, "SELECT 1\n\n  FROM orders   \n")], date(2026, 10, 23))
    updated = yaml.safe_load(add_to_library(QUERIES_YML, entries))["queries"]
    assert len(updated) == len(LIBRARY.queries) + 1
    added = semantic.VerifiedQuery.model_validate(updated[-1])
    assert (added.question, added.sql) == (question, "SELECT 1\n\n  FROM orders")
    assert (added.verified_by, added.verified_at, added.starter) == ("arnav", date(2026, 10, 23), False)


def test_the_library_keeps_its_comments_and_refuses_a_question_it_has():
    updated = add_to_library(QUERIES_YML, library_entries([curated()], date(2026, 10, 23)))
    assert updated.startswith(QUERIES_YML.rstrip("\n")) and "# approved in the Curator" in updated
    with pytest.raises(ValueError):
        add_to_library(QUERIES_YML, library_entries([curated(LIBRARY.queries[3].question)], date(2026, 10, 23)))


# --- the pull request ------------------------------------------------------------------------------------------

class FakeRepo:
    """The GitHub endpoints LibraryPR uses, on one repository."""

    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.file = QUERIES_YML
        self.fail_on = fail_on

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        assert request.headers["authorization"] == "Bearer ghp_test"
        if self.fail_on and self.fail_on in path:
            return httpx.Response(422, json={"message": "Reference already exists"})
        if path == "/repos/o/r/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "base-sha"}})
        if path == "/repos/o/r/contents/apps/api/semantic/queries.yml" and request.method == "GET":
            return httpx.Response(200, json={"sha": "file-sha", "content": base64.b64encode(self.file.encode()).decode()})
        if path == "/repos/o/r/git/refs":
            return httpx.Response(201, json={"ref": body["ref"]})
        if path == "/repos/o/r/contents/apps/api/semantic/queries.yml" and request.method == "PUT":
            self.file = base64.b64decode(body["content"]).decode()
            return httpx.Response(200, json={"commit": {"sha": "new"}})
        if path == "/repos/o/r/pulls":
            return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/9"})
        return httpx.Response(404, json={"message": "Not Found"})


def test_a_pull_request_adds_the_answers_on_a_new_branch():
    repo = FakeRepo()
    url = LibraryPR("ghp_test", "o/r", transport=httpx.MockTransport(repo.handle)).open([curated()], date(2026, 10, 23))
    assert url == "https://github.com/o/r/pull/9"
    ref = next(body for method, path, body in repo.calls if path.endswith("/git/refs"))
    put = next(body for method, path, body in repo.calls if method == "PUT")
    pr = next(body for method, path, body in repo.calls if path.endswith("/pulls"))
    assert ref["sha"] == "base-sha" and ref["ref"] == f"refs/heads/{put['branch']}"
    assert put["branch"].startswith("curator/2026-10-23-") and put["sha"] == "file-sha"
    assert pr["head"] == put["branch"] and pr["base"] == "main" and pr["draft"] is True
    assert "How many customers are there in each region? (approved by arnav)" in pr["body"]
    assert yaml.safe_load(repo.file)["queries"][-1]["question"] == "How many customers are there in each region?"


def test_a_pull_request_github_refuses_says_why():
    repo = FakeRepo(fail_on="/git/refs")
    with pytest.raises(ExportFailed, match="422"):
        LibraryPR("ghp_test", "o/r", transport=httpx.MockTransport(repo.handle)).open([curated()], date(2026, 10, 23))
    assert not any(method == "PUT" for method, _, _ in repo.calls)


def test_a_library_that_already_has_the_question_opens_no_pull_request():
    repo = FakeRepo()
    with pytest.raises(ExportFailed, match="doesn't take"):
        LibraryPR("ghp_test", "o/r", transport=httpx.MockTransport(repo.handle)).open(
            [curated(LIBRARY.queries[0].question)], date(2026, 10, 23))
    assert [method for method, _, _ in repo.calls] == ["GET", "GET"]


# --- the API ---------------------------------------------------------------------------------------------------

@pytest.fixture
def make(db, app_tables, monkeypatch):
    monkeypatch.setenv("CURATORS", '["Arnav"]')
    get_settings.cache_clear()
    sessions, users, messages = Sessions("secret", 3600), Users(app_tables), Messages(app_tables)
    curation = Curation(app_tables, LIBRARY)

    def build(login: str | None = "arnav", repo: FakeRepo | None = None):
        pr = LibraryPR("ghp_test", "o/r", transport=httpx.MockTransport(repo.handle)) if repo else None
        app = create_app(db_factory=lambda: db, sessions_factory=lambda: sessions, quota_factory=lambda: None,
                         messages_factory=lambda: messages, users_factory=lambda: users, github_factory=lambda: None,
                         transcriber_factory=lambda: None, curation_factory=lambda: curation,
                         library_pr_factory=lambda: pr)
        client = TestClient(app)
        client.post("/v1/session", json={})
        if login:
            user = users.sign_in(Profile(github_id=sum(map(ord, login)), login=login, name=None, avatar_url=None))
            client.cookies.set("aa_user", sessions.signer("user").seal(str(user.id), 3600))
        return client

    yield build
    get_settings.cache_clear()


def test_a_curator_reviews_the_queue_and_copies_the_approvals(make, app_tables):
    client = make()
    assert client.get("/v1/me").json()["user"]["curator"] is True
    id, other = rate(app_tables, "Customers by region"), rate(app_tables, "Something wrong")
    overview = client.get("/v1/curator").json()
    assert {c["message_id"] for c in overview["queue"]} == {str(id), str(other)}
    assert overview["approved"] == [] and overview["can_open_pr"] is False

    r = client.post(f"/v1/curator/{id}", json={"decision": "approve", "question": "How many customers per region?"})
    assert r.status_code == 200 and r.json()["reviewer"] == "arnav"
    assert client.post(f"/v1/curator/{other}", json={"decision": "reject"}).json()["status"] == "rejected"
    assert client.post(f"/v1/curator/{other}", json={"decision": "approve"}).json()["code"] == "candidate_not_found"
    overview = client.get("/v1/curator").json()
    assert overview["queue"] == [] and [c["question"] for c in overview["approved"]] == ["How many customers per region?"]

    copied = client.get("/v1/curator/export.yml")
    assert copied.headers["content-type"].startswith("text/plain")
    assert yaml.safe_load("queries:\n" + copied.text)["queries"][0]["question"] == "How many customers per region?"
    assert client.post("/v1/curator/export").json()["code"] == "export_off"


def test_approving_a_question_the_library_has_is_refused(make, app_tables):
    client = make()
    id = rate(app_tables)
    r = client.post(f"/v1/curator/{id}", json={"decision": "approve", "question": LIBRARY.queries[0].question})
    assert r.status_code == 409 and r.json()["code"] == "already_verified"


def test_exporting_opens_a_pull_request_once(make, app_tables):
    repo = FakeRepo()
    client = make(repo=repo)
    assert client.post("/v1/curator/export").json()["code"] == "nothing_to_export"
    client.post(f"/v1/curator/{rate(app_tables)}", json={"decision": "approve"})
    r = client.post("/v1/curator/export")
    assert r.status_code == 200 and r.json() == {"url": "https://github.com/o/r/pull/9", "count": 1}
    assert client.get("/v1/curator").json()["approved"] == []
    assert client.post("/v1/curator/export").json()["code"] == "nothing_to_export"


def test_a_failed_export_keeps_the_approvals_for_the_next_try(make, app_tables):
    client = make(repo=FakeRepo(fail_on="/pulls"))
    client.post(f"/v1/curator/{rate(app_tables)}", json={"decision": "approve"})
    r = client.post("/v1/curator/export")
    assert r.status_code == 502 and r.json()["code"] == "export_failed"
    assert len(client.get("/v1/curator").json()["approved"]) == 1


@pytest.mark.parametrize("login", [None, "someone-else"])
def test_only_curators_can_use_the_curator(make, app_tables, login):
    client = make(login)
    id = rate(app_tables)
    me = client.get("/v1/me").json()["user"]
    assert me is None or me["curator"] is False
    for r in (client.get("/v1/curator"), client.get("/v1/curator/export.yml"), client.post("/v1/curator/export"),
              client.post(f"/v1/curator/{id}", json={"decision": "approve"})):
        assert r.status_code == 403 and r.json()["code"] == "not_a_curator"
    with app_tables.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.curation")).scalar() == 0
