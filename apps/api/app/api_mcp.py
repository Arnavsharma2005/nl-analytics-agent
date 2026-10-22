"""An MCP server at /mcp, so Claude Desktop, Cursor and other agents can ask the analyst questions as a tool.

It is the same analyst behind the same SQL guard as the web app, and every question counts against the same
limits. MCP clients can't pass the Turnstile bot check, so they are limited by IP address alone (a visitor with no
session); signed-in limits don't apply. Only the connected database is reachable: uploads belong to a browser session.
Off unless MCP=true.

Tools: `ask` (a question in plain English), `list_tables` (what the data holds), `list_verified_questions` and
`run_verified_query` (a reviewed query, run as written, with no model involved).
"""

import asyncio
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from app.analyst import Analyst
from app.db import DataSource, QueryError
from app.problems import Problem, from_exception
from app.semantic import Semantic
from app.sql_guard import validate_sql
from app.usage import Visitor

MAX_ROWS = 50  # rows returned to an agent; enough to read, small enough not to flood its context


class Result(BaseModel):
    columns: list[str]
    rows: list[list[Any]] = Field(description=f"At most {MAX_ROWS} rows.")
    total_rows: int = Field(description="Rows the query returned, before cutting to the rows shown.")
    truncated: bool = Field(description="True when more rows exist than are shown.")


class AnswerOut(BaseModel):
    question: str
    answer: str | None = Field(description="The answer in words: a summary of the result, or a reply when the "
                                           "question wasn't about the data.")
    sql: str | None = Field(description="The query that produced the result.")
    result: Result | None
    assumptions: list[str] = Field(description="How an ambiguous question was read.")
    verified_question: str | None = Field(description="The reviewed question whose query was used as is, if any.")


class ColumnOut(BaseModel):
    name: str
    type: str
    description: str | None
    personal: bool = Field(description="Personal data: can filter and count, never returned.")


class TableOut(BaseModel):
    name: str
    description: str | None
    columns: list[ColumnOut]


def _result(columns: list[str], rows: list[list[Any]], truncated: bool) -> Result:
    return Result(columns=columns, rows=rows[:MAX_ROWS], total_rows=len(rows),
                  truncated=truncated or len(rows) > MAX_ROWS)


def _refused(e: Problem) -> ToolError:
    """A refusal (over the limit, say) as a tool error the agent can read and relay."""
    body = e.body()
    return ToolError(f"{body['title']}. {body.get('detail', '')}".strip())


def mcp_server(*, analyst: Callable[[], Analyst], db: Callable[[], DataSource], library: Callable[[], Semantic],
               spend: Callable[[Visitor], None], client_ip: Callable[[Request], str]) -> MCPServer:
    """`spend(visitor)` counts one question, raising Problem when the limit is reached."""
    server = MCPServer(
        "nl-analytics-agent", title="Sales analyst",
        instructions=f"Answers questions in plain English about {library().about.data}, with the SQL it ran. "
                     "Call list_tables to see what is there. Each question counts against a daily limit.",
    )

    def charge(ctx: Context) -> None:
        request = ctx.request_context.request
        ip = client_ip(request) if isinstance(request, Request) else "local"
        try:
            spend(Visitor(session=None, ip=ip))
        except Problem as e:
            raise _refused(e) from e

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def ask(question: Annotated[str, Field(min_length=3, max_length=500)], ctx: Context) -> AnswerOut:
        """Ask a question about the data in plain English, such as "Revenue by region in 2025". Returns
        the answer, the SQL behind it and the result table. Each call counts against the daily limit."""
        charge(ctx)
        try:
            answer = await asyncio.to_thread(analyst().ask, question.strip())
        except Exception as e:  # every model provider busy, today's budget spent, ...: say so, as the web app does
            raise _refused(from_exception(e)) from e
        if answer.error:
            raise ToolError(f"No working query could be written for this question: {answer.error}")
        table = _result(answer.columns, answer.rows, answer.truncated) if answer.intent == "data_query" else None
        return AnswerOut(question=answer.question, answer=answer.summary or answer.answer, sql=answer.sql,
                         result=table, assumptions=answer.assumptions, verified_question=answer.verified_question)

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def list_tables() -> list[TableOut]:
        """The tables of the data and their columns, with what each one means."""
        return [TableOut(name=t.name, description=t.comment,
                         columns=[ColumnOut(name=c.name, type=c.type, description=c.comment, personal=c.personal)
                                  for c in t.columns])
                for t in db().schema().tables.values()]

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def list_verified_questions() -> list[str]:
        """Questions whose SQL a person has checked by hand. run_verified_query runs one as written."""
        return [q.question for q in library().queries]

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def run_verified_query(question: Annotated[str, Field(max_length=500)], ctx: Context) -> Result:
        """Run the reviewed query for one of the questions from list_verified_questions, exactly as written,
        without a model. Counts against the daily limit."""
        wanted = question.strip().lower()
        query = next((q for q in library().queries if q.question.lower() == wanted), None)
        if query is None:
            raise ToolError("That isn't a verified question. Call list_verified_questions for the exact wording, "
                            "or use ask.")
        charge(ctx)
        source = db()
        schema = source.schema()
        sql = validate_sql(query.sql, set(schema.tables), schema.personal_columns(), source.dialect)
        try:
            result = await asyncio.to_thread(source.run, sql)
        except QueryError as e:
            raise ToolError(f"The query failed: {e}") from e
        return _result(result.columns, result.rows, result.truncated)

    return server


def mcp_route(server: MCPServer) -> Route:
    """The /mcp route: Streamable HTTP, stateless and answered as plain JSON, so it needs no sticky sessions and
    works behind any proxy. Run `server.session_manager.run()` in the app's lifespan."""
    app = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                     host="0.0.0.0")  # not only localhost: the Host header is whatever the proxy sends
    [route] = [r for r in app.routes if isinstance(r, Route) and r.path == "/mcp"]
    return route
