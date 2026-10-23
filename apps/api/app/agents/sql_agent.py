import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field

from app.brief import Brief
from app.llm import LLM

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "semantic" / "prompts"
PLACEHOLDERS = ("{dialect}", "{schema}", "{rules}", "{time_column}")


class PromptError(ValueError):
    """A prompt file that is missing or cannot be filled in."""


@dataclass(frozen=True)
class SQLPrompt:
    """The SQL writer's system prompt, one version of semantic/prompts/sql_writer.vN.md.

    Versions are files, never edited once used: a new prompt (by hand or from eval/optimize_prompt.py) is a new
    version, measured on the held-out eval split before SQL_PROMPT points at it. Every answer records the version."""

    version: str
    text: str

    def __post_init__(self):
        if missing := [p for p in PLACEHOLDERS if p not in self.text]:
            raise PromptError(f"SQL prompt {self.version} lacks {', '.join(missing)}")
        try:
            self.text.format(dialect="", schema="", rules="", time_column="")
        except (KeyError, IndexError, ValueError) as e:
            raise PromptError(f"SQL prompt {self.version} has braces other than its placeholders ({e}); "
                              "write a literal brace as {{ or }}") from e

    @classmethod
    def load(cls, version: str = "v1", directory: Path = PROMPTS_DIR) -> "SQLPrompt":
        if not re.fullmatch(r"v\d+", version):
            raise PromptError(f"a prompt version looks like v1 or v12, not {version!r}")
        path = directory / f"sql_writer.{version}.md"
        if not path.is_file():
            raise PromptError(f"there is no SQL prompt {version} ({path})")
        return cls(version, path.read_text().rstrip("\n"))


@cache
def default_prompt() -> SQLPrompt:
    return SQLPrompt.load("v1")


class SQLDraft(BaseModel):
    reasoning: str = Field(description="Two or three sentences: which tables, joins, filters and grouping answer the question.")
    sql: str = Field(description="The PostgreSQL SELECT query, with no markdown fences.")


# With context retrieval on, the prompt also carries question-specific context (verified examples, values the
# question names) and the model states the readings it chose. Off, the prompt is exactly as before.
CONTEXT_STYLE = "\n- If the question can be read more than one way, choose the most likely reading and state it in assumptions."


class ContextualSQLDraft(SQLDraft):
    assumptions: list[str] = Field(
        default_factory=list,
        description="Readings you chose where the question was ambiguous, in plain words for a business user, "
                    "e.g. \"2025 means calendar year 2025\". Empty when there were none.",
    )


DIALECTS = {"postgres": "PostgreSQL", "duckdb": "DuckDB"}


def write_sql(llm: LLM, question: str, schema_text: str, previous: list[tuple[str, str]], brief: Brief,
              dialect: str = "postgres", context: str | None = None, prompt: SQLPrompt | None = None) -> SQLDraft:
    """`previous` holds (sql, error) for earlier attempts that failed validation or execution. `dialect` is the
    data source's (see app.db.DataSource). `context`, when given (even empty), is the question's context from
    app.context and asks for a `ContextualSQLDraft`. `prompt` is the system prompt's version (default v1)."""
    system = (prompt or default_prompt()).text.format(dialect=DIALECTS[dialect], schema=schema_text, rules=brief.rules,
                           time_column=brief.time_column)
    user = f"Question: {question}"
    if context:
        user += f"\n\n{context}"
    if previous:
        user += "\n\nYour earlier attempts failed. Fix the problem and return a corrected query."
        for i, (sql, error) in enumerate(previous, 1):
            user += f"\n\nAttempt {i}:\n{sql}\nError: {error}"
    if context is None:
        return llm.structured(SQLDraft, system, user)
    return llm.structured(ContextualSQLDraft, system + CONTEXT_STYLE, user)
