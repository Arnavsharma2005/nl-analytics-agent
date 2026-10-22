"""The analyst: one question in, one answer out.

This is the module every entry point talks to (the HTTP API, the eval runner, and later MCP, bots and schedules).
It hides the LangGraph pipeline, the shape of its state, the step labels shown while it works, and the record
of which model provider served each step.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.agents.chart import ChartSpec
from app.db import DataSource
from app.graph import build_graph
from app.llm import LLM

STEP_LABELS = {
    "router": "Understanding the question",
    "sql_agent": "Writing SQL",
    "validate": "Checking the query against the schema",
    "execute": "Running the query",
    "chart_agent": "Choosing a chart",
    "summary_agent": "Writing the insight",
    "schema_answer": "Describing the data",
    "out_of_scope": "Done",
    "give_up": "Stopped",
    "cache": "Found a recent answer to the same question",
}


class Failure(BaseModel):
    """An attempt that failed validation or execution and was fed back to the SQL agent."""

    sql: str
    error: str
    stage: Literal["validate", "execute"]


class Answer(BaseModel):
    """Everything the analyst returns for one question. New fields are only ever added, and always optional."""

    question: str
    intent: Literal["data_query", "schema_question", "out_of_scope"] | None = None
    answer: str | None = Field(default=None, description="Plain-text reply for schema and out-of-scope questions.")
    sql: str | None = None
    sql_reasoning: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    truncated: bool = False
    chart: ChartSpec | None = None
    chart_source: Literal["llm", "fallback"] | None = None
    summary: str | None = None
    error: str | None = Field(default=None, description="Set when no working query could be built.")
    attempts: int = 0
    failures: list[Failure] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    llm_calls: list[dict[str, Any]] = Field(default_factory=list)
    cached: bool = Field(default=False, description="Served from the answer cache, without running the pipeline.")

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Answer":
        data_query = state.get("intent") == "data_query"
        return cls(
            question=state.get("question", ""),
            intent=state.get("intent"),
            answer=state.get("answer"),
            sql=state.get("sql") if data_query else None,
            sql_reasoning=state.get("sql_reasoning"),
            columns=state.get("columns", []),
            rows=state.get("rows", []),
            truncated=state.get("truncated", False),
            chart=state.get("chart"),
            chart_source=state.get("chart_source"),
            summary=state.get("summary"),
            error=state.get("error"),
            attempts=state.get("sql_attempts", 0),
            failures=state.get("failures", []),
            timings=state.get("timings", {}),
            llm_calls=state.get("llm_calls", []),
        )


class Cache(Protocol):
    """Somewhere to keep answers to questions asked before (see app/answer_cache.py)."""

    def get(self, question: str) -> Answer | None: ...

    def put(self, question: str, answer: Answer) -> None: ...


@dataclass(frozen=True)
class Step:
    """Emitted by `Analyst.stream` as each pipeline step finishes."""

    node: str
    label: str


@dataclass(frozen=True)
class Done:
    """The last event of `Analyst.stream`."""

    answer: Answer


class Analyst:
    """Answers business questions over one data source.

    Construction introspects the data source's schema once, so build one Analyst per data source and reuse it.
    Email addresses and phone numbers in results are always masked (see app/privacy.py).
    `ask` and `stream` raise `AllProvidersFailed` when no model provider can classify the question; failures
    later in the pipeline are reported inside the Answer instead.

    With a `cache`, a question asked before is answered from it (marked `cached`), and complete new answers are
    added to it.
    """

    def __init__(self, llm: LLM, source: DataSource, *, max_attempts: int = 3, rows_for_llm: int = 50,
                 cache: Cache | None = None):
        self._graph = build_graph(llm, source, max_attempts=max_attempts, rows_for_llm=rows_for_llm)
        self._cache = cache

    def ask(self, question: str) -> Answer:
        question = question.strip()
        if self._cache and (hit := self._cache.get(question)):
            return hit
        answer = Answer.from_state(self._graph.invoke({"question": question}))
        if self._cache:
            self._cache.put(question, answer)
        return answer

    async def stream(self, question: str) -> AsyncIterator[Step | Done]:
        question = question.strip()
        # The cache does blocking database calls, so keep them off the event loop.
        if self._cache and (hit := await asyncio.to_thread(self._cache.get, question)):
            yield Step("cache", STEP_LABELS["cache"])
            yield Done(hit)
            return
        final: dict[str, Any] = {}
        async for mode, chunk in self._graph.astream({"question": question}, stream_mode=["updates", "values"]):
            if mode == "updates":
                for node in chunk:
                    yield Step(node, STEP_LABELS.get(node, node))
            else:
                final = chunk
        answer = Answer.from_state(final)
        if self._cache:
            await asyncio.to_thread(self._cache.put, question, answer)
        yield Done(answer)
