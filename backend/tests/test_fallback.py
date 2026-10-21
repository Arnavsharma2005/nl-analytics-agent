import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm import AllProvidersFailed, FallbackLLM, Provider, classify
from app.providers import build_providers


class Answer(BaseModel):
    value: int


class HTTPError(Exception):
    def __init__(self, status_code, message="boom"):
        super().__init__(message)
        self.status_code = status_code


class Reply:
    def __init__(self, content):
        self.content = content


class FakeClient:
    """Plays back a script of replies or exceptions, one per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        item = self.script[min(self.calls, len(self.script)) - 1]
        if isinstance(item, Exception):
            raise item
        return Reply(item)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(scripts: dict, keys=None):
    providers = [Provider(n, n.title(), "https://x", "m", (keys or {}).get(n, "key")) for n in scripts]
    clients = {n: FakeClient(s) for n, s in scripts.items()}
    clock = Clock()
    llm = FallbackLLM(providers, client_factory=lambda p, t: clients[p.name], clock=clock)
    return llm, clients, clock


def test_first_healthy_provider_answers():
    llm, clients, _ = make({"a": ['{"value": 1}'], "b": ['{"value": 2}']})
    assert llm.structured(Answer, "sys", "q").value == 1
    assert clients["b"].calls == 0


def test_rate_limited_provider_falls_back_and_cools_down():
    llm, clients, clock = make({"a": [HTTPError(429, "Rate limit reached")], "b": ['{"value": 2}']})
    llm.start_log()
    assert llm.structured(Answer, "sys", "q").value == 2
    log = llm.take_log()
    assert [(c["provider"], c["ok"]) for c in log] == [("A", False), ("B", True)]
    assert log[0]["error"] == "rate limited"
    # The next call skips the cooling-down provider entirely...
    llm.structured(Answer, "sys", "q")
    assert clients["a"].calls == 1
    # ...until the cooldown expires.
    clock.now += 61
    llm.structured(Answer, "sys", "q")
    assert clients["a"].calls == 2


def test_timeouts_server_errors_and_bad_keys_all_fail_over():
    class APITimeoutError(Exception):
        pass

    llm, _, _ = make({
        "a": [APITimeoutError("Request timed out")],
        "b": [HTTPError(503)],
        "c": [HTTPError(401, "invalid api key")],
        "d": ['{"value": 4}'],
    })
    assert llm.structured(Answer, "sys", "q").value == 4
    assert [p["cooling_down_s"] > 0 for p in llm.status()] == [True, True, True, False]


def test_unusable_output_moves_on_without_cooldown():
    llm, _, _ = make({"a": ["Sure! The answer is four."], "b": ['```json\n{"value": 4}\n```']})
    assert llm.structured(Answer, "sys", "q").value == 4
    assert llm.status()[0]["cooling_down_s"] == 0


def test_providers_without_keys_are_skipped():
    llm, clients, _ = make({"a": ['{"value": 1}'], "b": ['{"value": 2}']}, keys={"a": None, "b": "k"})
    assert llm.structured(Answer, "sys", "q").value == 2
    assert clients["a"].calls == 0


def test_all_failing_raises_with_every_reason():
    llm, _, _ = make({"a": [HTTPError(429)], "b": [HTTPError(500)]})
    with pytest.raises(AllProvidersFailed, match="A: rate limited; B: unavailable"):
        llm.text("sys", "q")


def test_text_strips_thinking():
    llm, _, _ = make({"a": ["<think>hmm</think>Revenue rose 12%."]})
    assert llm.text("sys", "q") == "Revenue rose 12%."


def test_daily_quota_cools_down_longer():
    assert classify(HTTPError(429, "Rate limit reached for requests per day"))[1] == 3600
    assert classify(HTTPError(429, "Rate limit reached for requests per minute"))[1] == 60


def test_build_providers_order_keys_and_overrides():
    s = Settings(_env_file=None, llm_providers="groq,gemini,cloudflare", groq_api_key="g", google_api_key="x",
                 groq_model="qwen/qwen3.6-27b", cloudflare_api_token="t")
    providers = build_providers(s)
    assert [p.name for p in providers] == ["groq", "gemini", "cloudflare"]
    assert providers[0].model == "qwen/qwen3.6-27b" and providers[0].api_key == "g"
    assert providers[1].api_key == "x"  # GOOGLE_API_KEY works for Gemini
    assert providers[2].api_key is None  # Cloudflare needs an account id as well


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown provider"):
        build_providers(Settings(_env_file=None, llm_providers="gemini,zai"))


def test_settings_read_the_repo_root_env_file():
    from pathlib import Path

    from app.config import Settings

    root_env = Path(__file__).resolve().parents[2] / ".env"
    assert root_env in [Path(p) for p in Settings.model_config["env_file"]]
