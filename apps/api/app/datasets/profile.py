"""What an uploaded table holds, column by column: its kind, how complete it is, its range or common values, and
whether it is personal data. Personal columns can filter and be counted but are never returned (app/sql_guard.py).

Personal data is recognised by the column's name or by its values: email addresses, phone numbers, Indian Aadhaar
and PAN numbers, passport and social security numbers, card numbers, dates of birth and street addresses. Names of
people are not treated as personal, as in the demo data, so "top customers" still lists them.
"""

import re
from typing import Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from app.privacy import EMAIL, PHONE

Kind = Literal["text", "number", "date", "datetime", "yes/no", "other"]

# By column name (after it is cleaned up: lowercase words joined by underscores).
PERSONAL_NAMES = [
    ("email address", re.compile(r"(^|_)(e_?mail|email_?address|mail_?id)($|_)")),
    ("phone number", re.compile(r"(^|_)(phone|mobile|cell|telephone|tel|whatsapp|contact_?(no|number))($|_)")),
    ("government ID", re.compile(r"(^|_)(aadhaar|aadhar|pan_?(no|number|card)?|passport|ssn|social_security|"
                                 r"voter_?id|national_?id|tax_?id|gstin)($|_)")),
    ("card number", re.compile(r"(^|_)(card_?(no|number)|credit_?card|debit_?card|cc_?number|iban|"
                               r"account_?(no|number))($|_)")),
    ("date of birth", re.compile(r"(^|_)(dob|date_?of_?birth|birth_?date|birthday)($|_)")),
    ("street address", re.compile(r"(^|_)(address|street|address_?line_?\d?)($|_)")),
]

# By value: the share of a sample of non-empty values that must match.
AADHAAR = re.compile(r"^\d{4}\s?\d{4}\s?\d{4}$")
PAN = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
CARD = re.compile(r"^(?:\d[ -]?){13,19}$")
INDIAN_MOBILE = re.compile(r"^(?:\+?91[\s-]?)?[6-9]\d{9}$")
MATCH_SHARE = 0.6
SAMPLE = 1000


class ColumnV1(BaseModel):
    """One column of an uploaded table."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    name: str = Field(description="The column's name in questions and SQL.")
    header: str = Field(description="Its heading in the file.")
    kind: Kind
    empty: int = Field(description="Rows with no value.")
    distinct: int = Field(description="Distinct values (approximate on large tables).")
    low: str | float | None = Field(default=None, description="Smallest value, for numbers and dates.")
    high: str | float | None = Field(default=None, description="Largest value, for numbers and dates.")
    examples: list[str] = Field(default_factory=list, description="Common values, for text.")
    personal: str | None = Field(default=None, description="Why it is hidden from answers, when it is personal data.")


def _kind(duck_type: str) -> Kind:
    t = duck_type.upper()
    if t.startswith(("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
                     "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL")):
        return "number"
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP"):
        return "datetime"
    if t == "BOOLEAN":
        return "yes/no"
    if t == "VARCHAR":
        return "text"
    return "other"


def _luhn(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _is_card(value: str) -> bool:
    return bool(CARD.match(value)) and _luhn(re.sub(r"\D", "", value))


VALUE_CHECKS = [
    ("email address", lambda v: bool(EMAIL.fullmatch(v))),
    ("phone number", lambda v: bool(PHONE.fullmatch(v) or INDIAN_MOBILE.match(v))),
    ("government ID", lambda v: bool(AADHAAR.match(v) or PAN.match(v))),
    ("card number", _is_card),
]


def personal_reason(name: str, values: list[str]) -> str | None:
    """Why a column is personal data, from its cleaned-up name or a sample of its values; None if it isn't."""
    for reason, pattern in PERSONAL_NAMES:
        if pattern.search(name):
            return reason
    values = [v.strip() for v in values if v and v.strip()]
    if values:
        for reason, check in VALUE_CHECKS:
            if sum(check(v) for v in values) >= MATCH_SHARE * len(values):
                return reason
    return None


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def profile(conn: duckdb.DuckDBPyConnection, table: str, headers: dict[str, str]) -> list[ColumnV1]:
    """Describe every column of `table`. `headers` maps each column to its heading in the file."""
    columns = []
    for name, duck_type, *_ in conn.execute(f"DESCRIBE {_q(table)}").fetchall():
        kind, col = _kind(duck_type), _q(name)
        empty, filled, distinct = conn.execute(
            f"SELECT COUNT(*) - COUNT({col}), COUNT({col}), approx_count_distinct({col}) FROM {_q(table)}").fetchone()
        distinct = min(distinct, filled)  # the estimate can overshoot on small tables
        low = high = None
        if kind in ("number", "date", "datetime"):
            low, high = conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {_q(table)}").fetchone()
            if kind != "number":
                low, high = (str(v) if v is not None else None for v in (low, high))
            else:
                low, high = (float(v) if v is not None else None for v in (low, high))
        sample = [str(v) for (v,) in conn.execute(
            f"SELECT {col}::VARCHAR FROM {_q(table)} WHERE {col} IS NOT NULL LIMIT {SAMPLE}").fetchall()]
        reason = personal_reason(name, sample if kind in ("text", "number") else [])
        examples = []
        if kind == "text" and not reason:
            examples = [str(v)[:60] for (v,) in conn.execute(
                f"SELECT {col} FROM {_q(table)} WHERE {col} IS NOT NULL GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT 5"
            ).fetchall()]
        columns.append(ColumnV1(name=name, header=headers.get(name, name), kind=kind, empty=empty, distinct=distinct,
                                low=None if reason else low, high=None if reason else high, examples=examples,
                                personal=reason))
    return columns


ID_LIKE = re.compile(r"(^|_)(id|code|no|number|zip|pin|pincode|postcode|year)$")


def _words(name: str) -> str:
    return name.replace("_", " ")


def suggest(columns: list[ColumnV1], rows: int) -> list[str]:
    """Three questions worth asking first, from the columns alone: no model call."""
    usable = [c for c in columns if not c.personal]
    # A number with only a few values (a rating, a quantity of 1 to 3) is more often a category than an amount.
    few = min(10, rows // 2)
    measures = [c for c in usable if c.kind == "number" and not ID_LIKE.search(c.name) and c.distinct > few]
    # The fewest groups make the clearest first chart (a city before a customer).
    groups = sorted((c for c in usable if c.kind == "text" and 2 <= c.distinct <= 30), key=lambda c: c.distinct)
    dates = [c for c in usable if c.kind in ("date", "datetime")]
    questions = []
    if measures and groups:
        questions.append(f"Total {_words(measures[0].name)} by {_words(groups[0].name)}")
    if measures and dates:
        questions.append(f"{_words(measures[0].name).capitalize()} by month")
    if groups:
        questions.append(f"How many rows are there for each {_words(groups[-1].name)}?")
    if measures:
        questions.append(f"What is the average {_words(measures[-1].name)}?")
    questions.append("What does this data contain?")
    return list(dict.fromkeys(questions))[:3]
