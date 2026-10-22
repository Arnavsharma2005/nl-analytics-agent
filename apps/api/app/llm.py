"""Chat models behind one small interface, with automatic fallback across free-tier providers.

Every provider is reached through its OpenAI-compatible endpoint. A call goes to the first provider in the
configured order that has a key and is not cooling down; if it errors, times out or returns unusable output,
the next one is tried. A provider that hits a rate limit or an outage is skipped for a while so later calls
don't wait on it again.
"""

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLM(Protocol):
    def structured(self, output: type[T], system: str, user: str) -> T: ...

    def text(self, system: str, user: str) -> str: ...


@runtime_checkable
class CallLogging(Protocol):
    """An LLM that records which provider served each call, per thread, between start_log and take_log."""

    def start_log(self) -> None: ...

    def take_log(self) -> list[dict]: ...


@dataclass
class Provider:
    name: str  # config key, e.g. "groq"
    label: str  # shown in the UI, e.g. "Groq · gpt-oss-120b"
    base_url: str
    model: str
    api_key: str | None


class AllProvidersFailed(Exception):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        detail = "; ".join(f"{e['provider']}: {e['error']}" for e in errors) or "no provider has an API key configured"
        super().__init__(f"Every model provider failed ({detail})")


class BadOutput(Exception):
    """The provider answered, but not in a shape we can use."""


def classify(error: Exception) -> tuple[str, float]:
    """Return (short reason, cooldown seconds) for a failed provider call."""
    status = getattr(error, "status_code", None)
    text = str(error).lower()
    name = type(error).__name__
    if isinstance(error, BadOutput):
        return "unusable output", 0
    if status == 429 or "rate limit" in text or "quota" in text or "resource_exhausted" in text:
        daily = any(s in text for s in ("per day", "daily", "quota", "per month", "credits"))
        return "rate limited", 3600 if daily else 60
    if status in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return "key rejected", 3600
    if status == 404 or "NotFound" in name:
        return "model not found", 3600
    if "Timeout" in name or "timed out" in text:
        return "timed out", 60
    if "Connection" in name or (status is not None and status >= 500):
        return "unavailable", 60
    return f"error ({status or name})", 0


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def parse_json(raw: str, output: type[T]) -> T:
    text = _THINK.sub("", raw).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise BadOutput("no JSON object in the reply")
    try:
        return output.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as e:
        raise BadOutput(f"reply did not match the schema: {str(e)[:160]}") from e


def _openai_client(provider: Provider, timeout: float):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=provider.model,
        base_url=provider.base_url,
        api_key=provider.api_key,
        temperature=0,
        timeout=timeout,
        max_retries=0,  # we fail over to the next provider instead of retrying a struggling one
    )


class FallbackLLM:
    def __init__(self, providers: list[Provider], timeout: float = 30, client_factory: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.providers = providers
        self.timeout = timeout
        self._factory = client_factory or _openai_client
        self._clients: dict[str, object] = {}
        self._cooldown_until: dict[str, float] = {}
        self._clock = clock
        self._lock = threading.Lock()
        self._local = threading.local()

    # --- call log, read by the graph to show which model answered each step ---------------------------
    def start_log(self) -> None:
        self._local.log = []

    def take_log(self) -> list[dict]:
        log = getattr(self._local, "log", [])
        self._local.log = []
        return log

    def _log(self, entry: dict) -> None:
        if not hasattr(self._local, "log"):
            self._local.log = []
        self._local.log.append(entry)

    # --- provider selection -----------------------------------------------------------------------------
    def available(self) -> list[Provider]:
        now = self._clock()
        return [p for p in self.providers if p.api_key and self._cooldown_until.get(p.name, 0) <= now]

    def status(self) -> list[dict]:
        now = self._clock()
        return [
            {
                "name": p.name,
                "label": p.label,
                "configured": bool(p.api_key),
                "cooling_down_s": max(0, round(self._cooldown_until.get(p.name, 0) - now)),
            }
            for p in self.providers
        ]

    def _client(self, p: Provider):
        with self._lock:
            if p.name not in self._clients:
                self._clients[p.name] = self._factory(p, self.timeout)
            return self._clients[p.name]

    def _run(self, system: str, user: str, parse: Callable[[str], object]):
        errors = []
        for p in self.available():
            start = time.perf_counter()
            try:
                reply = self._client(p).invoke([("system", system), ("human", user)])
                result = parse(_content(reply))
            except Exception as e:  # noqa: BLE001 - any failure moves on to the next provider
                reason, cooldown = classify(e)
                if cooldown:
                    with self._lock:
                        self._cooldown_until[p.name] = self._clock() + cooldown
                errors.append({"provider": p.label, "error": reason})
                self._log({"provider": p.label, "ok": False, "error": reason, "seconds": round(time.perf_counter() - start, 2)})
                continue
            self._log({"provider": p.label, "ok": True, "seconds": round(time.perf_counter() - start, 2)})
            return result
        raise AllProvidersFailed(errors)

    def structured(self, output: type[T], system: str, user: str) -> T:
        # Plain-JSON prompting works on every provider, unlike tool calling or strict schemas.
        schema = json.dumps(output.model_json_schema(), separators=(",", ":"))
        system = (
            f"{system}\n\nRespond with ONLY a JSON object, no prose and no markdown, that matches this JSON schema:\n{schema}"
        )
        return self._run(system, user, lambda raw: parse_json(raw, output))

    def text(self, system: str, user: str) -> str:
        def parse(raw: str) -> str:
            out = _THINK.sub("", raw).strip()
            if not out:
                raise BadOutput("empty reply")
            return out

        return self._run(system, user, parse)


def _content(reply) -> str:
    content = getattr(reply, "content", reply)
    if isinstance(content, list):  # some providers return content blocks
        content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)
