import json
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

from app.analyst import Analyst, Answer, Done, Step
from app.config import get_settings
from app.db import Database
from app.llm import FallbackLLM
from app.problems import Problem, from_exception, install
from app.providers import build_llm

log = logging.getLogger(__name__)

EXAMPLES = [
    "What was our monthly revenue in 2025?",
    "Which region grew revenue fastest from 2024 to 2025?",
    "Top 10 products by profit in 2025",
    "What share of 2025 revenue came from each channel?",
    "Which product category has the highest return rate?",
    "How many new customers signed up each quarter?",
    "Average order value by customer segment",
    "Who are our top 5 customers by lifetime revenue?",
]

class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@lru_cache
def get_db() -> Database:
    s = get_settings()
    return Database(s.database_url, s.statement_timeout_ms, s.max_result_rows, s.max_query_cost, s.personal_data_columns)


@lru_cache
def get_llm() -> FallbackLLM:
    # One instance per process, so a provider that hit its rate limit is skipped by every request.
    return build_llm(get_settings())


@lru_cache
def get_analyst() -> Analyst:
    s = get_settings()
    return Analyst(get_llm(), get_db(), max_attempts=s.max_sql_attempts, rows_for_llm=s.rows_for_llm)


def create_app(analyst_factory=get_analyst, db_factory=get_db, llm_factory=get_llm) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="NL Business Analytics Agent")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install(app)

    @app.get("/healthz")
    def healthz():
        """Liveness for uptime pings and the host's health check. Never touches the database, so a serverless
        database can still suspend while the app is idle."""
        return {"status": "ok", "commit": settings.git_commit}

    @app.get("/readyz")
    @app.get("/api/health")
    def readyz():
        """Readiness: the database answers."""
        try:
            with db_factory().engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:  # noqa: BLE001 - the reason goes to the log, not to the caller
            log.warning("readiness check failed: %s", e)
            raise Problem(503, "database_unavailable", "The database is not reachable") from e
        return {"status": "ok"}

    @app.get("/api/providers")
    def providers():
        """Configured model providers in fallback order, and which are cooling down. Never returns keys."""
        return llm_factory().status()

    @app.get("/api/examples")
    def examples():
        return EXAMPLES

    @app.get("/api/schema")
    def schema():
        return [
            {"table": t.name, "description": t.comment, "columns": [{"name": c.name, "type": c.type, "description": c.comment} for c in t.columns]}
            for t in db_factory().schema().tables.values()
        ]

    @app.post("/api/ask")
    def ask(req: AskRequest) -> Answer:
        return analyst_factory().ask(req.question)

    @app.post("/api/ask/stream")
    async def ask_stream(req: AskRequest):
        analyst = analyst_factory()

        async def events():
            try:
                async for event in analyst.stream(req.question):
                    if isinstance(event, Step):
                        yield {"event": "step", "data": json.dumps({"node": event.node, "label": event.label})}
                    elif isinstance(event, Done):
                        yield {"event": "result", "data": event.answer.model_dump_json()}
            except Exception as e:  # noqa: BLE001 - report to the client instead of dropping the stream
                problem = from_exception(e).body()
                # `error` is what the current web app reads; the rest is the same problem body as the JSON routes.
                yield {"event": "error", "data": json.dumps({"error": problem.get("detail", problem["title"]), **problem})}

        return EventSourceResponse(events())

    # In production the built React app is served from the same origin.
    dist = Path(__file__).resolve().parents[3] / "apps" / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            file = (dist / path).resolve()
            ok = path and file.is_file() and file.is_relative_to(dist.resolve())
            return FileResponse(file if ok else dist / "index.html")

    return app


app = create_app()
