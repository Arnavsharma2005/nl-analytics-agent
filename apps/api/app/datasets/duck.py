"""A DuckDB data source for uploaded data: one database file per dataset, opened read-only with file and network
access switched off, a memory limit, one thread, and a timeout on every statement.

The SQL guard (app/sql_guard.py) still runs first; this is the second layer, so a query that got past the guard
still can't write, read files, reach the network, load extensions or change a setting.
"""

import math
import shutil
import tempfile
import threading
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from app.db import ENUM_LIKE_MAX_DISTINCT, LOOKUP_MAX_DISTINCT, Column, QueryError, QueryResult, Schema, Table


def _cell(value):
    """A result value as JSON can carry it."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    return str(value)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class DuckDBSource:
    dialect = "duckdb"

    def __init__(self, path: Path, *, personal: set[tuple[str, str]] = frozenset(), statement_timeout_ms: int = 10_000,
                 max_rows: int = 500, memory_limit_mb: int = 128, owns_directory: Path | None = None):
        """Open the database file at `path` read-only and locked down. `personal` holds the (table, column) pairs
        that are personal data. `owns_directory` is removed on close."""
        self.statement_timeout_ms = statement_timeout_ms
        self.max_rows = max_rows
        self._personal = set(personal)
        self._directory = owns_directory
        self._conn = duckdb.connect(str(path), read_only=True, config={
            "enable_external_access": False,
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "allow_community_extensions": False,
            "memory_limit": f"{memory_limit_mb}MB",
            "threads": 1,
            "lock_configuration": True,
        })
        self._schema: Schema | None = None

    @classmethod
    def build(cls, tables: dict[str, bytes], *, comments: dict[str, dict[str, str]] | None = None,
              **options) -> "DuckDBSource":
        """A source holding `tables` (Parquet files, by table name), in a new database file in a temporary
        directory that is removed on close. `comments` maps a table to its column comments ("" for the table's
        own). `options` are as for the constructor."""
        directory = Path(tempfile.mkdtemp(prefix="dataset-"))
        try:
            path = directory / "data.duckdb"
            with duckdb.connect(str(path)) as loader:
                for i, (name, parquet) in enumerate(tables.items()):
                    file = directory / f"{i}.parquet"
                    file.write_bytes(parquet)
                    loader.execute(f"CREATE TABLE {_quote(name)} AS SELECT * FROM read_parquet(?)", [str(file)])
                    file.unlink()
                    for column, text in (comments or {}).get(name, {}).items():
                        target = f"TABLE {_quote(name)}" if not column else f"COLUMN {_quote(name)}.{_quote(column)}"
                        loader.execute(f"COMMENT ON {target} IS '{text.replace(chr(39), chr(39) * 2)}'")
            return cls(path, owns_directory=directory, **options)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def close(self) -> None:
        self._conn.close()
        if self._directory:
            shutil.rmtree(self._directory, ignore_errors=True)

    def schema(self) -> Schema:
        if self._schema is None:
            self._schema = self._introspect()
        return self._schema

    def _introspect(self) -> Schema:
        cursor = self._conn.cursor()
        try:
            table_comments = dict(cursor.execute(
                "SELECT table_name, comment FROM duckdb_tables() WHERE schema_name = 'main' AND NOT internal "
                "AND database_name = current_database()").fetchall())
            tables: dict[str, Table] = {}
            for table, column, dtype, comment in cursor.execute("""
                SELECT table_name, column_name, data_type, comment FROM duckdb_columns()
                WHERE schema_name = 'main' AND NOT internal AND database_name = current_database()
                ORDER BY table_name, column_index
            """).fetchall():
                tables.setdefault(table, Table(table, table_comments.get(table), []))
                tables[table].columns.append(Column(column, dtype.lower(), comment,
                                                    personal=(table, column) in self._personal))
            for t in tables.values():
                for c in t.columns:
                    if c.type != "varchar" or c.personal:
                        continue
                    values = [v for (v,) in cursor.execute(
                        f"SELECT DISTINCT {_quote(c.name)} FROM {_quote(t.name)} WHERE {_quote(c.name)} IS NOT NULL "
                        f"ORDER BY 1 LIMIT {LOOKUP_MAX_DISTINCT + 1}").fetchall()]
                    if len(values) <= LOOKUP_MAX_DISTINCT:
                        c.distinct_values = values
                    if len(values) <= ENUM_LIKE_MAX_DISTINCT:
                        c.values = [v[:60] for v in values]
        finally:
            cursor.close()
        return Schema(tables)

    def _timed(self, sql: str, fetch: int | None):
        """Run `sql` on its own cursor, stopping it after the timeout. Returns (columns, rows) when fetching."""
        cursor = self._conn.cursor()
        timer = threading.Timer(self.statement_timeout_ms / 1000, cursor.interrupt)
        timer.start()
        try:
            cursor.execute(sql)
            if fetch is None:
                return None
            return [d[0] for d in cursor.description], cursor.fetchmany(fetch)
        except duckdb.InterruptException as e:
            raise QueryError(f"The query took longer than {self.statement_timeout_ms / 1000:g} seconds and was "
                             "stopped. Filter or aggregate more.") from e
        except duckdb.Error as e:
            raise QueryError(str(e).strip()) from e
        finally:
            timer.cancel()
            cursor.close()

    def explain(self, sql: str) -> None:
        """Plan the query without running it. Raises QueryError with DuckDB's message on failure."""
        self._timed("EXPLAIN " + sql, fetch=None)

    def run(self, sql: str) -> QueryResult:
        columns, fetched = self._timed(sql, fetch=self.max_rows + 1)
        truncated = len(fetched) > self.max_rows
        return QueryResult(columns, [[_cell(v) for v in row] for row in fetched[: self.max_rows]], truncated)
