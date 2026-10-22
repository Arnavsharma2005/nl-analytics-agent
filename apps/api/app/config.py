from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read the repo-root .env wherever the app is started from (repo root, apps/api/ or eval/);
    # a .env in the current directory, if any, wins.
    model_config = SettingsConfigDict(env_file=(Path(__file__).resolve().parents[3] / ".env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/analytics"
    # Providers tried in order; see app/providers.py. The first one with a key that responds wins.
    llm_providers: str = "gemini,groq,openrouter,mistral,cloudflare"
    llm_timeout_s: float = 30

    gemini_api_key: str | None = None
    google_api_key: str | None = None  # accepted as an alias for GEMINI_API_KEY
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None
    mistral_api_key: str | None = None
    cloudflare_api_token: str | None = None
    cloudflare_account_id: str | None = None

    # Optional model overrides, e.g. GROQ_MODEL=qwen/qwen3.6-27b
    gemini_model: str | None = None
    groq_model: str | None = None
    openrouter_model: str | None = None
    mistral_model: str | None = None
    cloudflare_model: str | None = None
    # Optional cheaper, higher-quota models for the light steps (routing, chart choice), e.g.
    # GROQ_FAST_MODEL=llama-3.1-8b-instant. Unset means the provider uses its main model for every step.
    gemini_fast_model: str | None = None
    groq_fast_model: str | None = None
    openrouter_fast_model: str | None = None
    mistral_fast_model: str | None = None
    cloudflare_fast_model: str | None = None

    max_sql_attempts: int = 3
    max_result_rows: int = 500
    statement_timeout_ms: int = 10_000
    # Queries whose planned cost (Postgres EXPLAIN units) is above this are sent back to the SQL agent instead of
    # run. The costliest gold query is about 4,000; a missing join condition on order_items is about 12,000,000.
    max_query_cost: float = 1_000_000
    # Columns that hold personal data ("table.column"). They can filter and be counted but are never returned.
    personal_data_columns: list[str] = ["customers.email"]
    # Rows shown to the chart and summary agents; the UI still gets every row.
    rows_for_llm: int = 50

    cors_origins: list[str] = ["http://localhost:5173"]

    # --- Usage limits (see SECURITY.md) -----------------------------------------------------------------
    # Where the app keeps its own tables (usage counters, answer cache), in the "app" schema. Defaults to
    # DATABASE_URL; set it when DATABASE_URL is the read-only analytics role.
    app_database_url: str | None = None
    # Signs the visitor's session cookie. Set a long random value in production; without it a random one is made
    # at startup, so sessions end on every restart.
    session_secret: str | None = None
    session_ttl_s: int = 12 * 3600
    # Cloudflare Turnstile bot check before a session is issued. Off unless both are set.
    turnstile_site_key: str | None = None
    turnstile_secret_key: str | None = None
    # Header holding the real client IP when behind a proxy, e.g. true-client-ip on Render (which sits behind
    # Cloudflare). Unset means the connecting address.
    client_ip_header: str | None = None
    # Shared with the web app's proxy (apps/web-next/functions). A request carrying it in X-Proxy-Secret came
    # through the proxy, so its X-Client-IP header is the visitor's address. Unset, X-Client-IP is ignored.
    proxy_secret: str | None = None
    # Questions per visitor session per hour, and per IP address per day. 0 turns a limit off.
    quota_session_per_hour: int = 30
    quota_ip_per_day: int = 300
    # Questions per signed-in user per hour and per day (they have no IP limit). 0 turns a limit off.
    quota_user_per_hour: int = 60
    quota_user_per_day: int = 500
    # Model calls per provider per UTC day, e.g. {"groq": 900}. Kept a little under each free tier so the app
    # can say "today's free limit is used up" instead of failing. Providers not listed are not limited.
    llm_daily_budget: dict[str, int] = {}
    # Repeated questions are answered from a cache for this long. 0 turns the cache off.
    answer_cache_ttl_s: int = 24 * 3600

    # --- Context and checks (see README). Each is off by default and is turned on only when the held-out eval
    # score holds up with it. ---------------------------------------------------------------------------------
    # Show the SQL agent verified queries for similar questions and the data values a question names, and have it
    # state the readings it chose.
    context_retrieval: bool = False
    # Answer a question with a verified query outright when it asks the same thing (needs CONTEXT_RETRIEVAL).
    verified_fast_path: bool = False
    # Send a query that finds nothing back to the SQL agent once.
    check_results: bool = False
    # Have a summary rewritten once if it quotes figures that are not in the result.
    check_summaries: bool = False
    # Embedding model for finding similar questions, on Cloudflare Workers AI (uses the CLOUDFLARE_* keys). Without
    # those keys, or with this empty, questions are compared by the words they share.
    embedding_model: str | None = "@cf/baai/bge-m3"

    # --- /v1 ------------------------------------------------------------------------------------------------
    # Questions and answers asked through /v1 are kept this long, for retries, ratings and explanations; a
    # signed-in user's for longer, as their history.
    message_retention_days: int = 7
    user_message_retention_days: int = 90

    # --- Uploads: CSV files people ask about instead of the demo data, kept for a day (app/datasets). ---------
    uploads: bool = True
    upload_max_mb: int = 5
    upload_max_rows: int = 200_000
    upload_max_columns: int = 60
    upload_ttl_hours: int = 24
    uploads_per_owner: int = 3
    # All uploads together, as stored Parquet, so the free database never fills up.
    uploads_total_mb: int = 200
    # Uploaded datasets kept open in memory for questions, and the memory each one's queries may use.
    uploads_open: int = 3
    upload_query_memory_mb: int = 128
    share_keep_days: int = 90  # how long a share link works

    # --- Sign-in with GitHub (optional). Off unless both are set. Create an OAuth app at
    # https://github.com/settings/developers with the callback URL <PUBLIC_URL>/v1/auth/github/callback. ----------
    github_client_id: str | None = None
    github_client_secret: str | None = None
    # Where visitors reach the web app, e.g. https://nl-analytics-agent.pages.dev. The sign-in callback and the
    # page visitors return to are on this origin. Unset means the origin the API was called on (local development).
    public_url: str | None = None
    # How long a sign-in lasts.
    user_session_days: int = 30
    # Speech to text for voice questions, with Whisper on Groq (needs GROQ_API_KEY). Empty turns voice off.
    transcribe_model: str | None = "whisper-large-v3-turbo"

    # Set by Render on every deploy; reported by /healthz so you can tell which build is live.
    git_commit: str | None = Field(default=None, validation_alias=AliasChoices("GIT_COMMIT", "RENDER_GIT_COMMIT"))

    @field_validator("database_url", "app_database_url")
    @classmethod
    def use_psycopg_driver(cls, url: str | None) -> str | None:
        # Railway and most hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named.
        for prefix in ("postgres://", "postgresql://"):
            if url and url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def app_db_url(self) -> str:
        return self.app_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
