"""Feature flags that change without a redeploy, so a feature can be switched on in stages and off in a minute.

Each flag names who gets the feature: "off", "curators" (the GitHub logins in CURATORS, so you first), "users"
(anyone signed in, curators included) or "everyone". A flag with no row in app.flags follows its setting in the
environment (CONTEXT_RETRIEVAL=true is "everyone", false is "off"), so a fresh deploy behaves as configured until a
flag is set. Rows are read at most once a minute; if the database can't be read, the last rows read stay in use.

Set a flag with `python scripts/flags.py set deep_analysis users`, or from GitHub with the Flags workflow.

Only switches that are safe to flip while the app runs are flags. Uploads, API keys, the MCP server and the Telegram
bot change which routes exist, so they stay settings, and changing one restarts the app.
"""

import logging
from collections.abc import Callable
from time import monotonic
from typing import Literal, get_args

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

Audience = Literal["off", "curators", "users", "everyone"]
AUDIENCES: tuple[str, ...] = get_args(Audience)

# Each flag, with what it does. Its default is the setting of the same name.
FLAGS = {
    "context_retrieval": "Show the SQL agent verified queries and data values like the question's",
    "verified_fast_path": "Answer with a verified query outright when the question matches one",
    "check_results": "Send a query that finds nothing back to the SQL agent once",
    "check_summaries": "Check every figure in the summary against the result",
    "driver_analysis": 'Answer "why did it change?" by splitting the change in code',
    "forecasting": "Answer questions about the future from their history",
    "learned_router": "Classify questions with the small trained model first",
    "deep_analysis": "Deep Analysis reports, for signed-in users",
    "schedules": "Scheduled reports and alerts; off pauses them",
}
TTL_S = 60


class Flags:
    """`on(name, user)` says whether a flag is on for a visitor (a user id when signed in, None otherwise).
    `is_curator(user)` is asked only when a flag's audience is "curators"."""

    def __init__(self, engine: Engine, defaults: dict[str, bool], is_curator: Callable[[int], bool],
                 clock: Callable[[], float] = monotonic):
        self._engine, self._defaults, self._is_curator, self._clock = engine, defaults, is_curator, clock
        self._rows: dict[str, str] = {}
        self._read_at: float | None = None

    def audiences(self) -> dict[str, Audience]:
        """Every flag's audience now: its row, or its default."""
        now = self._clock()
        if self._read_at is None or now - self._read_at >= TTL_S:
            try:
                with self._engine.connect() as conn:
                    self._rows = dict(conn.execute(text("SELECT name, audience FROM app.flags")).all())
            except Exception as e:  # noqa: BLE001 - a flag read must never take the app down
                log.warning("could not read feature flags, keeping the last ones: %s", e)
            self._read_at = now
        return {name: self._rows.get(name) or ("everyone" if self._defaults.get(name) else "off") for name in FLAGS}

    def on(self, name: str, user: int | None) -> bool:
        audience = self.audiences()[name]
        if audience in ("everyone", "off"):
            return audience == "everyone"
        if user is None:
            return False
        return audience == "users" or self._is_curator(user)

    def switches(self, user: int | None) -> frozenset[str]:
        """The flags on for this visitor."""
        return frozenset(name for name in FLAGS if self.on(name, user))

    def set(self, name: str, audience: str | None) -> None:
        """Set who gets a flag; None removes its row, so it follows its setting again. Takes effect everywhere
        within a minute (here at once)."""
        if name not in FLAGS:
            raise ValueError(f"unknown flag {name!r}; the flags are {', '.join(FLAGS)}")
        if audience is not None and audience not in AUDIENCES:
            raise ValueError(f"unknown audience {audience!r}; use one of {', '.join(AUDIENCES)}")
        with self._engine.begin() as conn:
            if audience is None:
                conn.execute(text("DELETE FROM app.flags WHERE name = :name"), {"name": name})
            else:
                conn.execute(text("""
                    INSERT INTO app.flags (name, audience) VALUES (:name, :audience)
                    ON CONFLICT (name) DO UPDATE SET audience = excluded.audience, updated_at = now()
                """), {"name": name, "audience": audience})
        self._read_at = None
