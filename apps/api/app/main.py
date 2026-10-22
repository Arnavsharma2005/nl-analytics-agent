import json
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sse_starlette.sse import EventSourceResponse

from app.analyst import Analyst, Answer, Done, Step
from app.answer_cache import AnswerCache
from app.config import get_settings
from app.db import Database
from app.llm import FallbackLLM
from app.problems import Problem, from_exception, install
from app.providers import build_llm
from app.sessions import COOKIE, Sessions, Turnstile
from app.usage import Counters, DailyBudget, Quota, Visitor

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


class SessionRequest(BaseModel):
    turnstile_token: str | None = Field(default=None, max_length=4096)


@lru_cache
def get_db() -> Database:
    s = get_settings()
    return Database(s.database_url, s.statement_timeout_ms, s.max_result_rows, s.max_query_cost, s.personal_data_columns)


@lru_cache
def get_app_engine() -> Engine:
    """The app's own database (usage counters, answer cache), migrated with Alembic."""
    return create_engine(get_settings().app_db_url, pool_pre_ping=True)


@lru_cache
def get_counters() -> Counters:
    return Counters(get_app_engine())


@lru_cache
def get_llm() -> FallbackLLM:
    # One instance per process, so a provider that hit its rate limit is skipped by every request.
    s = get_settings()
    budget = DailyBudget(get_counters(), s.llm_daily_budget).spend if s.llm_daily_budget else None
    return build_llm(s, budget)


@lru_cache
def get_analyst() -> Analyst:
    s = get_settings()
    cache = AnswerCache(get_app_engine(), s.git_commit or "dev", s.answer_cache_ttl_s) if s.answer_cache_ttl_s else None
    return Analyst(get_llm(), get_db(), max_attempts=s.max_sql_attempts, rows_for_llm=s.rows_for_llm, cache=cache)


@lru_cache
def get_sessions() -> Sessions:
    s = get_settings()
    bot_check = Turnstile(s.turnstile_secret_key) if s.turnstile_secret_key and s.turnstile_site_key else None
    return Sessions(s.session_secret, s.session_ttl_s, bot_check)


@lru_cache
def get_quota() -> Quota:
    s = get_settings()
    return Quota(get_counters(), s.quota_session_per_hour, s.quota_ip_per_day)


def create_app(analyst_factory=get_analyst, db_factory=get_db, llm_factory=get_llm, sessions_factory=get_sessions,
               quota_factory=get_quota) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="NL Business Analytics Agent")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install(app)

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get(settings.client_ip_header) if settings.client_ip_header else None
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def visitor(request: Request) -> Visitor:
        """Who is asking; raises when the bot check is missing or a quota is used up. Counts one question."""
        sessions = sessions_factory()
        session = sessions.read(request.cookies.get(COOKIE))
        if sessions.required and not session:
            raise Problem(403, "bot_check_required", "Please complete the bot check first",
                          "Start a session with POST /api/session and a Turnstile token.")
        who = Visitor(session, client_ip(request))
        quota = quota_factory()
        exhausted = quota.spend(who) if quota else None
        if exhausted:
            raise Problem(429, "quota_exceeded", "You have reached the question limit for now",
                          "This free demo limits how many questions one visitor can ask. Please try again later.",
                          retry_after=exhausted.retry_after_s)
        return who

    @app.get("/api/config")
    def config():
        """Settings the web app needs at runtime."""
        return {"turnstile_site_key": settings.turnstile_site_key if sessions_factory().required else None}

    @app.post("/api/session")
    def start_session(req: SessionRequest, request: Request):
        """Issue a visitor session cookie, after the Turnstile bot check when it is on."""
        sessions = sessions_factory()
        value = sessions.issue(req.turnstile_token, client_ip(request))
        if value is None:
            raise Problem(403, "bot_check_failed", "The bot check did not pass", "Please reload the page and try again.")
        response = JSONResponse({"expires_in": sessions.ttl_s})
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(COOKIE, value, max_age=sessions.ttl_s, httponly=True, samesite="lax", secure=https)
        return response

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

    @app.post("/api/ask", dependencies=[Depends(visitor)])
    def ask(req: AskRequest) -> Answer:
        return analyst_factory().ask(req.question)

    @app.post("/api/ask/stream", dependencies=[Depends(visitor)])
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
