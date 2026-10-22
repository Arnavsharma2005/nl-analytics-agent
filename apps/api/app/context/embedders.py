"""Turning questions into vectors, so that questions asked in different words can be found as similar.

Two adapters: `OpenAIEmbedder` calls an embedding model over an OpenAI-compatible API (Cloudflare Workers AI's
bge-m3 by default, on its free tier), and `LexicalEmbedder` hashes words locally. The lexical one needs no network,
so it is the fallback when the model can't be reached and the embedder used in tests.
"""

import hashlib
import math
from typing import Protocol

import httpx

from app.context.words import content_words


class Embedder(Protocol):
    name: str
    # Cosine similarities above which two questions are related (worth showing as an example) and ask the same
    # thing. Scores differ between models, so each adapter carries its own.
    related: float
    same_question: float

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One unit-length vector per text. Raises on failure; callers fall back to the lexical embedder."""
        ...


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))  # both are unit length


def _unit(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class LexicalEmbedder:
    """Content words and word pairs, hashed into a fixed-size vector. Finds rewordings that share their key words."""

    name = "lexical"
    related = 0.3
    same_question = 0.6

    def __init__(self, dimensions: int = 1024):
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        tokens = content_words(text)
        features = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        v = [0.0] * self._dimensions
        for f in features:
            v[int.from_bytes(hashlib.blake2b(f.encode(), digest_size=4).digest(), "big") % self._dimensions] += 1.0
        return _unit(v)


class OpenAIEmbedder:
    """An embedding model behind an OpenAI-compatible /embeddings endpoint."""

    related = 0.6
    same_question = 0.86

    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float = 10, post=None):
        self.name = model
        self._url = base_url.rstrip("/") + "/embeddings"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._timeout = timeout_s
        self._post = post or httpx.post

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = self._post(self._url, headers=self._headers, json={"model": self._model, "input": texts},
                       timeout=self._timeout)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        if len(data) != len(texts):
            raise ValueError(f"asked for {len(texts)} embeddings, got {len(data)}")
        return [_unit([float(x) for x in d["embedding"]]) for d in data]
