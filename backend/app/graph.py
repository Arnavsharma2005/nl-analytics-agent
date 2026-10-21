"""The LangGraph pipeline: router -> SQL (with validate/repair loop) -> execute -> chart -> summary."""

import operator
import time
from functools import wraps
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.chart import choose_chart
from app.agents.router import answer_schema_question, route
from app.agents.sql_agent import write_sql
from app.agents.summary import summarize
from app.db import Database, QueryError
from app.llm import LLM, AllProvidersFailed
from app.sql_guard import SQLValidationError, validate_sql

OUT_OF_SCOPE_REPLY = (
    "I can only answer questions about the sales data: orders, revenue, products, categories, customers and "
    "regions for 2024-2025. Try something like \"Monthly revenue in 2025\" or \"Top 5 products by profit\"."
)


def _add_timings(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    out = dict(a)
    for k, v in b.items():
        out[k] = round(out.get(k, 0.0) + v, 3)
    return out


class AnalyticsState(TypedDict, total=False):
    question: str
    intent: str
    route_reason: str
    sql: str
    sql_reasoning: str
    sql_ok: bool
    sql_attempts: int
    failures: list[dict[str, str]]  # earlier attempts: {"sql", "error", "stage"}
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    chart: dict[str, Any]
    chart_source: str
    summary: str
    answer: str
    error: str
    timings: Annotated[dict[str, float], _add_timings]
    llm_calls: Annotated[list[dict[str, Any]], operator.add]  # which provider served each step, incl. failovers


def _timed(name: str, llm: LLM | None = None):
    def deco(fn):
        @wraps(fn)
        def wrapper(state):
            logs = hasattr(llm, "start_log")
            if logs:
                llm.start_log()
            start = time.perf_counter()
            update = fn(state)
            update["timings"] = {name: time.perf_counter() - start}
            if logs:
                update["llm_calls"] = [{"step": name, **call} for call in llm.take_log()]
            return update

        return wrapper

    return deco


def build_graph(llm: LLM, db: Database, max_attempts: int = 3, rows_for_llm: int = 50):
    schema = db.schema()
    schema_text = schema.describe()
    table_names = sorted(schema.tables)

    def timed(name: str):
        return _timed(name, llm)

    @timed("router")
    def router_node(state: AnalyticsState):
        decision = route(llm, state["question"], table_names)
        return {"intent": decision.intent, "route_reason": decision.reason, "failures": [], "sql_attempts": 0}

    @timed("schema_answer")
    def schema_node(state: AnalyticsState):
        return {"answer": answer_schema_question(llm, state["question"], schema_text)}

    def out_of_scope_node(state: AnalyticsState):
        return {"answer": OUT_OF_SCOPE_REPLY}

    @timed("sql_agent")
    def write_sql_node(state: AnalyticsState):
        previous = [(f["sql"], f["error"]) for f in state.get("failures", [])]
        draft = write_sql(llm, state["question"], schema_text, previous)
        return {"sql": draft.sql, "sql_reasoning": draft.reasoning, "sql_attempts": state.get("sql_attempts", 0) + 1}

    @timed("validate")
    def validate_node(state: AnalyticsState):
        try:
            sql = validate_sql(state["sql"], set(table_names))
            db.explain(sql)
        except (SQLValidationError, QueryError) as e:
            failure = {"sql": state["sql"], "error": str(e), "stage": "validate"}
            return {"sql_ok": False, "failures": state.get("failures", []) + [failure]}
        return {"sql": sql, "sql_ok": True}

    @timed("execute")
    def execute_node(state: AnalyticsState):
        try:
            result = db.run(state["sql"])
        except QueryError as e:
            failure = {"sql": state["sql"], "error": str(e), "stage": "execute"}
            return {"sql_ok": False, "failures": state.get("failures", []) + [failure]}
        return {"columns": result.columns, "rows": result.rows, "truncated": result.truncated}

    @timed("chart_agent")
    def chart_node(state: AnalyticsState):
        spec, source = choose_chart(llm, state["question"], state["columns"], state["rows"], sample=10)
        return {"chart": spec.model_dump(), "chart_source": source}

    @timed("summary_agent")
    def summary_node(state: AnalyticsState):
        rows = state["rows"]
        try:
            text = summarize(llm, state["question"], state["columns"], rows[:rows_for_llm], len(rows), state.get("truncated", False))
        except AllProvidersFailed:
            # The chart and rows are still worth returning when no model is left to write the summary.
            text = None
        return {"summary": text}

    def give_up_node(state: AnalyticsState):
        last = state["failures"][-1]["error"] if state.get("failures") else "unknown error"
        return {"error": f"I couldn't build a working query after {max_attempts} attempts. Last error: {last}"}

    def after_router(state: AnalyticsState):
        return {"data_query": "sql_agent", "schema_question": "schema_answer"}.get(state["intent"], "out_of_scope")

    def after_check(next_node: str):
        def decide(state: AnalyticsState):
            if state.get("sql_ok"):
                return next_node
            return "sql_agent" if len(state.get("failures", [])) < max_attempts else "give_up"

        return decide

    g = StateGraph(AnalyticsState)
    g.add_node("router", router_node)
    g.add_node("schema_answer", schema_node)
    g.add_node("out_of_scope", out_of_scope_node)
    g.add_node("sql_agent", write_sql_node)
    g.add_node("validate", validate_node)
    g.add_node("execute", execute_node)
    g.add_node("chart_agent", chart_node)
    g.add_node("summary_agent", summary_node)
    g.add_node("give_up", give_up_node)

    g.add_edge(START, "router")
    g.add_conditional_edges("router", after_router, ["sql_agent", "schema_answer", "out_of_scope"])
    g.add_edge("sql_agent", "validate")
    g.add_conditional_edges("validate", after_check("execute"), ["execute", "sql_agent", "give_up"])
    g.add_conditional_edges("execute", after_check("chart_agent"), ["chart_agent", "sql_agent", "give_up"])
    g.add_edge("chart_agent", "summary_agent")
    g.add_edge("summary_agent", END)
    g.add_edge("schema_answer", END)
    g.add_edge("out_of_scope", END)
    g.add_edge("give_up", END)
    return g.compile()


def to_response(state: AnalyticsState) -> dict[str, Any]:
    """Shape the final graph state for the API."""
    return {
        "question": state.get("question"),
        "intent": state.get("intent"),
        "answer": state.get("answer"),
        "sql": state.get("sql") if state.get("intent") == "data_query" else None,
        "sql_reasoning": state.get("sql_reasoning"),
        "columns": state.get("columns", []),
        "rows": state.get("rows", []),
        "truncated": state.get("truncated", False),
        "chart": state.get("chart"),
        "chart_source": state.get("chart_source"),
        "summary": state.get("summary"),
        "error": state.get("error"),
        "attempts": state.get("sql_attempts", 0),
        "failures": state.get("failures", []),
        "timings": state.get("timings", {}),
        "llm_calls": state.get("llm_calls", []),
    }
