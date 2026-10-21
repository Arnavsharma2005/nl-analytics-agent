from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read the repo-root .env wherever the app is started from (repo root, backend/ or eval/);
    # a .env in the current directory, if any, wins.
    model_config = SettingsConfigDict(env_file=(Path(__file__).resolve().parents[2] / ".env", ".env"), extra="ignore")

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

    max_sql_attempts: int = 3
    max_result_rows: int = 500
    statement_timeout_ms: int = 10_000
    # Rows shown to the chart and summary agents; the UI still gets every row.
    rows_for_llm: int = 50

    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, url: str) -> str:
        # Railway and most hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
