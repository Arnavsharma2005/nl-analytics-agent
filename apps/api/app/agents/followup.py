"""Follow-up questions: rewrite a question that leans on earlier ones ("and in 2024?") into one that stands alone,
so the rest of the pipeline can answer it exactly as it answers a first question."""

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.llm import LLM

MAX_TURNS = 3

SYSTEM = """You help a business analytics assistant understand follow-up questions.
Rewrite the latest message so it can be understood on its own, without the earlier conversation.
Fill in what it leaves out from the earlier questions and the SQL that answered them: the metric, the filters,
the time period, the grouping, the number of results. Keep everything the latest message changes or adds.
If the latest message already stands on its own, or is not a question about the data, return it unchanged.
Write it as a question a business user would ask, without SQL."""


@dataclass(frozen=True)
class Turn:
    """An earlier question in the conversation (as it was understood) and what answered it."""

    question: str
    sql: str | None = None
    summary: str | None = None


class Standalone(BaseModel):
    question: str = Field(description="The latest message rewritten to stand on its own, or unchanged.")


def _describe(turns: list[Turn]) -> str:
    lines = []
    for i, t in enumerate(turns, 1):
        lines.append(f"{i}. {t.question}")
        if t.sql:
            lines.append(f"   SQL: {' '.join(t.sql.split())[:800]}")
        if t.summary:
            lines.append(f"   Answer: {' '.join(t.summary.split())[:300]}")
    return "\n".join(lines)


def standalone(llm: LLM, question: str, history: list[Turn]) -> str:
    """The question, made to stand on its own using the last few turns. With no history it is returned as is."""
    turns = history[-MAX_TURNS:]
    if not turns:
        return question
    user = f"Earlier in the conversation:\n{_describe(turns)}\n\nLatest message: {question}"
    rewritten = llm.structured(Standalone, SYSTEM, user, tier="fast").question.strip()
    return rewritten or question
