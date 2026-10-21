import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.seed import ensure_database
from tests.conftest import TEST_DB


def test_ensure_database_creates_a_missing_database():
    params = conninfo_to_dict(TEST_DB.replace("postgresql+psycopg://", "postgresql://"))
    admin = make_conninfo(**{**params, "dbname": "postgres"})
    try:
        conn = psycopg.connect(admin, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL not available")
    with conn:
        conn.execute('DROP DATABASE IF EXISTS "seed test db"')
        url = make_conninfo(**{**params, "dbname": "seed test db"})
        try:
            assert ensure_database(url) is True
            assert ensure_database(url) is False
        finally:
            conn.execute('DROP DATABASE IF EXISTS "seed test db"')
