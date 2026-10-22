from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

from app.config import get_settings

SCHEMA = "app"

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)


def run() -> None:
    engine = create_engine(get_settings().app_db_url)
    with engine.connect() as conn:
        # The version table lives in the app schema, so the schema has to exist before Alembic looks for it.
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        conn.commit()
        context.configure(connection=conn, version_table_schema=SCHEMA)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
run()
