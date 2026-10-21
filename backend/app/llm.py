"""A small interface over chat models, so agents can be tested with a scripted fake."""

import time
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLM(Protocol):
    def structured(self, output: type[T], system: str, user: str) -> T: ...

    def text(self, system: str, user: str) -> str: ...


class LangChainLLM:
    """Wraps any langchain chat model; the provider is picked by the model string."""

    def __init__(self, model: str, retries: int = 3):
        from langchain.chat_models import init_chat_model

        self.model = init_chat_model(model, temperature=0)
        self.retries = retries

    def _with_retry(self, fn):
        # Free tiers return 429/503 under load; back off rather than fail the whole question.
        for attempt in range(self.retries):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                transient = any(s in str(e) for s in ("429", "503", "overloaded", "RESOURCE_EXHAUSTED", "UNAVAILABLE"))
                if not transient or attempt == self.retries - 1:
                    raise
                time.sleep(2 ** (attempt + 1))

    def structured(self, output: type[T], system: str, user: str) -> T:
        runnable = self.model.with_structured_output(output)
        return self._with_retry(lambda: runnable.invoke([("system", system), ("human", user)]))

    def text(self, system: str, user: str) -> str:
        msg = self._with_retry(lambda: self.model.invoke([("system", system), ("human", user)]))
        content = msg.content
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return content.strip()
