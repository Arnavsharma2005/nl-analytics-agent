"""The Telegram bot's webhook (app/telegram.py). Telegram signs nothing, so the webhook is registered with a secret
that Telegram sends back in a header on every update (scripts/telegram_webhook.py does it); anything else is
refused. Updates are answered in the background, because Telegram waits for the webhook's response."""

import hmac
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Header, Request

from app.problems import Problem
from app.telegram import TelegramBot


def telegram_router(*, bot: Callable[[], TelegramBot], secret: str) -> APIRouter:
    router = APIRouter(prefix="/v1/telegram", include_in_schema=False)

    @router.post("/webhook")
    async def webhook(request: Request, background: BackgroundTasks,
                      x_telegram_bot_api_secret_token: str = Header(default="")) -> dict:
        if not hmac.compare_digest(x_telegram_bot_api_secret_token.encode(), secret.encode()):
            raise Problem(403, "telegram_secret_mismatch", "This webhook only takes updates from Telegram")
        try:
            update = await request.json()
        except ValueError as e:
            raise Problem(400, "bad_update", "The update is not JSON") from e
        if isinstance(update, dict):
            background.add_task(bot().handle, update)
        return {"ok": True}

    return router
