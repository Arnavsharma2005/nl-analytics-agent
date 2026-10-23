"""Run the scheduled reports and alerts that are due (app/schedules.py).

    python -m app.jobs.schedules

Run hourly by .github/workflows/schedules.yml, with the same settings as the app: DATABASE_URL, APP_DATABASE_URL, a
model provider's key, and RESEND_API_KEY/EMAIL_FROM and/or TELEGRAM_BOT_TOKEN for sending. The `schedules` flag
(app/flags.py) pauses them: off for everyone, or on only for some users.
"""

import logging
import sys

from app.config import get_settings
from app.main import get_analyst, get_flags, get_notifier, get_schedules
from app.schedules import run_due


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    s = get_settings()
    flags = get_flags()
    if flags.audiences()["schedules"] == "off":
        print("Schedules are off (the SCHEDULES setting or the schedules flag); nothing to run.")
        return 0
    lines = run_due(get_schedules(), get_analyst(), get_notifier(), s.public_url,
                    allowed=lambda user: flags.on("schedules", user))
    print(f"{len(lines)} schedules were due")
    for line in lines:
        print(f"- {line}")
    return 1 if any(line.endswith("failed") for line in lines) else 0


if __name__ == "__main__":
    sys.exit(main())
