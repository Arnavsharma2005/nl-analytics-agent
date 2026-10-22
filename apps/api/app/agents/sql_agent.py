from pydantic import BaseModel, Field

from app.brief import Brief
from app.llm import LLM

SYSTEM = """You are an expert {dialect} analyst. Write ONE read-only {dialect} SELECT query that answers
the user's question over the schema below.

SCHEMA
{schema}

BUSINESS RULES
{rules}

STYLE
- Return human-readable labels (region, category, product or customer names), never bare ids.
- Give every computed column a short snake_case alias, e.g. revenue, order_count, month.
- For time series, group by date_trunc('month' | 'quarter' | 'week', {time_column})::date and order by it ascending.
- Round money and percentages with ROUND(x::numeric, 2). Use NULLIF to avoid division by zero.
- For "top N" / "bottom N" questions, ORDER BY the measure and LIMIT N; when no N is given use 10.
- Use only the tables and columns in the schema. No SELECT *. Do not end with a semicolon."""


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
              dialect: str = "postgres", context: str | None = None) -> SQLDraft:
    """`previous` holds (sql, error) for earlier attempts that failed validation or execution. `dialect` is the
    data source's (see app.db.DataSource). `context`, when given (even empty), is the question's context from
    app.context and asks for a `ContextualSQLDraft`."""
    system = SYSTEM.format(dialect=DIALECTS[dialect], schema=schema_text, rules=brief.rules,
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
