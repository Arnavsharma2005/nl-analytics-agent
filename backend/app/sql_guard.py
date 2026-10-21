"""Static checks on generated SQL before it touches the database.

The guard is the first of two layers: it rejects anything that is not a single read-only SELECT over
known tables. The database's EXPLAIN (run afterwards) is the authority on columns and types.
"""

import re

import sqlglot
from sqlglot import exp

# Functions that can read files, sleep, or change server state. The read-only transaction blocks
# writes anyway; this list keeps obviously hostile queries from even being planned.
BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "lo_import", "lo_export",
    "set_config", "dblink", "dblink_exec", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "query_to_xml", "pg_advisory_lock",
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


def validate_sql(sql: str, known_tables: set[str]) -> str:
    """Return the cleaned SQL if it is a single read-only SELECT over known tables, else raise."""
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
        if isinstance(node, exp.Anonymous) and str(node.name).lower() in BLOCKED_FUNCTIONS:
            raise SQLValidationError(f"Function {node.name} is not allowed.")
        if isinstance(node, exp.Func) and node.sql_name().lower() in BLOCKED_FUNCTIONS:
            raise SQLValidationError(f"Function {node.sql_name()} is not allowed.")

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
    return sql
