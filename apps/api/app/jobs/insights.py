"""Refresh the insights feed: find the latest complete week's notable moves and store them with headlines.

    python -m app.jobs.insights

Reads the data through DATABASE_URL and writes the feed to the app's tables (APP_DATABASE_URL, or DATABASE_URL).
Needs semantic/drivers.yml for the metrics and dimensions to scan. Headlines are written by the fast model when a
provider key is set, and as plain sentences otherwise. Run nightly by .github/workflows/insights.yml.
"""

import logging
import sys

from sqlalchemy import create_engine

from app.analysis import drivers as driver_analysis
from app.analysis.insights import headlines, scan
from app.brief import database
from app.config import get_settings
from app.db import Database
from app.insights_feed import InsightsFeed
from app.providers import build_llm, build_providers


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    s = get_settings()
    drivers = driver_analysis.load()
    if drivers is None:
        print("No drivers.yml in the semantic folder, so there are no metrics to scan.")
        return 0
    db = Database(s.database_url, s.statement_timeout_ms, personal_columns=s.personal_data_columns)
    week, findings = scan(drivers, db)
    if week is None:
        print("The data is empty; nothing to scan.")
        return 0
    llm = build_llm(s) if any(p.api_key for p in build_providers(s)) else None
    written = headlines(llm, findings, database().money)
    InsightsFeed(create_engine(s.app_db_url)).replace(week, findings, written)
    print(f"Week of {week:%d %b %Y}: {len(findings)} insights")
    for line in written:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
