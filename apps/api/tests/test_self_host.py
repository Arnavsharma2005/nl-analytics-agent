"""Self-hosting on your own data: a read-only connection, your own semantic folder, and no sample data."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import main, semantic
from app.brief import database
from app.config import get_settings
from app.db import Database
from app.main import check_data_access

from .conftest import TEST_DB

ROLE = "self_host_reader"


@pytest.fixture
def reader(db):
    """A login role that can only SELECT, and a way to give it more."""
    admin = create_engine(TEST_DB, isolation_level="AUTOCOMMIT")
    name = make_url(TEST_DB).database

    def run(*statements: str) -> None:
        with admin.connect() as conn:
            for statement in statements:
                conn.execute(text(statement))

    run(f"DROP OWNED BY {ROLE}" if _role_exists(admin) else "SELECT 1", f"DROP ROLE IF EXISTS {ROLE}",
        f"CREATE ROLE {ROLE} LOGIN PASSWORD 'reader'", f'GRANT CONNECT ON DATABASE "{name}" TO {ROLE}',
        f"GRANT USAGE ON SCHEMA public TO {ROLE}", f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}")
    url = make_url(TEST_DB).set(username=ROLE, password="reader").render_as_string(hide_password=False)
    source = Database(url)
    yield source, run
    source.engine.dispose()
    run(f"DROP OWNED BY {ROLE}", f"DROP ROLE {ROLE}")
    admin.dispose()


def _role_exists(engine) -> bool:
    with engine.connect() as conn:
        return bool(conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}).first())


def test_a_select_only_role_has_no_write_access(reader):
    source, _ = reader
    assert source.write_access() == []


def test_write_rights_are_named(reader, db):
    source, grant = reader
    grant(f"GRANT INSERT ON orders TO {ROLE}", f"GRANT CREATE ON SCHEMA public TO {ROLE}")
    assert source.write_access() == ["it can change public.orders", "it can create tables in public"]
    assert db.write_access()[0].endswith("is a superuser")


def test_a_connection_that_can_write_stops_the_start_only_when_read_only_is_required(db, monkeypatch):
    warned = []
    monkeypatch.setattr(main.log, "warning", warned.append)
    check_data_access(db, required=False)
    assert warned and warned[0].startswith("DATABASE_URL can change data")
    with pytest.raises(RuntimeError, match="REQUIRE_READ_ONLY_DATA"):
        check_data_access(db, required=True)


def test_your_own_semantic_folder_replaces_the_demos(tmp_path, monkeypatch):
    (tmp_path / "queries.yml").write_text(
        "queries:\n  - question: How many tickets are open?\n    verified_by: me\n    verified_at: 2026-10-22\n"
        "    sql: SELECT count(*) FROM tickets WHERE status = 'open'\n    starter: true\n")
    monkeypatch.setenv("SEMANTIC_DIR", str(tmp_path))
    get_settings.cache_clear()
    semantic.load.cache_clear()
    try:
        library = semantic.load()
        assert [q.question for q in library.queries] == ["How many tickets are open?"]
        assert library.rules == ()  # no metrics.yml: no business rules
        brief = database()  # and nothing said about the store's sales or rupees
        assert brief.about == "a business database" and brief.money is None
        assert "sales" not in brief.out_of_scope and "order_date" not in brief.time_column
    finally:
        monkeypatch.delenv("SEMANTIC_DIR")
        get_settings.cache_clear()
        semantic.load.cache_clear()
    assert len(semantic.load().queries) > 1  # the demo's again


def test_your_metrics_file_says_what_the_data_is(tmp_path):
    (tmp_path / "metrics.yml").write_text(
        "about:\n  data: a helpdesk database\n  time_column: opened_at\n  money: Amounts are in euros.\n"
        "rules:\n  - id: open\n    text: Open tickets have status = 'open'.\n")
    library = semantic.load(tmp_path)
    assert library.about.data == "a helpdesk database" and library.about.time_column == "opened_at"
    assert library.about.money == "Amounts are in euros." and library.about.out_of_scope == semantic.About().out_of_scope
    assert library.rules_text() == "- Open tickets have status = 'open'."


def test_no_sample_data_is_loaded_when_it_is_off():
    env = {**os.environ, "SAMPLE_DATA": "false", "DATABASE_URL": "postgresql://nobody@127.0.0.1:1/none"}
    done = subprocess.run([sys.executable, "-m", "app.seed", "--if-empty"], env=env, capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parents[1], timeout=60)
    assert done.returncode == 0 and "not loading the sample data" in done.stdout
