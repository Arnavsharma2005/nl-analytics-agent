from typing import Literal

from pydantic import BaseModel, Field

from app.llm import LLM

Intent = Literal["data_query", "schema_question", "out_of_scope"]
WhyIntent = Literal["data_query", "why_change", "schema_question", "out_of_scope"]

SYSTEM = """You are the router for a business analytics assistant over {about}.
Classify the user's message into exactly one intent:

- data_query: a question answerable by querying the data (metrics, trends, rankings, comparisons, counts,
  lists of records). Vague but data-shaped questions ("how are we doing?") are data_query.
{why}- schema_question: a question about what data exists ("what tables do you have?", "what can I ask?").
- out_of_scope: anything else: greetings, small talk, general knowledge, requests to change or delete data,
  or questions about data this database does not hold.

The database tables are: {tables}."""


# Offered only when driver analysis is on (app/analysis/drivers.py); off, the prompt is exactly as before.
WHY = """- why_change: asks why a metric rose or fell, or what drove a change between periods ("why did revenue
  drop in Q3?", "what drove the growth in orders this year?"). A question that only asks how much it changed
  ("how did revenue change in Q3?") is data_query.
"""


class RouteDecision(BaseModel):
    reason: str = Field(description="One short sentence on why this intent fits.")
    intent: Intent


class RouteDecisionWithWhy(BaseModel):
    reason: str = Field(description="One short sentence on why this intent fits.")
    intent: WhyIntent


def route(llm: LLM, question: str, tables: list[str], about: str, why: bool = False
          ) -> RouteDecision | RouteDecisionWithWhy:
    """`why`: also recognise questions about why a metric changed (intent why_change)."""
    system = SYSTEM.format(about=about, tables=", ".join(tables), why=WHY if why else "")
    return llm.structured(RouteDecisionWithWhy if why else RouteDecision, system, question, tier="fast")


SCHEMA_SYSTEM = """You help business users understand what data they can ask about.
Using only the schema below, answer the user's question in 2-5 plain-English sentences, without SQL or
column type jargon, and end with two example questions they could ask.

{schema}"""


def answer_schema_question(llm: LLM, question: str, schema_text: str) -> str:
    return llm.text(SCHEMA_SYSTEM.format(schema=schema_text), question)
