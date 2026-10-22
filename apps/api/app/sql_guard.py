"""Static checks on generated SQL before it touches the database.

The guard is the first of two layers: it rejects anything that is not a single read-only SELECT over
known tables. The database's EXPLAIN (run afterwards) is the authority on columns and types.
"""

import re

import sqlglot
from sqlglot import exp

# Functions that can read files or settings, sleep, reach other servers, run dynamic SQL or change server state.
# The read-only role and transaction block writes anyway; this keeps hostile queries from even being planned.
# Every pg_* and lo_* function is blocked: analytics never needs them, and new ones appear with each release.
BLOCKED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "query_to_xml", "table_to_xml", "cursor_to_xml",
                             "schema_to_xml", "database_to_xml")
BLOCKED_FUNCTIONS = {
    "set_config", "current_setting", "version", "current_version", "inet_server_addr", "inet_server_port", "inet_client_addr",
    "txid_current", "xpath", "xmlparse",
}
WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
               exp.TruncateTable, exp.Grant, exp.Copy, exp.Set, exp.Into)


class SQLValidationError(Exception):
    pass


def clean_sql(raw: str) -> str:
    """Strip markdown fences and trailing semicolons that models like to add."""
    sql = raw.strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)```", sql, re.S | re.I)
    if fence:
        sql = fence.group(1).strip()
    return sql.rstrip().rstrip(";").strip()


def validate_sql(sql: str, known_tables: set[str], personal_columns: set[tuple[str, str]] = frozenset()) -> str:
    """Return the cleaned SQL if it is a single read-only SELECT over known tables that returns no personal data
    (`personal_columns` holds (table, column) pairs), else raise."""
    sql = clean_sql(sql)
    if not sql:
        raise SQLValidationError("The query is empty.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SQLValidationError(f"The query does not parse as PostgreSQL: {e.errors[0]['description'] if e.errors else e}") from e
    if len(statements) != 1:
        raise SQLValidationError("Return exactly one SQL statement.")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLValidationError(f"Only SELECT queries are allowed, got {tree.key.upper()}.")
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            raise SQLValidationError(f"Only read-only SELECT queries are allowed ({node.key.upper()} found).")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_FUNCTION_PREFIXES):
                raise SQLValidationError(f"Function {name} is not allowed.")
        if isinstance(node, exp.Lock):
            raise SQLValidationError("Row locks (FOR UPDATE / FOR SHARE) are not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    known = {t.lower() for t in known_tables}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name:  # e.g. generate_series(...) used as a table
            continue
        if table.db and table.db.lower() not in ("public",):
            raise SQLValidationError(f"Schema {table.db} is not available; use only the listed tables.")
        if name not in known and name not in cte_names:
            raise SQLValidationError(
                f"Table {table.name} does not exist. Available tables: {', '.join(sorted(known_tables))}."
            )
    if personal_columns:
        _check_personal_data(tree, personal_columns)
    return sql


def _check_personal_data(tree: exp.Expression, personal_columns: set[tuple[str, str]]) -> None:
    """Personal columns may filter, join, group and be counted, but never appear in what a SELECT returns.

    Checked by column name for every table the query reads, at every nesting level, so it errs on the side of
    refusing; result masking (app/privacy.py) backs it up for anything that slips through as text.
    """
    tables = {t.name.lower() for t in tree.find_all(exp.Table)}
    names = {col for table, col in personal_columns if table in tables}
    if not names:
        return
    for select in tree.find_all(exp.Select):
        # This SELECT's own FROM and JOIN tables, by alias (a table without an alias is its own alias).
        own = {t.alias_or_name.lower(): t.name.lower() for t in select.find_all(exp.Table)
               if t.find_ancestor(exp.Select) is select}
        for projection in select.expressions:
            if isinstance(projection, exp.Star):
                starred = set(own.values())
            elif isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
                starred = {own.get(projection.table.lower(), projection.table.lower())}
            else:
                starred = set()
            hidden = sorted(f"{t}.{c}" for t, c in personal_columns if t in starred)
            if hidden:
                raise SQLValidationError(
                    f"SELECT * would return personal data ({', '.join(hidden)}); list the columns you need instead."
                )
            for column in projection.find_all(exp.Column):
                if column.name.lower() in names and not _counted(column, projection):
                    raise SQLValidationError(
                        f"Column {column.name} is personal data and can't be returned. "
                        "Use it only in WHERE, JOIN or GROUP BY, or count it with COUNT(DISTINCT ...)."
                    )


def _counted(column: exp.Column, projection: exp.Expression) -> bool:
    """Whether the column sits inside a COUNT within this projection."""
    node = column
    while node is not projection and node.parent is not None:
        node = node.parent
        if isinstance(node, exp.Count):
            return True
    return False
