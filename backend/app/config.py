from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/analytics"
    # Any model string accepted by langchain's init_chat_model, e.g.
    # "google_genai:gemini-2.5-flash", "anthropic:claude-sonnet-5-5", "openai:gpt-4.1-mini".
    llm_model: str = "google_genai:gemini-2.5-flash"

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
