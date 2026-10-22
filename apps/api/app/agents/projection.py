"""Reads a forecasting question as the history to query and how far ahead to forecast (app/analysis/forecast.py
does the forecasting in code)."""

from pydantic import BaseModel, Field

from app.llm import LLM

SYSTEM = """You read a business question asking for a forecast or projection, and say what history to query and
how far ahead to forecast. You do not answer it.

- history_question: the question for the history the forecast is based on, as one measure per period over all
  the data, oldest first. Use the period size the question implies; monthly unless it says otherwise. Keep any
  filter it names. For example "What will revenue be next quarter?" becomes "Monthly revenue".
- periods_ahead: how many of those periods to forecast. "Next quarter" with monthly history is 3; "next year"
  is 12; with no horizon given, 3. At most 24."""


class ProjectionReading(BaseModel):
    history_question: str = Field(min_length=3, max_length=300)
    periods_ahead: int = Field(ge=1, le=24)


def read_projection(llm: LLM, question: str) -> ProjectionReading:
    return llm.structured(ProjectionReading, SYSTEM, f"Question: {question}", tier="fast")
