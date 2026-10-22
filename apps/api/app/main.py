import hmac
import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager
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

from app import semantic
from app.analyst import Analyst, Answer, Done, Step
from app.answer_cache import AnswerCache
from app.api_auth import auth_router, is_https, signed_in_user
from app.api_curator import curator_router
from app.api_datasets import datasets_router
from app.api_mcp import mcp_route, mcp_server
from app.api_shares import shares_router
from app.api_v1 import v1_router
from app.auth import GitHub, Users
from app.config import get_settings
from app.context import Context
from app.curation import Curation, LibraryPR
from app.datasets.csv_upload import Limits
from app.datasets.registry import DatasetAnalysts
from app.datasets.sheets import SheetsClient
from app.datasets.store import Datasets
from app.db import Database
from app.llm import FallbackLLM
from app.messages import Messages
from app.problems import Problem, from_exception, install
from app.providers import build_embedder, build_llm
from app.sessions import COOKIE, Sessions, Turnstile
from app.shares import Shares
from app.transcribe import Transcriber
from app.usage import Counters, DailyBudget, Quota, Visitor

log = logging.getLogger(__name__)


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
    db = get_db()
    context = (Context(semantic.load(), db.schema(), build_embedder(s), fast_path=s.verified_fast_path)
               if s.context_retrieval else None)
    switches = {"context": bool(context), "fast_path": bool(context) and s.verified_fast_path,
                "check_results": s.check_results, "check_summaries": s.check_summaries}
    cache = None
    if s.answer_cache_ttl_s:
        # Answers depend on the build and on which features are on, so either changing starts a fresh cache.
        version = (s.git_commit or "dev") + "".join(f":{k}" for k, on in switches.items() if on)
        cache = AnswerCache(get_app_engine(), version, s.answer_cache_ttl_s)
    return Analyst(get_llm(), db, max_attempts=s.max_sql_attempts, rows_for_llm=s.rows_for_llm, cache=cache,
                   context=context, check_results=s.check_results, check_summaries=s.check_summaries)


@lru_cache
def get_sessions() -> Sessions:
    s = get_settings()
    bot_check = Turnstile(s.turnstile_secret_key) if s.turnstile_secret_key and s.turnstile_site_key else None
    return Sessions(s.session_secret, s.session_ttl_s, bot_check)


@lru_cache
def get_quota() -> Quota:
    s = get_settings()
    return Quota(get_counters(), s.quota_session_per_hour, s.quota_ip_per_day, s.quota_user_per_hour,
                 s.quota_user_per_day)


@lru_cache
def get_messages() -> Messages:
    s = get_settings()
    return Messages(get_app_engine(), s.message_retention_days, user_retention_days=s.user_message_retention_days)


@lru_cache
def get_datasets() -> Datasets:
    s = get_settings()
    return Datasets(get_app_engine(), s.upload_ttl_hours, s.uploads_per_owner, s.uploads_total_mb)


@lru_cache
def get_dataset_analysts() -> DatasetAnalysts:
    s = get_settings()
    return DatasetAnalysts(get_datasets(), get_llm, keep=s.uploads_open, max_attempts=s.max_sql_attempts,
                           rows_for_llm=s.rows_for_llm, statement_timeout_ms=s.statement_timeout_ms,
                           max_rows=s.max_result_rows, memory_limit_mb=s.upload_query_memory_mb)


@lru_cache
def get_shares() -> Shares:
    return Shares(get_app_engine(), get_settings().share_keep_days)


@lru_cache
def get_users() -> Users:
    return Users(get_app_engine())


@lru_cache
def get_github() -> GitHub | None:
    s = get_settings()
    return GitHub(s.github_client_id, s.github_client_secret) if s.github_client_id and s.github_client_secret else None


@lru_cache
def get_curation() -> Curation:
    return Curation(get_app_engine(), semantic.load())


@lru_cache
def get_library_pr() -> LibraryPR | None:
    s = get_settings()
    if not (s.curator_github_token and s.curator_repo):
        return None
    return LibraryPR(s.curator_github_token, s.curator_repo, s.curator_base_branch)


@lru_cache
def get_suggester() -> Context:
    # Follow-up suggestions compare questions by their words: no embedding call per answer.
    return Context(semantic.load(), get_db().schema())


@lru_cache
def get_transcriber() -> Transcriber | None:
    s = get_settings()
    if not (s.transcribe_model and s.groq_api_key):
        return None
    return Transcriber(s.groq_api_key, s.transcribe_model)


def check_data_access(db: Database, required: bool) -> None:
    """Say so when the data connection could change data; refuse to start when it must only read."""
    reasons = db.write_access()
    if not reasons:
        return
    message = ("DATABASE_URL can change data: " + "; ".join(reasons) + ". Connect as a role that can only SELECT "
               "(docs/self-host.md shows how).")
    if required:
        raise RuntimeError(message + " Refusing to start because REQUIRE_READ_ONLY_DATA is on.")
    log.warning(message)


def create_app(analyst_factory=get_analyst, db_factory=get_db, llm_factory=get_llm, sessions_factory=get_sessions,
               quota_factory=get_quota, messages_factory=get_messages, suggester_factory=get_suggester,
               transcriber_factory=get_transcriber, users_factory=get_users, github_factory=get_github,
               datasets_factory=get_datasets, dataset_analysts_factory=get_dataset_analysts,
               shares_factory=get_shares, sheets_factory=SheetsClient, curation_factory=get_curation,
               library_pr_factory=get_library_pr) -> FastAPI:
    settings = get_settings()

    mcp = None  # the MCP server, made below once `spend` exists

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.database_url != settings.app_db_url:  # your own data, apart from the app's tables
            check_data_access(db_factory(), settings.require_read_only_data)
        async with AsyncExitStack() as stack:
            if mcp:
                await stack.enter_async_context(mcp.session_manager.run())
            yield
        if settings.uploads:
            dataset_analysts_factory().close()  # their DuckDB files live on the local disk

    app = FastAPI(title="NL Business Analytics Agent", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install(app)

    def client_ip(request: Request) -> str:
        secret = request.headers.get("x-proxy-secret")
        if settings.proxy_secret and secret and hmac.compare_digest(secret.encode(), settings.proxy_secret.encode()):
            if visitor_ip := request.headers.get("x-client-ip"):
                return visitor_ip.strip()
        forwarded = request.headers.get(settings.client_ip_header) if settings.client_ip_header else None
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def identify(request: Request) -> Visitor:
        """Who is asking; raises when the bot check is on and the request has neither a session nor a sign-in."""
        sessions = sessions_factory()
        session = sessions.read(request.cookies.get(COOKIE))
        user = signed_in_user(sessions, request)
        if sessions.required and not session and not user:
            raise Problem(403, "bot_check_required", "Please complete the bot check first",
                          "Start a session with POST /api/session and a Turnstile token.")
        return Visitor(session, client_ip(request), user)

    def spend(who: Visitor) -> None:
        """Count one question against the visitor's quota; raises when it is used up."""
        quota = quota_factory()
        exhausted = quota.spend(who) if quota else None
        if exhausted:
            raise Problem(429, "quota_exceeded", "You have reached the question limit for now",
                          "This free demo limits how many questions one visitor can ask. Please try again later.",
                          retry_after=exhausted.retry_after_s)

    def visitor(request: Request) -> Visitor:
        """`identify`, then `spend`: for routes where every request is a new question."""
        who = identify(request)
        spend(who)
        return who

    def turnstile_site_key() -> str | None:
        return settings.turnstile_site_key if sessions_factory().required else None

    def starters() -> list[str]:
        return [q.question for q in semantic.load().queries if q.starter]

    app.include_router(v1_router(
        analyst=analyst_factory, messages=messages_factory, llm=llm_factory, suggester=suggester_factory,
        transcriber=transcriber_factory, identify=identify, spend=spend, turnstile_site_key=turnstile_site_key,
        starters=starters, sign_in=lambda: github_factory() is not None, datasets=dataset_analysts_factory,
        upload_max_mb=settings.upload_max_mb if settings.uploads else None,
    ))
    if settings.uploads:
        app.include_router(datasets_router(
            store=datasets_factory, analysts=dataset_analysts_factory, identify=identify, spend=spend,
            limits=Limits(settings.upload_max_mb * 1024 * 1024, settings.upload_max_rows, settings.upload_max_columns),
            sheets=sheets_factory,
        ))
    app.include_router(shares_router(shares=shares_factory, messages=messages_factory, datasets=datasets_factory,
                                     identify=identify))

    def adopt(session: str, owner: str) -> None:
        for kept in (messages_factory(), datasets_factory(), shares_factory()):
            kept.adopt(session, owner)

    curators = {login.lower() for login in settings.curators}

    def login(user_id: int) -> str | None:
        user = users_factory().get(user_id)
        return user.login if user else None

    app.include_router(auth_router(
        sessions=sessions_factory, github=github_factory, users=users_factory, adopt=adopt,
        public_url=settings.public_url, user_session_days=settings.user_session_days,
        is_curator=lambda name: name.lower() in curators,
    ))
    app.include_router(curator_router(curation=curation_factory, library_pr=library_pr_factory, identify=identify,
                                      login=login, curators=curators))

    if settings.mcp:
        mcp = mcp_server(analyst=analyst_factory, db=db_factory, library=semantic.load, spend=spend,
                         client_ip=client_ip)
        app.router.routes.append(mcp_route(mcp))

    @app.get("/api/config")
    def config():
        """Settings the web app needs at runtime."""
        return {"turnstile_site_key": turnstile_site_key()}

    @app.post("/api/session")
    @app.post("/v1/session", tags=["v1"])
    def start_session(req: SessionRequest, request: Request):
        """Issue a visitor session cookie, after the Turnstile bot check when it is on."""
        sessions = sessions_factory()
        value = sessions.issue(req.turnstile_token, client_ip(request))
        if value is None:
            raise Problem(403, "bot_check_failed", "The bot check did not pass", "Please reload the page and try again.")
        response = JSONResponse({"expires_in": sessions.ttl_s})
        response.set_cookie(COOKIE, value, max_age=sessions.ttl_s, httponly=True, samesite="lax",
                            secure=is_https(request))
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
        return starters()

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
    dist = Path(__file__).resolve().parents[3] / "apps" / ("web-next" if settings.web_app == "next" else "web") / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            file = (dist / path).resolve()
            ok = path and file.is_file() and file.is_relative_to(dist.resolve())
            return FileResponse(file if ok else dist / "index.html")

    return app


app = create_app()
