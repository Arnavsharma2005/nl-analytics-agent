"""What the analyst knows about this business beyond the schema, kept as files a data owner can edit and review.

`semantic/metrics.yml` holds what the data is (`about`) and the business rules (what revenue, orders or "last
quarter" mean here), and `semantic/queries.yml` holds verified queries: questions with SQL someone has checked by hand. CI runs every
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


class About(_Strict):
    """What the data is, as the agents are told it. The defaults suit any database; the demo's describe its store."""

    data: str = "a business database"  # for the router: "an e-commerce database"
    time_column: str = "the date column"  # named in the SQL agent's example for time series
    money: str | None = None  # how the summary writes money; unset when the data says nothing about currency
    out_of_scope: str = ("I can only answer questions about the data in this database. Ask what it contains, or "
                         "try one of the suggested questions.")


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
    about: About = About()
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


def _sections(file: Path) -> dict:
    """A YAML file's top-level sections; none when the file is missing (a self-hosted folder may have only one)."""
    if not file.exists():
        return {}
    return yaml.safe_load(file.read_text(encoding="utf-8")) or {}


@cache
def load(directory: Path | None = None) -> Semantic:
    """The rules and verified queries in `directory`: by default SEMANTIC_DIR if set (your own, when self-hosting
    on your own data), else the demo's."""
    if directory is None:
        from app.config import get_settings  # here, not at the top: config is light, but tests load this alone

        directory = get_settings().semantic_dir or SEMANTIC_DIR
    metrics = _sections(directory / "metrics.yml")
    return Semantic(about=metrics.get("about") or {}, rules=metrics.get("rules") or [],
                    queries=_sections(directory / "queries.yml").get("queries") or [])
