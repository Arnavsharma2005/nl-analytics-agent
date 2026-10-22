"""What the analyst knows about this business beyond the schema, kept as files a data owner can edit and review.

`semantic/metrics.yml` holds the business rules (what revenue, orders or "last quarter" mean here), and
`semantic/queries.yml` holds verified queries: questions with SQL someone has checked by hand. CI runs every
verified query and checks that none of them gives away a held-out eval question.
"""

from datetime import date
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

SEMANTIC_DIR = Path(__file__).resolve().parents[1] / "semantic"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rule(_Strict):
    id: str
    text: str
    terms: tuple[str, ...] = ()  # empty: the rule applies to every question

    @field_validator("text")
    @classmethod
    def _one_paragraph(cls, text: str) -> str:
        if not text.strip() or "\n\n" in text:
            raise ValueError("a rule is one non-empty paragraph")
        return text.strip()


class VerifiedQuery(_Strict):
    question: str
    sql: str
    verified_by: str
    verified_at: date
    starter: bool = False  # offered as an example question in the app

    @field_validator("question", "sql")
    @classmethod
    def _stripped(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class Semantic(_Strict):
    rules: tuple[Rule, ...]
    queries: tuple[VerifiedQuery, ...] = Field(default=())

    @field_validator("rules")
    @classmethod
    def _unique_ids(cls, rules: tuple[Rule, ...]) -> tuple[Rule, ...]:
        ids = [r.id for r in rules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"rule ids must be unique: {ids}")
        return rules

    @field_validator("queries")
    @classmethod
    def _unique_questions(cls, queries: tuple[VerifiedQuery, ...]) -> tuple[VerifiedQuery, ...]:
        seen = [q.question.lower() for q in queries]
        if len(seen) != len(set(seen)):
            raise ValueError("verified questions must be unique")
        return queries

    def rules_text(self) -> str:
        """The rules as the bulleted list in the SQL agent's prompt; continuation lines are indented under each."""
        return "\n".join("- " + r.text.replace("\n", "\n  ") for r in self.rules)


@cache
def load(directory: Path = SEMANTIC_DIR) -> Semantic:
    rules = yaml.safe_load((directory / "metrics.yml").read_text(encoding="utf-8"))["rules"]
    queries = yaml.safe_load((directory / "queries.yml").read_text(encoding="utf-8"))["queries"]
    return Semantic(rules=rules, queries=queries)
