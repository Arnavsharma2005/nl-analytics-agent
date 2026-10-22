"""Database access: schema introspection and safe, read-only query execution."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

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


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class Database:
    def __init__(self, url: str, statement_timeout_ms: int = 10_000, max_rows: int = 500):
        self.engine: Engine = create_engine(url, pool_pre_ping=True)
        self.statement_timeout_ms = statement_timeout_ms
        self.max_rows = max_rows
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
                tables[table].columns.append(Column(col, dtype, comment))
            for table, col, ref_t, ref_c in fks:
                if table in tables:
                    tables[table].foreign_keys.append((col, ref_t, ref_c))

            for t in tables.values():
                for c in t.columns:
                    if c.type not in ("text", "character varying") or c.name == "email":
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
                    conn.exec_driver_sql("EXPLAIN " + sql)
            except Exception as e:  # noqa: BLE001 - surface the driver's message to the SQL agent
                raise QueryError(_db_message(e)) from e

    def run(self, sql: str) -> QueryResult:
        with self.engine.connect() as conn:
            try:
                with conn.begin():
                    self._read_only(conn)
                    result = conn.exec_driver_sql(sql)
                    columns = list(result.keys())
                    fetched = result.fetchmany(self.max_rows + 1)
            except Exception as e:  # noqa: BLE001
                raise QueryError(_db_message(e)) from e
        truncated = len(fetched) > self.max_rows
        rows = [[_jsonable(v) for v in row] for row in fetched[: self.max_rows]]
        return QueryResult(columns, rows, truncated)


def _db_message(e: Exception) -> str:
    orig = getattr(e, "orig", None)
    if orig is not None:
        diag = getattr(orig, "diag", None)
        msg = str(orig).strip()
        hint = getattr(diag, "message_hint", None) if diag else None
        if hint and hint not in msg:
            msg += f" (hint: {hint})"
        return msg
    return str(e).strip()
