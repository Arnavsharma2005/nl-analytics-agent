"""Speech to text for voice questions, with Whisper on Groq's free tier. The key stays on the server."""

from collections.abc import Callable

import httpx

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_BYTES = 2_000_000  # about two minutes of compressed speech; a question takes seconds
AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/m4a",
               "audio/x-m4a"}


class TranscriptionFailed(Exception):
    pass


class Transcriber:
    def __init__(self, api_key: str, model: str, timeout_s: float = 30, post: Callable[..., httpx.Response] | None = None):
        self._key = api_key
        self._model = model
        self._timeout = timeout_s
        self._post = post or httpx.post

    def transcribe(self, audio: bytes, content_type: str, language: str | None = None) -> str:
        data = {"model": self._model, "response_format": "json", "temperature": "0"}
        if language:
            data["language"] = language
        extension = content_type.split("/")[-1].removeprefix("x-")
        try:
            r = self._post(URL, headers={"Authorization": f"Bearer {self._key}"}, data=data,
                           files={"file": (f"question.{extension}", audio, content_type)}, timeout=self._timeout)
            r.raise_for_status()
            return str(r.json()["text"]).strip()
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise TranscriptionFailed(str(e)[:200]) from e
