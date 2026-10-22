"""Usage limits: questions per visitor and per IP address, and model calls per provider per day.

All of them are counters in fixed time windows, kept in the app database so that limits hold across restarts and
across instances. If the app database is unreachable the limits are skipped (and logged) rather than taking the
whole app down with them; the bot check and each provider's own rate limits still apply.
"""

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

HOUR = 3600
DAY = 24 * HOUR


@dataclass(frozen=True)
class Spend:
    allowed: bool
    retry_after_s: int  # until the current window ends


class Counters:
    """Counts events per key in fixed windows aligned to the epoch (so daily windows start at UTC midnight)."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._engine = engine
        self._clock = clock

    def spend(self, key: str, window_s: int, limit: int) -> Spend:
        """Count one event if `key` is still under `limit` in the current window, atomically."""
        now = self._clock()
        start = datetime.fromtimestamp(now.timestamp() // window_s * window_s, UTC)
        with self._engine.begin() as conn:
            counted = conn.execute(text("""
                INSERT INTO app.usage_counters (key, window_start, count) VALUES (:key, :start, 1)
                ON CONFLICT (key, window_start) DO UPDATE SET count = app.usage_counters.count + 1
                WHERE app.usage_counters.count < :limit
                RETURNING count
            """), {"key": key, "start": start, "limit": limit}).first()
            if random.random() < 0.002:  # now and then, drop windows nobody can be in any more
                conn.execute(text("DELETE FROM app.usage_counters WHERE window_start < :old"),
                             {"old": now - timedelta(days=2)})
        ends = start + timedelta(seconds=window_s)
        return Spend(counted is not None, max(1, int((ends - now).total_seconds())))


@dataclass(frozen=True)
class Visitor:
    """Who is asking: the signed session from the cookie, if any, the client IP address, and the signed-in user."""

    session: str | None
    ip: str
    user: int | None = None

    @property
    def owner(self) -> str | None:
        """Whose messages these are: the user's when signed in, else the session's."""
        return f"user:{self.user}" if self.user else self.session


class Quota:
    """Questions allowed per visitor session per hour and per IP address per day; a signed-in user instead has their
    own limits per hour and per day, and no IP limit (a campus or office shares one address). 0 turns a limit off."""

    def __init__(self, counters: Counters, per_session_hour: int, per_ip_day: int, per_user_hour: int = 0,
                 per_user_day: int = 0):
        self._counters = counters
        self._anonymous = [("session", HOUR, per_session_hour), ("ip", DAY, per_ip_day)]
        self._signed_in = [("user", HOUR, per_user_hour), ("user-day", DAY, per_user_day)]

    def spend(self, visitor: Visitor) -> Spend | None:
        """Count one question. Returns the exhausted window when a limit is reached, else None."""
        who_by_kind = {"session": visitor.session, "ip": visitor.ip, "user": visitor.user, "user-day": visitor.user}
        for kind, window, limit in self._signed_in if visitor.user else self._anonymous:
            who = who_by_kind[kind]
            if not limit or not who:
                continue
            try:
                result = self._counters.spend(f"{kind}:{who}", window, limit)
            except Exception:  # noqa: BLE001 - fail open; see the module docstring
                log.exception("quota check skipped: app database unavailable")
                return None
            if not result.allowed:
                return result
        return None


class DailyBudget:
    """Model calls per provider per UTC day. `spend(provider)` is False once today's budget is used."""

    def __init__(self, counters: Counters, limits: dict[str, int]):
        self._counters = counters
        self._limits = limits

    def spend(self, provider: str) -> bool:
        limit = self._limits.get(provider)
        if not limit:
            return True
        try:
            return self._counters.spend(f"llm:{provider}", DAY, limit).allowed
        except Exception:  # noqa: BLE001 - fail open; see the module docstring
            log.exception("model budget check skipped: app database unavailable")
            return True

