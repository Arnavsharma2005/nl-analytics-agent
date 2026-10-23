"""Errors to Sentry and answer traces to Langfuse, each on only when its keys are set.

Sentry (SENTRY_DSN) gets unhandled errors and every ERROR log line (a model provider failing, the message store
down, a schedule that could not send), tagged with the deploy's commit and APP_ENV. Request bodies are not sent,
nor, by Sentry's default, cookies or IP addresses. SENTRY_TRACES_SAMPLE_RATE adds performance traces for that share
of requests.

Langfuse (LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST) gets a trace of each answer on the sample data:
every step of the pipeline and every model call, with its prompt, output, timing and tokens. Answers about uploaded
files are not traced, since their rows are other people's data. LANGFUSE_SAMPLE_RATE keeps that share of traces.

Both SDKs are imported only when their keys are set, so an app without them does not pay their memory.
"""

import logging
from functools import lru_cache

from app.config import Settings

log = logging.getLogger(__name__)


def init_sentry(s: Settings) -> bool:
    """Start reporting errors to Sentry; whether it is on."""
    if not s.sentry_dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(dsn=s.sentry_dsn, environment=s.app_env, release=s.git_commit,
                    traces_sample_rate=s.sentry_traces_sample_rate, max_request_body_size="never")
    return True


class Tracing:
    """LangChain callbacks that send each answer's trace to Langfuse; none when it is not set up."""

    def __init__(self, s: Settings, span_exporter=None):
        self._client = None
        if not (s.langfuse_public_key and s.langfuse_secret_key):
            return
        from langfuse import Langfuse

        self._client = Langfuse(public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key,
                                base_url=s.langfuse_host, environment=s.app_env, release=s.git_commit,
                                sample_rate=s.langfuse_sample_rate, span_exporter=span_exporter)
        self._public_key = s.langfuse_public_key

    @property
    def on(self) -> bool:
        return self._client is not None

    def callbacks(self) -> list | None:
        if not self._client:
            return None
        from langfuse.langchain import CallbackHandler

        return [CallbackHandler(public_key=self._public_key)]

    def flush(self) -> None:
        """Send what is buffered; for jobs that exit right after answering."""
        if self._client:
            try:
                self._client.flush()
            except Exception as e:  # noqa: BLE001 - losing a trace must not fail the job
                log.warning("could not send traces to Langfuse: %s", e)


@lru_cache
def get_tracing() -> Tracing:
    from app.config import get_settings

    return Tracing(get_settings())
