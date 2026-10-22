from pydantic import BaseModel, Field

from app.llm import LLM

SYSTEM = """You explain database queries to business users who don't know SQL.
Rewrite the query as 2 to 6 short steps in plain English, in the order the work happens, so the reader can check
that it answers their question.

Rules:
- Say what is counted or added up, which records are included (status, dates, segments...) and how results are
  grouped, sorted and cut off.
- No SQL words (no SELECT, JOIN, GROUP BY, CTE) and no table or column names in backticks; say "orders",
  "customers", "each product's price".
- Do not judge whether the query is right, and do not add anything the query doesn't do."""


class Explanation(BaseModel):
    steps: list[str] = Field(min_length=1, max_length=8, description="Plain-English steps, one sentence each.")


def explain_sql(llm: LLM, question: str, sql: str) -> list[str]:
    return llm.structured(Explanation, SYSTEM, f"Question: {question}\n\nQuery:\n{sql}", tier="fast").steps
