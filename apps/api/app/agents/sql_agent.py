from pydantic import BaseModel, Field

from app import semantic
from app.llm import LLM

SYSTEM = """You are an expert PostgreSQL analyst. Write ONE read-only PostgreSQL SELECT query that answers
the user's question over the schema below.

SCHEMA
{schema}

BUSINESS RULES
{rules}

STYLE
- Return human-readable labels (region, category, product or customer names), never bare ids.
- Give every computed column a short snake_case alias, e.g. revenue, order_count, month.
- For time series, group by date_trunc('month' | 'quarter' | 'week', order_date)::date and order by it ascending.
- Round money and percentages with ROUND(x::numeric, 2). Use NULLIF to avoid division by zero.
- For "top N" / "bottom N" questions, ORDER BY the measure and LIMIT N; when no N is given use 10.
- Use only the tables and columns in the schema. No SELECT *. Do not end with a semicolon."""


class SQLDraft(BaseModel):
    reasoning: str = Field(description="Two or three sentences: which tables, joins, filters and grouping answer the question.")
    sql: str = Field(description="The PostgreSQL SELECT query, with no markdown fences.")


def write_sql(llm: LLM, question: str, schema_text: str, previous: list[tuple[str, str]]) -> SQLDraft:
    """`previous` holds (sql, error) for earlier attempts that failed validation or execution."""
    user = f"Question: {question}"
    if previous:
        user += "\n\nYour earlier attempts failed. Fix the problem and return a corrected query."
        for i, (sql, error) in enumerate(previous, 1):
            user += f"\n\nAttempt {i}:\n{sql}\nError: {error}"
    return llm.structured(SQLDraft, SYSTEM.format(schema=schema_text, rules=semantic.load().rules_text()), user)
