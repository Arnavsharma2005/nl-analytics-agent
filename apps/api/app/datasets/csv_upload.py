"""Reading an uploaded CSV file into a table the analyst can query.

The file is checked (size, that it is text, rows and columns within limits), read with DuckDB's CSV sniffer, given
column names that are plain SQL identifiers (the original headings are kept as column comments), profiled, and
stored as Parquet. DuckDB only ever runs fixed statements here; a visitor's questions run against the result in a
separate, locked-down connection (app/datasets/duck.py).
"""

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb

from app.datasets.profile import ColumnV1, profile, suggest


class UploadRejected(Exception):
    """The file can't be used. `code`, `title` and `detail` are for a problem response."""

    def __init__(self, code: str, title: str, detail: str, status: int = 422):
        super().__init__(title)
        self.code, self.title, self.detail, self.status = code, title, detail, status


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 5 * 1024 * 1024
    max_rows: int = 200_000
    max_columns: int = 60


@dataclass(frozen=True)
class Upload:
    name: str  # the file's name, as shown to the visitor
    table: str  # the table's name in SQL
    rows: int
    columns: list[ColumnV1]
    suggestions: list[str]
    parquet: bytes


RESERVED = {
    "all", "and", "any", "as", "asc", "between", "by", "case", "cast", "check", "column", "create", "default", "desc",
    "distinct", "else", "end", "except", "false", "from", "group", "having", "in", "intersect", "into", "is", "join",
    "like", "limit", "not", "null", "offset", "on", "or", "order", "select", "table", "then", "to", "true", "union",
    "user", "using", "values", "when", "where", "window", "with",
}


def identifier(text: str, fallback: str, max_length: int = 50) -> str:
    """`text` as a lowercase SQL identifier that needs no quoting: "Order Date (IST)" -> order_date_ist."""
    name = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:max_length].rstrip("_") or fallback
    if name[0].isdigit():
        name = f"c_{name}"
    return f"{name}_" if name in RESERVED else name


def _unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in names:
        n = seen.get(name, 0) + 1
        seen[name] = n
        out.append(name if n == 1 else f"{name}_{n}")
    return out


def _text(data: bytes) -> str:
    if data[:2] == b"PK" or data[:4] == b"\xd0\xcf\x11\xe0":
        raise UploadRejected("not_csv", "This looks like an Excel file",
                             "Save it as CSV (File > Save As > CSV UTF-8) and upload that.")
    if b"\x00" in data[:65536]:
        raise UploadRejected("not_csv", "This isn't a text file", "Upload a CSV file: plain text with a heading row.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


DEFAULT_LIMITS = Limits()


def read_csv(data: bytes, filename: str, limits: Limits = DEFAULT_LIMITS) -> Upload:
    """Check and read an uploaded CSV file. Raises UploadRejected with a message the visitor can act on."""
    if len(data) > limits.max_bytes:
        raise UploadRejected("file_too_large", "This file is too large",
                             f"Files can be up to {limits.max_bytes // (1024 * 1024)} MB.", status=413)
    if not data.strip():
        raise UploadRejected("empty_file", "This file is empty", "Upload a CSV file with a heading row and some data.")
    name = re.sub(r"[\x00-\x1f\"\\]", "", Path(filename or "data.csv").name)[:80] or "data.csv"
    table = identifier(Path(name).stem, "data", max_length=40)

    directory = Path(tempfile.mkdtemp(prefix="upload-"))
    try:
        source = directory / "upload.csv"
        source.write_text(_text(data), encoding="utf-8")
        with duckdb.connect(config={"threads": 1, "memory_limit": "256MB"}) as conn:
            try:
                conn.execute("CREATE TABLE __raw AS SELECT * FROM read_csv(?, header = true, sample_size = -1, "
                              "null_padding = true)", [str(source)])
            except duckdb.Error as e:
                first = str(e).strip().splitlines()[0]
                raise UploadRejected("unreadable_csv", "This file couldn't be read as CSV", first) from e
            headers = [h for h, *_ in conn.execute("DESCRIBE __raw").fetchall()]
            rows = conn.execute("SELECT COUNT(*) FROM __raw").fetchone()[0]
            if rows == 0:
                raise UploadRejected("empty_file", "This file has a heading row but no data",
                                     "Upload a CSV file with at least one row under the headings.")
            if rows > limits.max_rows:
                raise UploadRejected("too_many_rows", "This file has too many rows",
                                     f"It has {rows:,} rows; files can have up to {limits.max_rows:,}.")
            if len(headers) > limits.max_columns:
                raise UploadRejected("too_many_columns", "This file has too many columns",
                                     f"It has {len(headers)}; files can have up to {limits.max_columns}.")
            names = _unique([identifier(h, f"column_{i}") for i, h in enumerate(headers, 1)])
            if table in names:  # a bare table name in SQL can mean its whole row, so keep the two apart
                table = f"{table}_data"
            select = ", ".join(f"{_q(h)} AS {n}" for h, n in zip(headers, names, strict=True))
            conn.execute(f"CREATE TABLE {table} AS SELECT {select} FROM __raw")
            columns = profile(conn, table, dict(zip(names, headers, strict=True)))
            parquet = directory / "data.parquet"
            conn.execute(f"COPY {table} TO '{parquet}' (FORMAT parquet, COMPRESSION zstd)")
        return Upload(name=name, table=table, rows=rows, columns=columns, suggestions=suggest(columns, rows),
                      parquet=parquet.read_bytes())
    finally:
        shutil.rmtree(directory, ignore_errors=True)
