"""The Telegram bot (app/telegram.py, app/api_telegram.py): the webhook takes only Telegram's updates, each chat is
a conversation, repeats are answered once, limits are said in the chat, and charts become QuickChart images."""

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.chart import ChartSpec
from app.analyst import Analyst, Answer
from app.config import get_settings
from app.main import create_app
from app.messages import Messages
from app.problems import Problem
from app.sessions import Sessions
from app.telegram import ROWS_SHOWN, TelegramBot, TelegramClient, chart_url, reply_text, table_text

from .conftest import FakeLLM

BY_REGION = """SELECT r.name AS region, ROUND(SUM(oi.quantity * oi.unit_price)::numeric, 2) AS revenue
FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN customers c ON c.customer_id = o.customer_id
JOIN regions r ON r.region_id = c.region_id GROUP BY r.name ORDER BY revenue DESC"""


class Telegram:
    """Records what the bot sends instead of calling Telegram."""

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    def post(self, url, json, timeout):
        self.sent.append((url.rsplit("/", 1)[1], json))
        return httpx.Response(200, json={"ok": True})

    def texts(self):
        return [p["text"] for m, p in self.sent if m == "sendMessage"]

    def photos(self):
        return [p for m, p in self.sent if m == "sendPhoto"]


def update(text, chat=42, message_id=1):
    return {"update_id": message_id, "message": {"message_id": message_id, "chat": {"id": chat, "type": "private"},
                                                 "text": text}}


@pytest.fixture
def bot(db, app_tables):
    tg = Telegram()
    llm = FakeLLM(sql=[BY_REGION])
    spent = []
    limit = {"left": 99}

    def spend(visitor):
        spent.append(visitor)
        limit["left"] -= 1
        if limit["left"] < 0:
            raise Problem(429, "quota_exceeded", "You have reached the question limit for now", retry_after=1800)

    made = TelegramBot(TelegramClient("token", post=tg.post), lambda: Analyst(llm, db), lambda: Messages(app_tables),
                       spend)
    return made, tg, llm, spent, limit


def test_a_question_gets_a_summary_a_table_and_a_chart(bot, db):
    made, tg, llm, spent, _ = bot
    made.handle(update("Revenue by region"))
    assert [m for m, _ in tg.sent][0] == "sendChatAction"
    [reply] = tg.texts()
    top = db.run(BY_REGION).rows[0]
    assert "<pre>" in reply and top[0] in reply and f"{top[1]:,.2f}" in reply
    assert spent[0].session == "telegram:42" and spent[0].ip == "telegram:42"
    [photo] = tg.photos()
    assert photo["chat_id"] == 42 and photo["photo"].startswith("https://quickchart.io/chart?")


def test_follow_ups_share_the_chat_and_repeats_are_answered_once(bot):
    made, tg, llm, _, _ = bot
    made.handle(update("Revenue by region", message_id=1))
    made.handle(update("Revenue by region", message_id=1))  # Telegram retried the webhook
    assert len(tg.texts()) == 1
    standalone = lambda: [u for name, u in llm.calls if name == "Standalone"]  # noqa: E731
    assert standalone() == []
    made.handle(update("and last year?", message_id=2))
    assert len(tg.texts()) == 2 and "1. Revenue by region" in standalone()[0]  # read with the earlier question
    made.handle(update("Revenue by region", chat=7, message_id=2))  # another chat is another conversation
    assert len(standalone()) == 1

    made.handle(update("/new", message_id=3))
    assert tg.texts()[-1].startswith("Starting a new conversation")
    made.handle(update("and the year before?", message_id=4))
    assert len(standalone()) == 1  # a new conversation has no earlier questions


def test_commands_and_other_updates(bot):
    made, tg, _, spent, _ = bot
    made.handle(update("/start"))
    made.handle(update("/help@my_sales_bot"))
    assert len(tg.texts()) == 2 and "Ask me about the sales data" in tg.texts()[0]
    made.handle({"update_id": 9, "edited_message": {"text": "x"}})
    made.handle({"update_id": 10, "message": {"message_id": 5, "chat": {"id": 42}, "sticker": {}}})
    made.handle(update("hi"))
    assert tg.texts()[-1].startswith("Please ask a question") and spent == []


def test_a_used_up_limit_is_said_in_the_chat(bot):
    made, tg, _, _, limit = bot
    limit["left"] = 0
    made.handle(update("Revenue by region"))
    assert tg.texts() == ["You have reached the question limit for now. Please try again in about 30 minutes."]
    limit["left"] = 5
    made.handle(update("Revenue by region"))  # the refused message can be asked again
    assert "<pre>" in tg.texts()[-1]


def test_reply_text_escapes_and_trims():
    answer = Answer(question="q", intent="data_query", summary="<b>North</b> & co", columns=["name", "n"],
                    rows=[[f"<r{i}>", i] for i in range(ROWS_SHOWN + 3)])
    text = reply_text(answer)
    assert "&lt;b&gt;North&lt;/b&gt; &amp; co" in text and "&lt;r0&gt;" in text and "… 3 more rows" in text
    assert reply_text(Answer(question="q", intent="out_of_scope", answer="I only know <sales>.")) == \
        "I only know &lt;sales&gt;."
    lines = table_text(["a", "bb"], [[1.5, "x" * 40]]).split("\n")
    assert lines[0] == "a     bb" and lines[2].startswith("1.50  xxxx") and lines[2].endswith("…")


def test_chart_urls():
    columns, rows = ["month", "revenue"], [["2025-01", 10.123], ["2025-02", 12.0]]
    url = chart_url(ChartSpec(type="line", x="month", y=["revenue"], title="Revenue"), columns, rows)
    config = json.loads(parse_qs(urlsplit(url).query)["c"][0])
    assert config["type"] == "line" and config["data"]["labels"] == ["2025-01", "2025-02"]
    assert config["data"]["datasets"][0]["data"] == [10.12, 12.0]
    assert chart_url(ChartSpec(type="kpi", y=["revenue"], title="t"), columns, rows) is None
    assert chart_url(ChartSpec(type="bar", x="month", y=["nope"], title="t"), columns, rows) is None
    assert chart_url(ChartSpec(type="bar", x="month", y=["revenue"], series="month", title="t"), columns, rows) is None
    many = [[f"m{i}", i] for i in range(100)]
    assert chart_url(ChartSpec(type="bar", x="month", y=["revenue"], title="t"), columns, many) is None


def test_the_webhook_takes_only_telegrams_updates(db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret_value")
    get_settings.cache_clear()
    handled = []

    class Bot:
        def handle(self, u):
            handled.append(u)

    app = create_app(db_factory=lambda: db, sessions_factory=lambda: Sessions("secret", 3600),
                     quota_factory=lambda: None, github_factory=lambda: None, transcriber_factory=lambda: None,
                     telegram_factory=lambda: Bot())
    client = TestClient(app)
    try:
        assert client.post("/v1/telegram/webhook", json=update("hi")).status_code == 403
        r = client.post("/v1/telegram/webhook", json=update("Revenue"),
                        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
        assert r.status_code == 403
        r = client.post("/v1/telegram/webhook", json=update("Revenue"),
                        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret_value"})
        assert r.status_code == 200 and handled == [update("Revenue")]
    finally:
        get_settings.cache_clear()
