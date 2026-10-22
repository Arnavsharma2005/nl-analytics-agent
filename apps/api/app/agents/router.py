from functools import cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.llm import LLM

Intent = Literal["data_query", "schema_question", "out_of_scope"]

SYSTEM = """You are the router for a business analytics assistant over {about}.
Classify the user's message into exactly one intent:

- data_query: a question answerable by querying the data (metrics, trends, rankings, comparisons, counts,
  lists of records). Vague but data-shaped questions ("how are we doing?") are data_query.
{extra}- schema_question: a question about what data exists ("what tables do you have?", "what can I ask?").
- out_of_scope: anything else: greetings, small talk, general knowledge, requests to change or delete data,
  or questions about data this database does not hold.

The database tables are: {tables}."""


# Each offered only when its analysis is on (app/analysis/); with neither, the prompt is exactly as before.
WHY = """- why_change: asks why a metric rose or fell, or what drove a change between periods ("why did revenue
  drop in Q3?", "what drove the growth in orders this year?"). A question that only asks how much it changed
  ("how did revenue change in Q3?") is data_query.
"""
FORECAST = """- forecast: asks what a metric will be in the future, or for a forecast or projection ("what will revenue
  be next quarter?", "forecast orders for 2026"). Questions about past or current figures are data_query.
"""


class RouteDecision(BaseModel):
    reason: str = Field(description="One short sentence on why this intent fits.")
    intent: Intent


@cache
def _decision(why: bool, forecast: bool) -> type[BaseModel]:
    if not (why or forecast):
        return RouteDecision
    extra = (["why_change"] if why else []) + (["forecast"] if forecast else [])
    name = "RouteDecisionWith" + "".join(["Why"] * why + ["Forecast"] * forecast)
    return create_model(name, reason=(str, Field(description="One short sentence on why this intent fits.")),
                        intent=(Literal[("data_query", *extra, "schema_question", "out_of_scope")], ...))


def route(llm: LLM, question: str, tables: list[str], about: str, why: bool = False, forecast: bool = False
          ) -> BaseModel:
    """The question's intent. `why` also offers why_change (a metric's change explained), `forecast` forecast."""
    extra = (WHY if why else "") + (FORECAST if forecast else "")
    system = SYSTEM.format(about=about, tables=", ".join(tables), extra=extra)
    return llm.structured(_decision(why, forecast), system, question, tier="fast")


SCHEMA_SYSTEM = """You help business users understand what data they can ask about.
Using only the schema below, answer the user's question in 2-5 plain-English sentences, without SQL or
column type jargon, and end with two example questions they could ask.

{schema}"""


def answer_schema_question(llm: LLM, question: str, schema_text: str) -> str:
    return llm.text(SCHEMA_SYSTEM.format(schema=schema_text), question)
