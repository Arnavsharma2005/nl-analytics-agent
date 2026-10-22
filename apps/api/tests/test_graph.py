from app.agents.chart import ChartSpec
from app.analyst import Analyst
from app.brief import database

from .conftest import FakeLLM

REVENUE_BY_REGION = """
SELECT r.name AS region, ROUND(SUM(oi.quantity * oi.unit_price)::numeric, 2) AS revenue
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
JOIN regions r ON r.region_id = c.region_id
JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.status = 'completed' GROUP BY r.name ORDER BY revenue DESC
"""


def run(llm, db, question="Revenue by region"):
    return Analyst(llm, db).ask(question)


def test_happy_path(db):
    llm = FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="Revenue"))
    out = run(llm, db)
    assert out.error is None
    assert out.columns == ["region", "revenue"]
    assert len(out.rows) == 6
    assert out.chart.type == "bar" and out.chart_source == "llm"
    assert out.summary == "Summary."
    assert out.attempts == 1
    assert set(out.timings) >= {"router", "sql_agent", "validate", "execute", "chart_agent", "summary_agent"}


def test_repairs_bad_sql_using_the_error(db):
    llm = FakeLLM(sql=["SELECT region_name, SUM(total) FROM orders GROUP BY 1", REVENUE_BY_REGION])
    out = run(llm, db)
    assert out.error is None
    assert out.attempts == 2
    assert "does not exist" in out.failures[0].error
    # The second SQL-agent call is shown the failed query and the database's error.
    retry_prompt = [user for name, user in llm.calls if name == "SQLDraft"][1]
    assert "region_name" in retry_prompt and "does not exist" in retry_prompt


def test_runtime_errors_are_retried(db):
    llm = FakeLLM(sql=["SELECT 1 / (COUNT(*) - COUNT(*)) AS x FROM orders", REVENUE_BY_REGION])
    out = run(llm, db)
    assert out.failures[0].stage == "execute"
    assert out.error is None and out.attempts == 2


def test_gives_up_after_max_attempts(db):
    llm = FakeLLM(sql=["DELETE FROM orders"])
    out = run(llm, db, "delete all orders please")
    assert out.attempts == 3
    assert out.error.startswith("I couldn't build a working query")
    assert out.rows == []


def test_out_of_scope_skips_sql(db):
    llm = FakeLLM(intent="out_of_scope")
    out = run(llm, db, "What's the weather in Bengaluru?")
    assert out.answer == database().out_of_scope
    assert out.sql is None
    assert [name for name, _ in llm.calls] == ["RouteDecision"]


def test_schema_question(db):
    out = run(FakeLLM(intent="schema_question"), db, "What data do you have?")
    assert out.answer == "We have sales data."


def test_empty_result_gets_a_table(db):
    llm = FakeLLM(sql=["SELECT order_id FROM orders WHERE order_date > '2030-01-01'"])
    out = run(llm, db)
    assert out.rows == [] and out.chart.type == "table"


def test_summary_failure_still_returns_the_chart(db):
    from app.llm import AllProvidersFailed

    class NoSummary(FakeLLM):
        def text(self, system, user, tier="strong"):
            raise AllProvidersFailed([{"provider": "x", "error": "rate limited"}])

    out = run(NoSummary(sql=[REVENUE_BY_REGION]), db)
    assert out.error is None and out.summary is None and len(out.rows) == 6


def test_light_steps_use_the_fast_tier(db):
    llm = FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="Revenue"))
    run(llm, db)
    assert llm.tiers == [("RouteDecision", "fast"), ("SQLDraft", "strong"), ("ChartSpec", "fast"), ("text", "strong")]


def collect(analyst, question="Revenue by region"):
    import asyncio

    async def run_stream():
        return [e async for e in analyst.stream(question)]

    return asyncio.run(run_stream())


def test_stream_sends_the_table_chart_and_summary_as_they_are_ready(db):
    from app.analyst import Chart, Done, Step, Table, Token

    llm = FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t"),
                  summary="North leads.")
    events = collect(Analyst(llm, db))
    kinds = [type(e).__name__ for e in events]
    assert events[kinds.index("Table") - 1] == Step("execute", "Running the query")  # straight after the query
    table = next(e for e in events if isinstance(e, Table))
    assert table.columns == ["region", "revenue"] and len(table.rows) == 6
    assert next(e for e in events if isinstance(e, Chart)).spec.type == "bar"
    tokens = [e for e in events if isinstance(e, Token)]
    assert "".join(t.text for t in tokens) == "North leads." and not any(t.replace for t in tokens)
    assert kinds.index("Table") < kinds.index("Chart") < kinds.index("Token") < kinds.index("Done")
    assert isinstance(events[-1], Done) and events[-1].answer.summary == "North leads."
    assert [e.node for e in events if isinstance(e, Step)][-1] == "summary_agent"


def test_a_summary_stream_that_breaks_is_written_again_whole(db):
    from app.analyst import Token
    from app.llm import AllProvidersFailed

    class Breaks(FakeLLM):
        def stream_text(self, system, user, tier="strong"):
            yield "North le"
            raise AllProvidersFailed([{"provider": "x", "error": "unavailable"}])

    events = collect(Analyst(Breaks(sql=[REVENUE_BY_REGION], summary="North leads."), db))
    tokens = [e for e in events if isinstance(e, Token)]
    assert tokens == [Token("North le"), Token("North leads.", replace=True)]
    assert events[-1].answer.summary == "North leads."
