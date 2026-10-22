"""Uploaded CSV files: reading and checking them, finding personal data, keeping them for a day, and answering
questions about them in a locked-down DuckDB."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agents.chart import ChartSpec
from app.analyst import Analyst
from app.config import get_settings
from app.datasets.csv_upload import Limits, UploadRejected, identifier, read_csv
from app.datasets.duck import DuckDBSource
from app.datasets.profile import personal_reason
from app.datasets.registry import DatasetAnalysts
from app.datasets.store import Datasets
from app.db import QueryError
from app.main import create_app
from app.messages import Messages
from app.sessions import Sessions

from .conftest import FakeLLM

SALES = b"""Order ID,Order Date,Customer,Contact,City,Category,Amount (INR),Paid?
1,2025-01-03,Asha Rao,asha@example.com,Bengaluru,Books,1250.50,true
2,2025-01-05,Ravi K,ravi@example.org,Mumbai,Toys,830,false
3,2025-02-11,Meera S,meera@example.in,Bengaluru,Books,99.90,true
4,2025-02-20,Kiran P,kiran@example.com,Delhi,Games,410,true
5,2025-03-02,Asha Rao,asha@example.com,Bengaluru,Toys,2200,false
"""

BY_CITY = "SELECT city, SUM(amount_inr) AS total FROM sales GROUP BY city ORDER BY total DESC"


# --- reading a file --------------------------------------------------------------------------------------------

def test_a_csv_becomes_a_table_with_clean_names_and_a_profile():
    upload = read_csv(SALES, "Sales.csv")
    assert (upload.name, upload.table, upload.rows) == ("Sales.csv", "sales", 5)
    columns = {c.name: c for c in upload.columns}
    assert list(columns) == ["order_id", "order_date", "customer", "contact", "city", "category", "amount_inr", "paid"]
    assert columns["amount_inr"].header == "Amount (INR)" and columns["amount_inr"].kind == "number"
    assert (columns["amount_inr"].low, columns["amount_inr"].high) == (99.9, 2200.0)
    assert columns["order_date"].kind == "date" and columns["order_date"].low == "2025-01-03"
    assert columns["paid"].kind == "yes/no"
    assert columns["city"].examples[0] == "Bengaluru" and columns["city"].distinct == 3
    # the email addresses are found by their values, whatever the column is called
    assert columns["contact"].personal == "email address" and columns["contact"].examples == []
    assert upload.suggestions[0] == "Total amount inr by city"
    assert upload.parquet.startswith(b"PAR1")


@pytest.mark.parametrize(("name", "values", "reason"), [
    ("mobile_no", [], "phone number"),
    ("dob", [], "date of birth"),
    ("pan_number", [], "government ID"),
    ("notes", ["9876543210", "+91 91234 56789", "8123456789"], "phone number"),
    ("ref", ["1234 5678 9012", "234567890123", "3456 7890 1234"], "government ID"),
    ("ref", ["ABCDE1234F", "PQRSX9876Z"], "government ID"),
    ("ref", ["4111 1111 1111 1111", "5500-0000-0000-0004"], "card number"),
    ("amount", ["120", "4500", "75"], None),
    ("customer_name", ["Asha Rao", "Ravi K"], None),  # names stay usable, as in the demo
])
def test_personal_data_is_found_by_name_or_by_value(name, values, reason):
    assert personal_reason(name, values) == reason


@pytest.mark.parametrize(("data", "code"), [
    (b"", "empty_file"),
    (b"a,b\n", "empty_file"),
    (b"PK\x03\x04 an xlsx file", "not_csv"),
    (b"a,b\n\x00\x01\x02", "not_csv"),
    (b"a,b\n" + b"1,2\n" * 11, "too_many_rows"),
    (b",".join(f"c{i}".encode() for i in range(5)) + b"\n" + b",".join(b"1" for _ in range(5)), "too_many_columns"),
    (b"x" * 2000, "file_too_large"),
])
def test_files_that_cant_be_used_are_refused_with_a_reason(data, code):
    with pytest.raises(UploadRejected) as refused:
        read_csv(data, "f.csv", Limits(max_bytes=1000, max_rows=10, max_columns=4))
    assert refused.value.code == code and refused.value.detail


def test_names_become_plain_identifiers():
    assert identifier("Order Date (IST)", "x") == "order_date_ist"
    assert identifier("2024 Sales", "x") == "c_2024_sales"
    assert identifier("Order", "x") == "order_"
    assert identifier("₹ ₹", "fallback") == "fallback"
    upload = read_csv(b"City,city,CITY\na,b,c\n", "city.csv")
    assert [c.name for c in upload.columns] == ["city", "city_1", "city_2"]  # DuckDB numbers repeated headings
    assert upload.table == "city_data"  # never the same as a column: a bare table name is its whole row


def test_a_file_that_isnt_utf8_is_still_read():
    upload = read_csv("city,amount\nSão Paulo,10\n".encode("cp1252"), "x.csv")
    assert upload.columns[0].examples == ["São Paulo"]


# --- querying it -----------------------------------------------------------------------------------------------

@pytest.fixture
def source():
    upload = read_csv(SALES, "sales.csv")
    personal = {(upload.table, c.name) for c in upload.columns if c.personal}
    s = DuckDBSource.build({upload.table: upload.parquet}, comments={"sales": {"amount_inr": "heading: Amount"}},
                           personal=personal, statement_timeout_ms=1000, max_rows=3)
    yield s
    s.close()


def test_the_source_shows_only_the_uploaded_table_with_its_notes(source):
    schema = source.schema()
    assert list(schema.tables) == ["sales"]
    described = schema.describe()
    assert "amount_inr double  -- heading: Amount" in described
    assert "contact varchar  -- personal data" in described and "asha@" not in described


def test_queries_are_read_only_bounded_and_stopped_when_slow(source):
    result = source.run(BY_CITY)
    assert result.rows[0] == ["Bengaluru", 3550.4] and not result.truncated
    assert source.run("SELECT * FROM range(10)").truncated
    with pytest.raises(QueryError, match="read-only"):
        source.run("DELETE FROM sales")
    with pytest.raises(QueryError, match="file system operations are disabled"):
        source.run("SELECT * FROM read_csv('/etc/passwd')")
    with pytest.raises(QueryError, match="longer than 1 seconds"):
        source.run("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r")


def test_closing_the_source_removes_its_files(source):
    directory = source._directory
    source.close()
    assert not directory.exists()


def test_an_upload_is_answered_with_a_brief_of_its_own(source):
    llm = FakeLLM(sql=[BY_CITY], chart=ChartSpec(type="bar", x="city", y=["total"], title="t"))
    from app.datasets.registry import brief
    from app.datasets.store import DatasetV1

    dataset = DatasetV1(id=uuid.uuid4(), name="sales.csv", table="sales", rows=5,
                        columns=read_csv(SALES, "sales.csv").columns, suggestions=[], created_at="2026-10-22T00:00:00Z",
                        expires_at="2026-10-23T00:00:00Z")
    out = Analyst(llm, source, brief=brief(dataset)).ask("Which city spends most?")
    assert out.error is None and out.rows[0][0] == "Bengaluru"
    prompts = "\n".join(system for _, system in llm.systems)
    assert "uploaded by the user from a CSV file" in prompts and "DuckDB" in prompts
    assert "e-commerce" not in prompts and "rupees" not in prompts and "completed" not in prompts
    assert "Total amount inr by city" not in prompts


# --- keeping it ------------------------------------------------------------------------------------------------

def test_uploads_are_kept_for_their_owner_and_expire(app_tables):
    store = Datasets(app_tables, ttl_hours=24, per_owner=2)
    mine = store.add("sid-1", read_csv(SALES, "sales.csv"))
    assert store.get(mine.id, "sid-1").columns == mine.columns and store.parquet(mine.id).startswith(b"PAR1")
    assert store.get(mine.id, "sid-2") is None and store.get(mine.id, None) is None
    assert [d.id for d in store.list("sid-1")] == [mine.id] and store.list("sid-2") == []
    store.add("sid-1", read_csv(SALES, "more.csv"))
    with pytest.raises(UploadRejected) as refused:
        store.add("sid-1", read_csv(SALES, "third.csv"))
    assert refused.value.code == "too_many_datasets" and refused.value.status == 409
    assert not store.delete(mine.id, "sid-2") and store.delete(mine.id, "sid-1") and store.get(mine.id, "sid-1") is None
    with app_tables.begin() as conn:
        conn.execute(text("UPDATE app.datasets SET expires_at = now() - interval '1 minute'"))
    assert store.list("sid-1") == []


def test_uploads_together_are_capped(app_tables):
    store = Datasets(app_tables, total_mb=0)
    with pytest.raises(UploadRejected) as refused:
        store.add("sid", read_csv(SALES, "sales.csv"))
    assert refused.value.code == "uploads_full"


def test_an_analyst_is_built_once_per_dataset_and_forgotten_when_gone(app_tables):
    store = Datasets(app_tables)
    dataset = store.add("sid", read_csv(SALES, "sales.csv"))
    analysts = DatasetAnalysts(store, lambda: FakeLLM(sql=[BY_CITY]), keep=1)
    first = analysts.get(dataset.id, "sid")
    assert first and analysts.get(dataset.id, "sid")[1] is first[1]
    assert analysts.get(dataset.id, "someone-else") is None
    store.delete(dataset.id, "sid")
    assert analysts.get(dataset.id, "sid") is None
    analysts.close()


# --- through the API -------------------------------------------------------------------------------------------

@pytest.fixture
def make(db, app_tables):
    llm = FakeLLM(sql=[BY_CITY], chart=ChartSpec(type="bar", x="city", y=["total"], title="t"), summary="Bengaluru.")
    store = Datasets(app_tables)
    analysts = DatasetAnalysts(store, lambda: llm, statement_timeout_ms=2000)
    messages = Messages(app_tables)

    def build(session=True):
        app = create_app(analyst_factory=lambda: Analyst(llm, db), db_factory=lambda: db, llm_factory=lambda: llm,
                         sessions_factory=lambda: Sessions("secret", 3600), quota_factory=lambda: None,
                         messages_factory=lambda: messages, datasets_factory=lambda: store,
                         dataset_analysts_factory=lambda: analysts, github_factory=lambda: None,
                         transcriber_factory=lambda: None)
        client = TestClient(app)
        if session:
            client.post("/v1/session", json={})
        return client

    build.llm = llm  # the model every app built here shares, so a test can script its queries
    yield build
    analysts.close()


def upload(client, data=SALES, name="sales.csv"):
    return client.post("/v1/datasets", files={"file": (name, data, "text/csv")})


def ask(client, dataset_id, question="Which city spends most?", conversation=None, key=None):
    headers = {"Idempotency-Key": str(key or uuid.uuid4())}
    return client.post(f"/v1/conversations/{conversation or uuid.uuid4()}/messages",
                       json={"question": question, "dataset_id": str(dataset_id)}, headers=headers)


def test_upload_then_ask_about_it(make):
    client = make()
    r = upload(client)
    assert r.status_code == 201
    dataset = r.json()
    assert dataset["table"] == "sales" and dataset["rows"] == 5 and len(dataset["columns"]) == 8
    body = ask(client, dataset["id"]).json()
    assert body["dataset_id"] == dataset["id"]
    assert body["answer"]["rows"][0] == ["Bengaluru", 3550.4] and body["answer"]["error"] is None
    assert body["suggestions"] == dataset["suggestions"][:3]
    assert [d["id"] for d in client.get("/v1/datasets").json()] == [dataset["id"]]
    assert client.get("/v1/conversations").json()[0]["dataset_id"] == dataset["id"]


def test_a_dataset_is_only_its_owners(make):
    owner, stranger = make(), make()
    dataset = upload(owner).json()
    r = ask(stranger, dataset["id"])
    assert r.status_code == 404 and r.json()["code"] == "dataset_not_found"
    assert stranger.get(f"/v1/datasets/{dataset['id']}").status_code == 404
    assert stranger.delete(f"/v1/datasets/{dataset['id']}").status_code == 404


def test_a_deleted_dataset_answers_no_more_questions_but_keeps_its_answers(make):
    client = make()
    dataset = upload(client).json()
    key = uuid.uuid4()
    first = ask(client, dataset["id"], key=key)
    assert client.delete(f"/v1/datasets/{dataset['id']}").status_code == 204
    r = ask(client, dataset["id"])
    assert r.status_code == 404 and r.json()["code"] == "dataset_not_found"
    replay = ask(client, dataset["id"], key=key, conversation=first.json()["conversation_id"])
    assert replay.status_code == 200 and replay.json()["answer"] == first.json()["answer"]


def test_uploads_need_a_session_and_a_readable_file(make):
    newcomer = make(session=False)
    r = upload(newcomer)
    assert r.status_code == 403 and r.json()["code"] == "session_required"
    assert newcomer.get("/v1/datasets").json() == []
    r = upload(make(), data=b"PK\x03\x04", name="book.xlsx")
    assert r.status_code == 422 and r.json()["code"] == "not_csv" and "CSV" in r.json()["detail"]


@pytest.mark.parametrize("sql", ["SELECT customer, contact FROM sales", "SELECT s FROM sales s",
                                 "SELECT * FROM sales"])
def test_the_answer_never_shows_the_personal_column(make, sql):
    client = make()
    dataset = upload(client).json()
    make.llm.sql = [sql]
    answer = ask(client, dataset["id"]).json()["answer"]
    assert answer["error"] and not answer["rows"]
    assert "asha@" not in str(answer)


def test_a_follow_up_only_counts_earlier_questions_about_the_same_data(make):
    client = make()
    dataset = upload(client).json()
    conversation = uuid.uuid4()
    demo = client.post(f"/v1/conversations/{conversation}/messages", json={"question": "Revenue by region"},
                       headers={"Idempotency-Key": str(uuid.uuid4())})
    assert demo.status_code == 200
    ask(client, dataset["id"], conversation=conversation)
    assert not [name for name, _ in make.llm.calls if name == "Standalone"]  # asked on its own, not as a follow-up
    ask(client, dataset["id"], "and in Delhi?", conversation=conversation)
    assert [name for name, _ in make.llm.calls if name == "Standalone"] == ["Standalone"]


def test_with_uploads_off_a_dataset_id_is_not_found(make, monkeypatch):
    client = make()
    dataset = upload(client).json()
    monkeypatch.setattr(get_settings(), "uploads", False)
    same_visitor = make(session=False)
    same_visitor.cookies = client.cookies
    assert same_visitor.get("/v1/config").json()["upload_max_mb"] is None
    r = ask(same_visitor, dataset["id"])
    assert r.status_code == 404 and r.json()["code"] == "dataset_not_found"
    assert ask(client, dataset["id"]).status_code == 200  # the owner's own file, with uploads on
