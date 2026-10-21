"""Execution-accuracy comparison between a predicted and a gold query result.

A prediction is correct when it returns the same number of rows and every gold column is matched by a
distinct predicted column holding the same values (as a multiset, or as a sequence when order matters).
Column names and column order are ignored, and extra predicted columns are allowed, so
`SELECT region, revenue, order_count` still answers "revenue by region". Numbers match within 0.01
absolute or 0.01% relative, so rounding differences do not count as errors.
"""

import math
import re

_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]00:00:00(\+00:00)?$")


def _norm(v):
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = _MIDNIGHT.match(v)
        return m.group(1) if m else v.strip()
    return v


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-4, abs_tol=0.01)
    return a == b


def _sort_key(v):
    return (0, v, "") if isinstance(v, float) else (1, 0.0, "" if v is None else str(v))


def _columns_match(gold: list, pred: list, ordered: bool) -> bool:
    if not ordered:
        gold, pred = sorted(gold, key=_sort_key), sorted(pred, key=_sort_key)
    return all(_same(g, p) for g, p in zip(gold, pred))


def results_match(gold_rows: list[list], pred_rows: list[list], ordered: bool = False) -> bool:
    if len(gold_rows) != len(pred_rows):
        return False
    if not gold_rows:
        return True
    gold_cols = [[_norm(r[i]) for r in gold_rows] for i in range(len(gold_rows[0]))]
    pred_cols = [[_norm(r[i]) for r in pred_rows] for i in range(len(pred_rows[0]))]
    if len(pred_cols) < len(gold_cols):
        return False

    # Assign each gold column to a distinct predicted column (small backtracking search; results are narrow).
    def assign(i: int, used: frozenset) -> bool:
        if i == len(gold_cols):
            return True
        return any(
            j not in used and _columns_match(gold_cols[i], pred_cols[j], ordered) and assign(i + 1, used | {j})
            for j in range(len(pred_cols))
        )

    return assign(0, frozenset())
