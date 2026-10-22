"""Context for the SQL agent: similar verified questions, the values a question names, and the verified fast path."""

import copy
from pathlib import Path

import pytest
import yaml

from app import semantic
from app.analyst import Analyst
from app.context import Context, LexicalEmbedder, OpenAIEmbedder
from app.context.embedders import cosine
from app.context.values import ValueIndex
from app.db import Database

from .conftest import TEST_DB, FakeLLM
from .test_graph import REVENUE_BY_REGION

LIBRARY = semantic.load()
QUESTIONS = yaml.safe_load((Path(__file__).resolve().parents[3] / "eval" / "questions.yaml").read_text())


@pytest.fixture(scope="module")
def source(db):
    return Database(TEST_DB, personal_columns=["customers.email"])


@pytest.fixture(scope="module")
def context(source):
    return Context(LIBRARY, source.schema(), fast_path=True)


# --- values ------------------------------------------------------------------------------------------------

def test_values_named_in_a_question_are_found_despite_spelling(source):
    index = ValueIndex(source.schema())
    assert [h.value for h in index.find("How many Pro Laptops did we sell?")] == ["Pro Laptop"]
    assert [h.value for h in index.find("revenue from the pro-laptop")] == ["Pro Laptop"]
    # 'Max Smartwatch' shares a word with the product named, but is a much weaker match.
    assert [h.value for h in index.find("How much did the Ultra Smartwatch earn?")] == ["Ultra Smartwatch"]
    assert index.find("Top customers by revenue") == []


def test_personal_and_listed_values_are_not_indexed(source):
    schema = source.schema()
    email = source.run("SELECT email FROM customers LIMIT 1").rows[0][0]
    index = ValueIndex(schema)
    assert not any(h.column == "email" for h in index.find(f"orders from {email}"))
    # Region names are listed in the prompt already, so they are not looked up again.
    assert not any(h.table == "regions" for h in index.find("Revenue in the North region"))


# --- embedders ---------------------------------------------------------------------------------------------

def test_lexical_similarity_follows_shared_words():
    a, b, c = LexicalEmbedder().embed(["Monthly revenue in 2025", "Show me monthly revenue for 2025",
                                       "How many customers signed up?"])
    assert cosine(a, a) == pytest.approx(1.0)
    assert cosine(a, b) == pytest.approx(1.0)  # only stop words differ
    assert cosine(a, c) < 0.1


class Reply:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_embedding_api_answers_are_normalised_and_kept_in_order():
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, auth=headers["Authorization"], body=json)
        return Reply({"data": [{"index": 1, "embedding": [0.0, 2.0]}, {"index": 0, "embedding": [3.0, 4.0]}]})

    embedder = OpenAIEmbedder("https://example.test/v1/", "key", "@cf/baai/bge-m3", post=post)
    assert embedder.embed(["a", "b"]) == [[0.6, 0.8], [0.0, 1.0]]
    assert seen == {"url": "https://example.test/v1/embeddings", "auth": "Bearer key",
                    "body": {"model": "@cf/baai/bge-m3", "input": ["a", "b"]}}
    with pytest.raises(ValueError):
        embedder.embed(["just one"])


class Broken:
    name = "broken"
    related = 0.5
    same_question = 0.9

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        raise ConnectionError("unreachable")


def test_a_failing_embedder_falls_back_to_shared_words(source):
    broken = Broken()
    pack = Context(LIBRARY, source.schema(), broken, fast_path=True).pack("Show me the monthly revenue for 2025")
    assert broken.calls == 1 and pack.embedder == "lexical"
    assert pack.verified.question == "What was our monthly revenue in 2025?"


# --- examples and the fast path ----------------------------------------------------------------------------

def test_similar_verified_queries_become_examples(context):
    pack = context.pack("Revenue by product category in 2025")
    assert pack.verified is None  # a different year: an example, not the answer
    assert pack.examples[0].question == "Revenue by product category in 2024"
    assert len(pack.examples) <= 3 and [e.similarity for e in pack.examples] == sorted(
        (e.similarity for e in pack.examples), reverse=True)
    text = pack.prompt_text()
    assert text.startswith("Verified queries for similar questions") and pack.examples[0].sql in text


def test_values_are_listed_in_the_prompt(context):
    text = context.pack("How many Pro Laptops did we sell in 2025?").prompt_text()
    assert "- products.name = 'Pro Laptop'" in text


@pytest.mark.parametrize(("asked", "verified"), [
    ("Show me the monthly revenue for 2025", "What was our monthly revenue in 2025?"),
    ("What were the top ten products by revenue in 2025", "Top 10 products by revenue in 2025"),
    ("return rate per product category", "What is the return rate for each product category?"),
    ("How many customers do we have per segment", "How many customers do we have in each segment?"),
])
def test_rewordings_of_a_verified_question_use_its_query(context, asked, verified):
    assert context.pack(asked).verified.question == verified


@pytest.mark.parametrize("asked", [
    "Which 3 customers placed the most orders in 2024?",  # another year
    "Which 3 customers placed the fewest orders in 2025?",  # the other direction
    "Which 5 customers placed the most orders in 2025?",  # another number
    "What is the return rate for each region?",  # another breakdown
    "Revenue by product category in 2024 for the North region",  # an extra filter
    "How many product categories are there?",  # a count, not a count per category
    "What was revenue this quarter?",  # another period
])
def test_a_detail_that_changes_the_answer_rules_the_fast_path_out(context, asked):
    assert context.pack(asked).verified is None


def test_the_fast_path_never_answers_a_held_out_question(context):
    # A verified query never returns a held-out answer (test_semantic), so answering one with it would be wrong.
    answered = {q["id"]: context.pack(q["question"]).verified for q in QUESTIONS if q["split"] == "test"}
    assert {i: v.question for i, v in answered.items() if v} == {}


def test_every_verified_question_finds_its_own_query(context):
    assert all(context.pack(q.question).verified == q for q in LIBRARY.queries)


def test_without_the_fast_path_nothing_is_answered_outright(source):
    assert Context(LIBRARY, source.schema()).pack("What was our monthly revenue in 2025?").verified is None


# --- in the pipeline ---------------------------------------------------------------------------------------

def test_the_sql_agent_sees_the_context_and_states_its_readings(db, context):
    llm = FakeLLM(sql=[REVENUE_BY_REGION], assumptions=["2025 means calendar 2025"])
    out = Analyst(llm, db, context=context).ask("Revenue by product category in 2025")
    [(name, user)] = [c for c in llm.calls if c[0].endswith("SQLDraft")]
    assert name == "ContextualSQLDraft" and "Q: Revenue by product category in 2024" in user
    assert "state it in assumptions" in dict(llm.systems)["ContextualSQLDraft"]
    assert out.assumptions == ["2025 means calendar 2025"] and out.verified_question is None
    assert "context" in out.timings


def test_without_context_the_sql_prompt_is_unchanged(db):
    llm = FakeLLM(sql=[REVENUE_BY_REGION])
    out = Analyst(llm, db).ask("Revenue by product category in 2025")
    assert [n for n, _ in llm.calls if n.endswith("SQLDraft")] == ["SQLDraft"]
    assert "assumptions" not in dict(llm.systems)["SQLDraft"] and out.assumptions == []


def test_a_verified_question_skips_the_sql_agent(db, context):
    llm = FakeLLM(sql=["SELECT 1 AS wrong"])
    out = Analyst(llm, db, context=context).ask("Show me the monthly revenue for 2025")
    verified = next(q for q in LIBRARY.queries if q.question == "What was our monthly revenue in 2025?")
    assert not any(n.endswith("SQLDraft") for n, _ in llm.calls)
    assert out.verified_question == verified.question and out.sql == verified.sql and out.attempts == 0
    assert len(out.rows) == 12 and out.error is None and out.summary == "Summary."


def test_a_broken_verified_query_falls_back_to_the_sql_agent(db, source):
    broken = copy.deepcopy(LIBRARY.model_dump())
    for q in broken["queries"]:
        if q["question"] == "What was our monthly revenue in 2025?":
            q["sql"] = "SELECT no_such_column FROM orders"
    context = Context(semantic.Semantic(**broken), source.schema(), fast_path=True)
    llm = FakeLLM(sql=[REVENUE_BY_REGION])
    out = Analyst(llm, db, context=context).ask("What was our monthly revenue in 2025?")
    assert out.error is None and out.verified_question is None and out.attempts == 1
    assert out.failures[0].sql == "SELECT no_such_column FROM orders"
