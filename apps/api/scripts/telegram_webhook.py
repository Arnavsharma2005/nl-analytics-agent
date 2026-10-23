"""Point the Telegram bot at this app: registers PUBLIC_URL/v1/telegram/webhook with TELEGRAM_WEBHOOK_SECRET.

Usage (with TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET and PUBLIC_URL set as on the server):
    python scripts/telegram_webhook.py           # register, then show what Telegram has
    python scripts/telegram_webhook.py --info    # only show it
"""

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.telegram import API  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--info", action="store_true", help="only show the current webhook")
    args = parser.parse_args(argv)
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_webhook_secret and (args.info or s.public_url)):
        print("Set TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET and PUBLIC_URL first.")
        return 2
    base = f"{API}/bot{s.telegram_bot_token}"
    if not args.info:
        r = httpx.post(f"{base}/setWebhook", timeout=20, json={
            "url": f"{s.public_url.rstrip('/')}/v1/telegram/webhook", "secret_token": s.telegram_webhook_secret,
            "allowed_updates": ["message"], "drop_pending_updates": True})
        print(r.json().get("description", r.text))
    info = httpx.get(f"{base}/getWebhookInfo", timeout=20).json().get("result", {})
    print(f"Webhook: {info.get('url') or '(none)'}; waiting updates: {info.get('pending_update_count', 0)}; "
          f"last error: {info.get('last_error_message') or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
