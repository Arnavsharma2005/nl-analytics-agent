"""Database access: schema introspection and safe, read-only query execution."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Text columns with at most this many distinct values get their values listed in the prompt,
# so the model writes status = 'completed' rather than guessing 'Complete'.
ENUM_LIKE_MAX_DISTINCT = 12


@dataclass
class Column:
    name: str
    type: str
    comment: str | None = None
    values: list[str] | None = None
    personal: bool = False  # personal data: usable in filters and counts, never returned (see sql_guard)


@dataclass
class Table:
    name: str
    comment: str | None
    columns: list[Column]
    foreign_keys: list[tuple[str, str, str]] = field(default_factory=list)  # (column, ref_table, ref_column)


@dataclass
class Schema:
    tables: dict[str, Table]

    def column_types(self) -> dict[str, dict[str, str]]:
        return {t.name: {c.name: c.type for c in t.columns} for t in self.tables.values()}

    def personal_columns(self) -> set[tuple[str, str]]:
        return {(t.name, c.name) for t in self.tables.values() for c in t.columns if c.personal}

    def describe(self) -> str:
        """Render the schema as compact text for LLM prompts."""
        out = []
        for t in self.tables.values():
            out.append(f"TABLE {t.name}" + (f"  -- {t.comment}" if t.comment else ""))
            for c in t.columns:
                line = f"  {c.name} {c.type}"
                notes = []
                if c.comment:
                    notes.append(c.comment.rstrip("."))
                if c.personal:
                    notes.append("personal data: use it only in filters or COUNT, never select it")
                if c.values:
                    notes.append("values: " + ", ".join(repr(v) for v in c.values))
                if notes:
                    line += "  -- " + "; ".join(notes)
                out.append(line)
            for col, ref_t, ref_c in t.foreign_keys:
                out.append(f"  FOREIGN KEY ({col}) REFERENCES {ref_t}({ref_c})")
            out.append("")
        return "\n".join(out).strip()


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool


class QueryError(Exception):
    """The database rejected or failed to run a query."""


class DataSource(Protocol):
    """What the analyst needs from a database it can query."""

    def schema(self) -> Schema:
        """The tables the model may query, with comments and small value lists. Cached after the first call."""
        ...

    def explain(self, sql: str) -> None:
        """Plan a query without running it; raise QueryError with the database's message if it would fail,
        or if the plan is too expensive to run."""
        ...

    def run(self, sql: str) -> QueryResult:
        """Run a read-only query with a timeout and a row cap; raise QueryError on failure."""
        ...


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class Database:
    """A PostgreSQL data source. Queries run in read-only transactions with a statement timeout; `explain` also
    rejects plans whose estimated cost is above `max_cost` (0 turns the check off), which catches runaway joins
    before they run. `personal_columns` ("table.column") are marked as personal data in the schema."""

    def __init__(self, url: str, statement_timeout_ms: int = 10_000, max_rows: int = 500, max_cost: float = 0,
                 personal_columns: Iterable[str] = ()):
        self.engine: Engine = create_engine(url, pool_pre_ping=True)
        self.statement_timeout_ms = statement_timeout_ms
        self.max_rows = max_rows
        self.max_cost = max_cost
        self.personal_columns = {tuple(c.lower().split(".", 1)) for c in personal_columns}
        self._schema: Schema | None = None

    def schema(self) -> Schema:
        if self._schema is None:
            self._schema = self._introspect()
        return self._schema

    def _introspect(self) -> Schema:
        with self.engine.connect() as conn:
            cols = conn.execute(text("""
                SELECT c.table_name, c.column_name, c.data_type,
                       col_description(format('%I.%I', c.table_schema, c.table_name)::regclass, c.ordinal_position)
                FROM information_schema.columns c
                JOIN information_schema.tables t
                  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                ORDER BY c.table_name, c.ordinal_position
            """)).all()
            table_comments = dict(conn.execute(text("""
                SELECT relname, obj_description(oid) FROM pg_class
                WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
            """)).all())
            fks = conn.execute(text("""
                SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
            """)).all()

            tables: dict[str, Table] = {}
            for table, col, dtype, comment in cols:
                tables.setdefault(table, Table(table, table_comments.get(table), []))
                personal = (table.lower(), col.lower()) in self.personal_columns
                tables[table].columns.append(Column(col, dtype, comment, personal=personal))
            for table, col, ref_t, ref_c in fks:
                if table in tables:
                    tables[table].foreign_keys.append((col, ref_t, ref_c))

            for t in tables.values():
                for c in t.columns:
                    if c.type not in ("text", "character varying") or c.personal:
                        continue
                    values = conn.execute(text(
                        f'SELECT DISTINCT "{c.name}" FROM "{t.name}" ORDER BY 1 LIMIT {ENUM_LIKE_MAX_DISTINCT + 1}'
                    )).scalars().all()
                    if len(values) <= ENUM_LIKE_MAX_DISTINCT:
                        c.values = [v for v in values if v is not None]
        return Schema(tables)

    def _read_only(self, conn) -> None:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}"))

    def explain(self, sql: str) -> None:
        """Plan the query without running it. Raises QueryError with Postgres' message on failure."""
        with self.engine.connect() as conn:
            try:
                with conn.begin():
                    self._read_only(conn)
                    plan = conn.exec_driver_sql("EXPLAIN (FORMAT JSON) " + sql).scalar()
            except Exception as e:  # noqa: BLE001 - surface the driver's message to the SQL agent
                raise QueryError(_db_message(e)) from e
        cost = plan[0]["Plan"]["Total Cost"]
        if self.max_cost and cost > self.max_cost:
            raise QueryError(
                f"The query is too expensive to run (estimated cost {cost:,.0f}, limit {self.max_cost:,.0f}). "
                "Check that every joined table has a join condition, and filter or aggregate more."
            )

    def run(self, sql: str) -> QueryResult:
        with self.engine.connect() as conn:
            try:
                with conn.begin():
                    self._read_only(conn)
                    # A server-side cursor, so only the rows we keep are sent over the wire and held in memory.
                    stream = {"stream_results": True, "max_row_buffer": self.max_rows + 1}
                    result = conn.exec_driver_sql(sql, execution_options=stream)
                    columns = list(result.keys())
                    fetched = result.fetchmany(self.max_rows + 1)
            except Exception as e:  # noqa: BLE001
                raise QueryError(_db_message(e)) from e
        truncated = len(fetched) > self.max_rows
        rows = [[_jsonable(v) for v in row] for row in fetched[: self.max_rows]]
        return QueryResult(columns, rows, truncated)


# What we wrap around the model's SQL (EXPLAIN, or the server-side cursor's DECLARE). Postgres quotes the full
# statement in its error context, so this is cut out to show the model only its own SQL.
_WRAPPER = re.compile(r'^(LINE 1: )(EXPLAIN \(FORMAT JSON\) |DECLARE "[^"]+" CURSOR FOR )', re.M)


def _strip_wrapper(msg: str) -> str:
    match = _WRAPPER.search(msg)
    if not match:
        return msg
    cut = len(match.group(2))
    lines = msg[match.start():].split("\n")
    lines[0] = match.group(1) + lines[0][len(match.group(0)):]
    if len(lines) > 1 and lines[1].strip() == "^":
        lines[1] = lines[1][cut:]
    return msg[:match.start()] + "\n".join(lines)


def _db_message(e: Exception) -> str:
    orig = getattr(e, "orig", None)
    if orig is not None:
        diag = getattr(orig, "diag", None)
        msg = _strip_wrapper(str(orig).strip())
        hint = getattr(diag, "message_hint", None) if diag else None
        if hint and hint not in msg:
            msg += f" (hint: {hint})"
        return msg
    return str(e).strip()
