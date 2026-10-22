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
    "SELECT DATE_TRUNC('month', order_date) AS m, COUNT(*) FROM orders GROUP BY 1",
    "SELECT TO_CHAR(order_date, 'YYYY-MM') AS m FROM orders",
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
    ("SELECT pg_catalog.pg_sleep(1)", "not allowed"),
    ("SELECT pg_read_file('/etc/passwd')", "not allowed"),
    ("SELECT lo_import('/etc/passwd')", "not allowed"),
    ("SELECT current_setting('data_directory')", "not allowed"),
    ("SELECT version()", "not allowed"),
    ("SELECT dblink('host=evil', 'SELECT 1')", "not allowed"),
    ("SELECT query_to_xml('DELETE FROM orders', true, true, '')", "not allowed"),
    ("SELECT order_id FROM orders FOR UPDATE", "locks"),
    ("SELECT usename FROM pg_user", "Table pg_user does not exist"),
    ("COPY orders TO '/tmp/x'", "Only SELECT"),
    ("SELECT * FROM users", "Table users does not exist"),
    ("", "empty"),
])
def test_rejects(sql, message):
    with pytest.raises(SQLValidationError, match=message):
        validate_sql(sql, TABLES)


PERSONAL = {("customers", "email")}


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(DISTINCT email) AS n FROM customers",
    "SELECT COUNT(*) FROM customers WHERE email LIKE '%@gmail.com'",
    "SELECT SPLIT_PART(email, '@', 2) AS domain_hidden FROM orders",  # orders has no personal columns
    "SELECT segment, COUNT(email) FROM customers GROUP BY segment",
    "SELECT o.* FROM orders o JOIN customers c ON c.customer_id = o.customer_id",
])
def test_personal_data_can_filter_and_count(sql):
    assert validate_sql(sql, TABLES, PERSONAL)


@pytest.mark.parametrize("sql, message", [
    ("SELECT name, email FROM customers", "personal data"),
    ("SELECT * FROM customers", "SELECT \\* would return personal data"),
    ("SELECT c.* FROM customers c", "SELECT \\* would return personal data"),
    ("SELECT REPLACE(email, '@', ' at ') FROM customers", "personal data"),
    ("SELECT SPLIT_PART(c.email, '@', 1) FROM customers c", "personal data"),
    ("WITH x AS (SELECT email FROM customers) SELECT COUNT(*) FROM x", "personal data"),
    ("SELECT (SELECT MAX(email) FROM customers) AS e", "personal data"),
])
def test_personal_data_is_never_returned(sql, message):
    with pytest.raises(SQLValidationError, match=message):
        validate_sql(sql, TABLES, PERSONAL)
