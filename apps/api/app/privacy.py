"""Mask personal data in query results before anyone sees them: the user, the chart and summary models, or logs.

Masking goes by value, not by column name, so it holds however the query reaches the data (aliases, CTEs, string
concatenation). Email addresses keep their first character and domain, so "how many customers use gmail" still reads
sensibly. Phone numbers are recognised only with a country code (+91 98765 43210), since a bare run of digits is
far more often an order number or ID; they keep their last two digits. Uploaded files are also checked column by
column when they arrive (app/datasets/profile.py), and personal columns are then never returned at all.
"""

import re

EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
# A + and country code, then 8 to 13 more digits with optional spaces, dashes or brackets between them.
PHONE = re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s().-]*\d){8,13}(?!\w)")


def _mask_phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "•" * (len(digits) - 2) + digits[-2:]


def mask_text(value: str) -> str:
    value = EMAIL.sub(lambda m: f"{m.group(1)}•••@{m.group(2)}", value)
    return PHONE.sub(_mask_phone, value)


def mask_rows(rows: list[list]) -> list[list]:
    """Return the rows with personal data masked in every text cell. Other values are returned unchanged."""
    return [[mask_text(v) if isinstance(v, str) else v for v in row] for row in rows]
