"""Checks on an answer before it is shown: did the query find anything, and are the summary's numbers real?"""

import re
from bisect import bisect_left
from itertools import permutations

EMPTY_RESULT = (
    "The query ran but found nothing (no rows, or only empty values). Check the filters against the values listed "
    "in the schema, the date range and the join conditions. If finding nothing is the right answer, return the same "
    "query unchanged."
)


def looks_empty(rows: list[list]) -> bool:
    """No rows, or nothing but NULLs (an aggregate over no rows)."""
    return all(v is None for row in rows for v in row)


_SCALE = {"cr": 1e7, "crore": 1e7, "crores": 1e7, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "k": 1e3, "thousand": 1e3,
          "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_NUMBER = re.compile(
    r"(?<![\w.])(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<unit>%|(?:crores?|cr|lakhs?|l|k|thousand|mn?|million|bn?|billion)\b)?",
    re.IGNORECASE,
)
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_ORDINAL = re.compile(r"\b(?:q[1-4]|h[12]|\d+(?:st|nd|rd|th))\b", re.IGNORECASE)


def _mentioned(text: str) -> list[tuple[str, float, float, bool]]:
    """Numbers in `text` as (as written, value, how far off rounding could make it, is a percentage)."""
    text = _ORDINAL.sub(" ", _DATE.sub(" ", text))
    found = []
    for m in _NUMBER.finditer(text):
        raw, unit = m.group("num"), (m.group("unit") or "").lower()
        value = float(raw.replace(",", ""))
        if not unit and not ("," in raw or "." in raw) and (value <= 12 or 1990 <= value <= 2100):
            continue  # "top 5", "3 categories", a year: not figures from the result
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        scale = _SCALE.get(unit, 1.0)
        found.append((m.group(0).strip(), value * scale, 0.5 * 10 ** -decimals * scale, unit == "%"))
    return found


def _figures(rows: list[list], total_rows: int) -> tuple[list[float], list[float]]:
    """What a summary may quote: values and simple sums, differences and ratios of them; and percentages."""
    numbers = [float(v) for row in rows for v in row if isinstance(v, (int, float)) and not isinstance(v, bool)]
    columns = [[float(r[i]) for r in rows if isinstance(r[i], (int, float)) and not isinstance(r[i], bool)]
               for i in range(len(rows[0]) if rows else 0)]
    sums = [sum(c) for c in columns]  # one per column, 0 for a column of text
    plain = numbers + [s for s in sums if s] + [float(len(rows)), float(total_rows)]
    plain += [abs(a - b) for a, b in permutations(numbers, 2)]
    plain += [a / b for a, b in permutations(numbers, 2) if b]  # "2.3 times"
    percent = numbers[:]
    percent += [100 * a / b for a, b in permutations(numbers, 2) if b]  # shares
    percent += [100 * (a - b) / b for a, b in permutations(numbers, 2) if b]  # growth
    percent += [100 * v / s for c, s in zip(columns, sums, strict=True) if s for v in c]  # share of a column
    return plain, percent


def unsupported_numbers(summary: str, rows: list[list], total_rows: int) -> list[str]:
    """The figures in `summary` that are not in the result rows or simply worked out from them."""
    plain, percent = (sorted(abs(x) for x in xs) for xs in _figures(rows, total_rows))
    bad = []
    for written, value, slack, is_percent in _mentioned(summary):
        candidates = percent if is_percent else plain
        tolerance = max(slack, 0.005 * value, 0.01)
        i = bisect_left(candidates, value - tolerance)
        if i == len(candidates) or candidates[i] > value + tolerance:
            bad.append(written)
    return bad
