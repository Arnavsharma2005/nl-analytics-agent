"""The context for one question: verified examples like it, the data values it names, and whether a verified
query already answers it.

A verified query answers a question outright only when the two are close in meaning *and* agree on every detail
that changes the answer: the numbers and years, the direction (top or bottom), the measure (a count, a share, an
average...), the business terms, the breakdown, and the values named. Similar questions that differ in one of
those ("revenue by region in 2024" and "... in 2025") still help as examples, but never short-cut the SQL agent.
"""

import logging
import re
from dataclasses import dataclass, field

from app.context.embedders import Embedder, LexicalEmbedder, cosine
from app.context.values import ValueHit, ValueIndex
from app.context.words import contains_phrase, content_words, singular, words
from app.db import Schema
from app.semantic import Semantic, VerifiedQuery

log = logging.getLogger(__name__)

# Words that change what a question asks for, grouped by what they mean.
_CLASSES = {
    "up": "top highest most best largest biggest max maximum greatest fastest increase",
    "down": "bottom lowest least worst smallest min minimum fewest slowest decrease",
    "share": "share percentage percent proportion fraction ratio rate %",
    "average": "average mean avg typical",
    "count": "count many number",
    "total": "total sum overall",
    "running": "cumulative running",
    "growth": "growth grew grow change changed",
    "compare": "compare compared versus vs against",
    "negation": "not never no without excluding except",
    "relative": "last this previous current ytd",
    "first": "first new",
    "grouped": "each per by every",
}
_CLASS_OF = {w: name for name, ws in _CLASSES.items() for w in ws.split()}
_BREAKDOWNS = {
    "region": "region", "country": "country", "category": "category", "channel": "channel", "segment": "segment",
    "product": "product", "customer": "customer", "day": "day", "daily": "day", "week": "week", "weekly": "week",
    "month": "month", "monthly": "month", "quarter": "quarter", "quarterly": "quarter", "year": "year",
    "yearly": "year", "annual": "year", "weekend": "weekend", "weekday": "weekend", "order": "order",
    "unit": "unit", "item": "unit",
}


@dataclass(frozen=True)
class Example:
    question: str
    sql: str
    similarity: float


@dataclass(frozen=True)
class ContextPack:
    examples: tuple[Example, ...] = ()
    values: tuple[ValueHit, ...] = ()
    verified: VerifiedQuery | None = None  # answers the question outright
    embedder: str = ""

    def prompt_text(self) -> str:
        """The question-specific context for the SQL agent's prompt (empty when there is none)."""
        parts = []
        if self.examples:
            shown = "\n\n".join(f"Q: {e.question}\nSQL:\n{e.sql}" for e in self.examples)
            parts.append("Verified queries for similar questions (correct for their own question; adapt, don't "
                         f"copy):\n\n{shown}")
        if self.values:
            listed = "\n".join(f"- {v.table}.{v.column} = {v.value!r}" for v in self.values)
            parts.append(f"Values in the data that the question may refer to:\n{listed}")
        return "\n\n".join(parts)


@dataclass
class _Indexed:
    embedder: Embedder
    vectors: list[list[float]] = field(default_factory=list)


class Context:
    """Builds a `ContextPack` per question from the verified-query library and the schema's values.

    `embedder` is the preferred embedder (None: lexical only). If it fails, the lexical embedder is used for that
    question. `fast_path` turns on answering with a verified query outright.
    """

    def __init__(self, semantic: Semantic, schema: Schema, embedder: Embedder | None = None, *,
                 examples: int = 3, fast_path: bool = False):
        self._queries = list(semantic.queries)
        self._rules = [(r.id, [content_words(t) for t in r.terms]) for r in semantic.rules if r.terms]
        self._enum_values = sorted(
            {v for t in schema.tables.values() for c in t.columns if c.values and not c.personal for v in c.values},
            key=str,
        )
        self._values = ValueIndex(schema)
        self._lexical = _Indexed(LexicalEmbedder())
        self._preferred = _Indexed(embedder) if embedder else None
        self._examples = examples
        self._fast_path = fast_path
        self._signatures = [self._signature(q.question) for q in self._queries]

    def pack(self, question: str) -> ContextPack:
        values = tuple(self._values.find(question))
        if not self._queries:
            return ContextPack(values=values)
        scored, embedder = self._similarities(question)
        ranked = sorted(zip(scored, self._queries, self._signatures, strict=True), key=lambda x: -x[0])
        examples = tuple(Example(q.question, q.sql, round(s, 3))
                         for s, q, _ in ranked[: self._examples] if s >= embedder.related)
        verified = None
        if self._fast_path:
            mine = self._signature(question, values)
            verified = next((q for s, q, sig in ranked
                             if s >= embedder.same_question and sig == mine), None)
        return ContextPack(examples, values, verified, embedder.name)

    def _similarities(self, question: str) -> tuple[list[float], Embedder]:
        for index in (self._preferred, self._lexical):
            if index is None:
                continue
            try:
                if not index.vectors:
                    index.vectors = index.embedder.embed([q.question for q in self._queries])
                [mine] = index.embedder.embed([question])
            except Exception:  # noqa: BLE001 - any failure of a remote embedder: use the lexical one
                log.exception("embedder %s failed; using lexical similarity", index.embedder.name)
                continue
            return [cosine(mine, v) for v in index.vectors], index.embedder
        raise AssertionError("the lexical embedder does not fail")

    def _signature(self, question: str, values: tuple[ValueHit, ...] | None = None) -> tuple:
        """Everything about a question that changes its answer, beyond its general meaning."""
        tokens = words(question)
        content = content_words(question)
        if values is None:
            values = tuple(self._values.find(question))
        return (
            frozenset(t for t in tokens if re.fullmatch(r"\d+(\.\d+)?", t)),
            frozenset(_CLASS_OF[w] for w in map(singular, tokens) if w in _CLASS_OF) | ({"share"} if "%" in question else set()),
            frozenset(rule for rule, terms in self._rules if any(contains_phrase(content, t) for t in terms)),
            frozenset(_BREAKDOWNS[t] for t in content if t in _BREAKDOWNS),
            frozenset(v for v in self._enum_values if contains_phrase(content, content_words(v))),
            frozenset((v.table, v.column, v.value) for v in values),
        )
