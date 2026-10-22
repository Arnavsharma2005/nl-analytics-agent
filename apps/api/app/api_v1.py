"""The /v1 API: conversations of questions with streamed answers, feedback, explanations and voice input.

/api/* stays as it is for the current web app. /v1 adds, on top of the same Analyst:
- idempotency: a message's id is the client's `Idempotency-Key`, so a retried request replays the stored answer
  instead of asking (and spending quota) again;
- a richer stream: the table as soon as the query has run, then the chart, then the summary as it is written;
- follow-up suggestions, ratings, plain-English explanations of the SQL, and speech to text.
Errors are problem+json objects (docs/errors.md), and on the stream they arrive as an `error` event.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Header, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.agents.explain import explain_sql
from app.analyst import Analyst, Answer, Chart, Done, Step, Table, Token
from app.context import Context
from app.llm import LLM
from app.messages import ConversationV1, KeyReused, Messages, MessageV1
from app.problems import Problem, from_exception
from app.transcribe import AUDIO_TYPES, MAX_BYTES, Transcriber, TranscriptionFailed
from app.usage import Visitor

log = logging.getLogger(__name__)


class AskV1(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class FeedbackV1(BaseModel):
    rating: Literal["up", "down"]
    category: Literal["wrong_data", "misunderstood", "wrong_chart", "lost_context", "other"] | None = None
    comment: str | None = Field(default=None, max_length=500)


class ExplanationV1(BaseModel):
    steps: list[str]


class TranscriptV1(BaseModel):
    text: str


class ConfigV1(BaseModel):
    turnstile_site_key: str | None
    starters: list[str]
    features: dict[str, bool]


def _sse(event: str, data) -> dict:
    return {"event": event, "data": data if isinstance(data, str) else json.dumps(data, default=str)}


def _answer_events(answer: Answer) -> list[dict]:
    """The table, chart and summary events of an answer that already exists (a replay)."""
    if answer.intent != "data_query" or answer.error:
        return []
    events = [_sse("table", {"columns": answer.columns, "rows": answer.rows, "truncated": answer.truncated})]
    if answer.chart:
        events.append(_sse("chart", answer.chart.model_dump()))
    if answer.summary:
        events.append(_sse("token", {"text": answer.summary, "replace": True}))
    return events


def v1_router(*, analyst: Callable[[], Analyst], messages: Callable[[], Messages], llm: Callable[[], LLM],
              suggester: Callable[[], Context], transcriber: Callable[[], Transcriber | None],
              identify: Callable[[Request], Visitor], spend: Callable[[Visitor], None],
              turnstile_site_key: Callable[[], str | None], starters: Callable[[], list[str]],
              sign_in: Callable[[], bool]) -> APIRouter:
    """`identify` names the visitor (raising if the bot check is missing); `spend` counts one question against
    their quota (raising when it is used up)."""
    router = APIRouter(prefix="/v1", tags=["v1"])

    def suggestions(answer: Answer) -> list[str]:
        if answer.intent != "data_query" or answer.error:
            return starters()[:3]
        asked = answer.standalone_question or answer.question
        return [q for q in suggester().related(asked) if q != answer.verified_question][:3]

    async def store(call, *args):
        """Run a message-store call off the event loop; None (and a log line) if the app database is down."""
        try:
            return await asyncio.to_thread(call, *args)
        except Exception:  # noqa: BLE001 - answering matters more than remembering the answer
            log.exception("message store unavailable")
            return None

    @router.get("/config")
    def config() -> ConfigV1:
        """What the web app needs at start-up."""
        return ConfigV1(turnstile_site_key=turnstile_site_key(), starters=starters(),
                        features={"voice": transcriber() is not None, "explain": True, "feedback": True,
                                  "sign_in": sign_in()})

    @router.post("/conversations/{conversation_id}/messages", response_model=MessageV1,
                 responses={200: {"content": {"text/event-stream": {}}}})
    async def ask(conversation_id: UUID, body: AskV1, request: Request,
                  idempotency_key: Annotated[UUID | None, Header()] = None):
        """Ask a question. With `Accept: text/event-stream` the answer streams as events: `message` (its id),
        `step` as each step finishes, `table`, `chart`, `token` (summary text: add it, or with `replace` use it
        instead of everything so far), and `done` with the whole message; or `error` with a problem object.
        Otherwise the response is the finished message as JSON."""
        visitor = identify(request)
        message_id = idempotency_key or uuid4()
        question = body.question.strip()
        store_ok = True
        try:
            state, stored = await asyncio.to_thread(messages().begin, message_id, conversation_id, visitor.owner,
                                                    question)
        except KeyReused as e:
            raise Problem(422, "idempotency_key_reused", "This Idempotency-Key was used for another request",
                          "Send a new key for each new question.") from e
        except Exception:  # noqa: BLE001 - without the store there is no replay, but the question still gets answered
            log.exception("message store unavailable")
            state, stored, store_ok = "new", None, False
        if state == "running":
            raise Problem(409, "request_in_progress", "This question is still being answered",
                          "Wait for the first request to finish.", retry_after=5)
        if state == "new":
            try:
                spend(visitor)
            except Problem:
                if store_ok:
                    await store(messages().abandon, message_id)
                raise
        # Earlier turns let a follow-up ("and last year?") be understood; without them it is asked on its own.
        history = []
        if state == "new" and store_ok:
            history = await store(messages().history, conversation_id, visitor.owner, message_id) or []

        async def finish(answer: Answer) -> MessageV1:
            follow_ups = await asyncio.to_thread(suggestions, answer)
            saved = await store(messages().finish, message_id, answer, follow_ups) if store_ok else None
            return saved or MessageV1(id=message_id, conversation_id=conversation_id, question=question,
                                      answer=answer, suggestions=follow_ups, created_at=datetime.now(UTC))

        if "text/event-stream" not in request.headers.get("accept", ""):
            if stored:
                return stored
            try:
                answer = await asyncio.to_thread(analyst().ask, question, history)
            except Exception:
                if store_ok:
                    await store(messages().abandon, message_id)
                raise
            return await finish(answer)

        async def events() -> AsyncIterator[dict]:
            yield _sse("message", {"id": str(message_id), "conversation_id": str(conversation_id)})
            if stored:
                for event in _answer_events(stored.answer):
                    yield event
                yield _sse("done", stored.model_dump_json())
                return
            try:
                async for event in analyst().stream(question, history):
                    if isinstance(event, Step):
                        yield _sse("step", {"node": event.node, "label": event.label})
                    elif isinstance(event, Table):
                        yield _sse("table", {"columns": event.columns, "rows": event.rows, "truncated": event.truncated})
                    elif isinstance(event, Chart):
                        yield _sse("chart", event.spec.model_dump())
                    elif isinstance(event, Token):
                        yield _sse("token", {"text": event.text, "replace": event.replace})
                    elif isinstance(event, Done):
                        yield _sse("done", (await finish(event.answer)).model_dump_json())
            except Exception as e:  # noqa: BLE001 - report to the client instead of dropping the stream
                if store_ok:
                    await store(messages().abandon, message_id)
                yield _sse("error", from_exception(e).body())

        return EventSourceResponse(events())

    @router.get("/conversations")
    def conversations(request: Request) -> list[ConversationV1]:
        """Your recent conversations, most recent first: when signed in, from every device and kept for longer;
        otherwise this session's."""
        return messages().conversations(identify(request).owner)

    @router.get("/conversations/{conversation_id}/messages")
    def conversation(conversation_id: UUID, request: Request) -> list[MessageV1]:
        """The answered messages of one of your conversations, oldest first, to pick it up again."""
        return messages().conversation(conversation_id, identify(request).owner)

    def owned(message_id: UUID, request: Request) -> tuple[Visitor, MessageV1]:
        visitor = identify(request)
        message = messages().get(message_id, visitor.owner)
        if message is None:
            raise Problem(404, "message_not_found", "No such message",
                          "It may have expired, or it was asked in another session.")
        return visitor, message

    @router.get("/messages/{message_id}")
    def get_message(message_id: UUID, request: Request) -> MessageV1:
        return owned(message_id, request)[1]

    @router.post("/messages/{message_id}/feedback", status_code=204)
    def feedback(message_id: UUID, body: FeedbackV1, request: Request) -> Response:
        """Rate an answer. Rating again replaces the earlier rating."""
        visitor = identify(request)
        rating = 1 if body.rating == "up" else -1
        if not messages().rate(message_id, visitor.owner, rating, body.category, body.comment):
            raise Problem(404, "message_not_found", "No such message",
                          "It may have expired, or it was asked in another session.")
        return Response(status_code=204)

    @router.post("/messages/{message_id}/explain")
    def explain(message_id: UUID, request: Request) -> ExplanationV1:
        """The answer's query as plain-English steps. Worked out once, then kept."""
        visitor, message = owned(message_id, request)
        if not message.answer.sql or message.answer.error:
            raise Problem(422, "nothing_to_explain", "This answer has no query to explain")
        if steps := messages().explanation(message_id):
            return ExplanationV1(steps=steps)
        spend(visitor)
        steps = explain_sql(llm(), message.question, message.answer.sql)
        messages().save_explanation(message_id, steps)
        return ExplanationV1(steps=steps)

    @router.post("/transcribe")
    def transcribe(request: Request, audio: Annotated[UploadFile, File()],
                   language: Annotated[str | None, Form(max_length=8)] = None) -> TranscriptV1:
        """Speech to text for a spoken question (audio/webm, ogg, mp4, mpeg, wav or m4a, up to 2 MB)."""
        visitor = identify(request)
        whisper = transcriber()
        if whisper is None:
            raise Problem(503, "transcription_unavailable", "Voice questions are not set up on this server")
        content_type = (audio.content_type or "").split(";")[0].strip().lower()
        if content_type not in AUDIO_TYPES:
            raise Problem(415, "unsupported_audio", "This audio format is not supported",
                          f"Send one of: {', '.join(sorted(AUDIO_TYPES))}.")
        data = audio.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise Problem(413, "audio_too_large", "The recording is too long", "Keep a question under a minute.")
        spend(visitor)
        try:
            return TranscriptV1(text=whisper.transcribe(data, content_type, language))
        except TranscriptionFailed as e:
            log.warning("transcription failed: %s", e)
            raise Problem(502, "transcription_failed", "The recording could not be turned into text",
                          "Try again, or type the question.") from e

    return router
