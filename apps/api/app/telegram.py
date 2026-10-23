"""A Telegram bot: message it a question and it answers with a summary, the first rows and a chart.

Telegram posts each message to the app's webhook (app/api_telegram.py), which answers it in the background. Each
chat is one conversation, so "and last year?" is understood with the question before it. Every chat is limited
like a visitor without a sign-in: per hour, per chat (app/usage.py).

Charts are drawn by QuickChart (https://quickchart.io, free, no key): the bot sends Telegram a QuickChart URL holding
a Chart.js config built from the answer's chart, and Telegram fetches the PNG from there. So the result's figures
travel in that URL; turn charts off with TELEGRAM_CHARTS=false where that matters.
"""

import html
import json
import logging
from collections.abc import Callable
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from app.agents.chart import ChartSpec
from app.analyst import Analyst, Answer
from app.messages import Messages
from app.problems import Problem
from app.usage import Visitor

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
QUICKCHART = "https://quickchart.io/chart"
ROWS_SHOWN = 10
CHART_POINTS = 40  # more points than this make an unreadable phone-sized chart
MAX_URL = 4000
CELL = 24  # characters per table cell

HELP = ("Ask me about the sales data in plain words, for example <i>Revenue by region in 2025</i>. "
        "Follow-ups like <i>and in 2024?</i> are understood with your last questions. /new starts over.")


class TelegramClient:
    def __init__(self, token: str, post: Callable[..., httpx.Response] | None = None):
        self._base = f"{API}/bot{token}"
        self._post = post or httpx.post

    def call(self, method: str, **params) -> None:
        try:
            r = self._post(f"{self._base}/{method}", json=params, timeout=20)
            if r.status_code >= 400:
                log.warning("Telegram %s failed: %s %s", method, r.status_code, r.text[:300])
        except httpx.HTTPError as e:
            log.warning("Telegram %s failed: %s", method, e)

    def send_message(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", link_preview_options={"is_disabled": True})

    def send_photo(self, chat_id: int, url: str, caption: str) -> None:
        self.call("sendPhoto", chat_id=chat_id, photo=url, caption=caption[:1000])

    def typing(self, chat_id: int) -> None:
        self.call("sendChatAction", chat_id=chat_id, action="typing")


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:,.2f}"
    elif isinstance(value, int) and not isinstance(value, bool):
        text = f"{value:,}"
    else:
        text = str(value)
    return text if len(text) <= CELL else text[:CELL - 1] + "…"


def table_text(columns: list[str], rows: list[list]) -> str:
    """The first rows as a fixed-width table, for a <pre> block."""
    shown = [[_cell(v) for v in r] for r in rows[:ROWS_SHOWN]]
    head = [_cell(c) for c in columns]
    widths = [max(len(x) for x in col) for col in zip(head, *shown, strict=True)]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()  # noqa: E731
    out = [line(head), line(["-" * w for w in widths]), *map(line, shown)]
    if len(rows) > ROWS_SHOWN:
        out.append(f"… {len(rows) - ROWS_SHOWN} more rows")
    return "\n".join(out)


def reply_text(answer: Answer) -> str:
    """The answer as a Telegram HTML message."""
    if answer.intent != "data_query" or answer.error:
        return html.escape(answer.answer or "I could not answer that from the data. Try asking it another way.")
    parts = [html.escape(answer.summary or "Here is what the data shows.")]
    if answer.columns and answer.rows:
        parts.append(f"<pre>{html.escape(table_text(answer.columns, answer.rows))}</pre>")
    return "\n\n".join(parts)


def chart_url(spec: ChartSpec | None, columns: list[str], rows: list[list]) -> str | None:
    """A QuickChart URL drawing the answer's chart, or None when there is nothing to draw (or too much)."""
    if spec is None or spec.type not in ("bar", "line", "pie") or not spec.x or not spec.y or spec.series:
        return None
    if not rows or len(rows) > CHART_POINTS or spec.x not in columns or any(y not in columns for y in spec.y):
        return None
    xi = columns.index(spec.x)
    labels = [str(r[xi]) for r in rows]
    datasets = []
    for y in spec.y[:3]:
        yi = columns.index(y)
        values = [r[yi] if isinstance(r[yi], (int, float)) and not isinstance(r[yi], bool) else None for r in rows]
        if all(v is None for v in values):
            return None
        datasets.append({"label": y, "data": [round(v, 2) if isinstance(v, float) else v for v in values],
                         **({"fill": False} if spec.type == "line" else {})})
    config = {"type": spec.type, "data": {"labels": labels, "datasets": datasets},
              "options": {"plugins": {"title": {"display": True, "text": spec.title},
                                      "legend": {"display": len(datasets) > 1 or spec.type == "pie"}}}}
    url = f"{QUICKCHART}?w=800&h=480&bkg=white&v=4&c={quote(json.dumps(config, separators=(',', ':')))}"
    return url if len(url) <= MAX_URL else None


def conversation_of(chat_id: int, epoch: int = 0) -> UUID:
    return uuid5(NAMESPACE_URL, f"telegram:{chat_id}:{epoch}")


class TelegramBot:
    """Answers one Telegram message. `spend` counts a question against the chat's quota (raising Problem 429)."""

    def __init__(self, client: TelegramClient, analyst: Callable[[], Analyst], messages: Callable[[], Messages],
                 spend: Callable[[Visitor], None], charts: bool = True):
        self._client, self._analyst, self._messages, self._spend, self._charts = client, analyst, messages, spend, charts
        self._epochs: dict[int, int] = {}  # /new starts a fresh conversation (until a restart, which also does)

    def handle(self, update: dict) -> None:
        message = update.get("message") or {}
        chat_id, text = (message.get("chat") or {}).get("id"), (message.get("text") or "").strip()
        if not isinstance(chat_id, int) or not text or not isinstance(message.get("message_id"), int):
            return  # stickers, edits, joins and the like
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        if command in ("/start", "/help"):
            return self._client.send_message(chat_id, HELP)
        if command == "/new":
            self._epochs[chat_id] = self._epochs.get(chat_id, 0) + 1
            return self._client.send_message(chat_id, "Starting a new conversation. What would you like to know?")
        if len(text) < 3 or len(text) > 500:
            return self._client.send_message(chat_id, "Please ask a question between 3 and 500 characters long.")
        self._answer(chat_id, message["message_id"], text)

    def _answer(self, chat_id: int, message_id: int, question: str) -> None:
        owner = f"telegram:{chat_id}"
        conversation = conversation_of(chat_id, self._epochs.get(chat_id, 0))
        id = uuid5(NAMESPACE_URL, f"telegram:{chat_id}:message:{message_id}")
        store = self._messages()
        state, _ = store.begin(id, conversation, owner, question)
        if state != "new":
            return  # Telegram sent this message again: it is answered or being answered already
        try:
            self._spend(Visitor(session=owner, ip=owner))
        except Problem as e:
            store.abandon(id)
            wait = f" Please try again in about {max(1, (e.retry_after or 60) // 60)} minutes." if e.retry_after else ""
            return self._client.send_message(chat_id, html.escape(e.title) + "." + wait)
        self._client.typing(chat_id)
        try:
            answer = self._analyst().ask(question, store.history(conversation, owner, id))
        except Exception:  # noqa: BLE001 - the chat hears that it failed instead of nothing
            log.exception("telegram answer failed")
            store.abandon(id)
            return self._client.send_message(chat_id, "Something went wrong while answering. Please try again.")
        store.finish(id, answer, [])
        self._client.send_message(chat_id, reply_text(answer))
        url = chart_url(answer.chart, answer.columns, answer.rows) if self._charts and not answer.error else None
        if url:
            self._client.send_photo(chat_id, url, answer.chart.title)
