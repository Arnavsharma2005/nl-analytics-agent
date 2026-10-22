"""Finding the data values a question names, even when spelled a little differently ("Pro Laptops" -> 'Pro Laptop').

Every distinct value of the indexed text columns is split into letter trigrams, as Postgres' pg_trgm does, and
compared with each run of words in the question of about the same length.
"""

from collections import defaultdict
from dataclasses import dataclass

from app.context.words import STOPWORDS, words
from app.db import Schema


@dataclass(frozen=True)
class ValueHit:
    table: str
    column: str
    value: str
    similarity: float


def _trigrams(tokens: list[str]) -> frozenset[str]:
    grams = set()
    for t in tokens:
        padded = f"  {t} "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return frozenset(grams)


class ValueIndex:
    """The values of a schema's non-personal text columns that are too many to list in the prompt."""

    def __init__(self, schema: Schema, min_similarity: float = 0.6):
        self._min = min_similarity
        self._entries: list[tuple[str, str, str, int, frozenset[str]]] = []  # table, column, value, words, grams
        self._by_gram: dict[str, list[int]] = defaultdict(list)
        for t in schema.tables.values():
            for c in t.columns:
                if c.personal or c.values or not c.distinct_values:
                    continue  # personal data is never matched; enum-like values are in the prompt already
                for value in c.distinct_values:
                    tokens = words(value)
                    if not tokens:
                        continue
                    grams = _trigrams(tokens)
                    for g in grams:
                        self._by_gram[g].append(len(self._entries))
                    self._entries.append((t.name, c.name, value, len(tokens), grams))

    def __len__(self) -> int:
        return len(self._entries)

    def find(self, question: str, limit: int = 5) -> list[ValueHit]:
        tokens = words(question)
        windows = {
            n: [_trigrams(tokens[i:i + n]) for i in range(len(tokens) - n + 1)
                if tokens[i] not in STOPWORDS and tokens[i + n - 1] not in STOPWORDS]
            for n in range(1, 5)
        }
        candidates = {i for g in _trigrams(tokens) for i in self._by_gram.get(g, ())}
        hits = []
        for i in candidates:
            table, column, value, n, grams = self._entries[i]
            best = max((len(grams & w) / len(grams | w)
                        for size in (n - 1, n, n + 1) for w in windows.get(size, ())), default=0.0)
            if best >= self._min:
                hits.append(ValueHit(table, column, value, round(best, 3)))
        hits.sort(key=lambda h: (-h.similarity, h.table, h.column, h.value))
        # Near misses of a much closer match are other values that share words with it ('Max Smartwatch' for
        # "Ultra Smartwatch"), not values the question names.
        return [h for h in hits if h.similarity >= hits[0].similarity - 0.15][:limit]
