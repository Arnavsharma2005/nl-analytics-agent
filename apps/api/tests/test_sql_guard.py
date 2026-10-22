import pytest

from app.sql_guard import SQLValidationError, clean_sql, validate_sql

TABLES = {"orders", "customers", "order_items"}


def test_strips_fences_and_semicolon():
    assert clean_sql("```sql\nSELECT 1;\n```") == "SELECT 1"


@pytest.mark.parametrize("sql", [
    "SELECT order_id FROM orders",
    "WITH t AS (SELECT customer_id FROM orders) SELECT customer_id FROM t",
    "SELECT d FROM generate_series(1, 3) AS d",
    "SELECT customer_id FROM orders UNION SELECT customer_id FROM customers",
    "SELECT o.order_id FROM public.orders o",
])
def test_accepts_read_only_selects(sql):
    assert validate_sql(sql, TABLES)


@pytest.mark.parametrize("sql, message", [
    ("DELETE FROM orders", "Only SELECT"),
    ("DROP TABLE orders", "Only SELECT"),
    ("UPDATE orders SET status = 'x'", "Only SELECT"),
    ("SELECT 1; DROP TABLE orders", "exactly one"),
    ("SELECT * INTO copy FROM orders", "read-only"),
    ("SELECT pg_sleep(10)", "not allowed"),
    ("SELECT set_config('a', 'b', false)", "not allowed"),
    ("SELECT usename FROM pg_catalog.pg_user", "Schema pg_catalog"),
    ("SELECT * FROM users", "Table users does not exist"),
    ("", "empty"),
])
def test_rejects(sql, message):
    with pytest.raises(SQLValidationError, match=message):
        validate_sql(sql, TABLES)
