"""Errors to Sentry and answer traces to Langfuse (app/observability.py), each off until its keys are set."""

import pytest
import sentry_sdk
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableLambda
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.analyst import Analyst
from app.config import Settings
from app.llm import FallbackLLM, Provider
from app.observability import Tracing, init_sentry

from .conftest import FakeLLM

KEYS = {"langfuse_public_key": "pk-lf-test", "langfuse_secret_key": "sk-lf-test", "langfuse_host": "http://127.0.0.1:9"}


def test_without_keys_nothing_is_set_up():
    s = Settings(_env_file=None)
    assert not init_sentry(s)
    tracing = Tracing(s)
    assert not tracing.on and tracing.callbacks() is None
    tracing.flush()


@pytest.fixture(scope="module")
def tracing_and_exporter():
    # Langfuse keeps one client per public key in a process, so the tests share one.
    exporter = InMemorySpanExporter()
    return Tracing(Settings(_env_file=None, app_env="test", **KEYS), span_exporter=exporter), exporter


@pytest.fixture
def traced(tracing_and_exporter):
    yield tracing_and_exporter
    tracing_and_exporter[1].clear()


def test_an_answer_is_traced_step_by_step(traced, db):
    tracing, exporter = traced
    answer = Analyst(FakeLLM(), db, callbacks=tracing.callbacks()).ask("How many orders are there?")
    assert answer.rows and not answer.error
    tracing.flush()
    names = [span.name for span in exporter.get_finished_spans()]
    assert "answer" in names
    assert {"router", "sql_agent", "validate", "execute", "summary_agent"} <= set(names), names


def test_a_model_call_inside_a_step_is_traced_with_its_prompt(traced):
    tracing, exporter = traced
    llm = FallbackLLM([Provider("groq", "Groq", "https://x", "m", "k")],
                      client_factory=lambda p, t: FakeListChatModel(responses=["42"]))
    step = RunnableLambda(lambda question: llm.text("Answer with a number.", question), name="step")
    assert step.invoke("How many?", {"callbacks": tracing.callbacks()}) == "42"
    tracing.flush()
    [model] = [span for span in exporter.get_finished_spans() if span.name == "FakeListChatModel"]
    assert "Answer with a number." in str(model.attributes) and "42" in str(model.attributes)


def test_sentry_reports_without_personal_data():
    try:
        assert init_sentry(Settings(_env_file=None, sentry_dsn="https://key@o0.ingest.sentry.io/0", app_env="test",
                                    git_commit="abc123"))
        options = sentry_sdk.get_client().options
        assert options["environment"] == "test" and options["release"] == "abc123"
        assert not options["send_default_pii"] and options["max_request_body_size"] == "never"
    finally:
        sentry_sdk.init()  # off again for the other tests
