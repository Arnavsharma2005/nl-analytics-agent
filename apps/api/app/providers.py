"""The free-tier providers the agent can fall back across, all via OpenAI-compatible endpoints.

Default order runs from the strongest model for SQL to the weakest. Change it with LLM_PROVIDERS, and any
model with <NAME>_MODEL (e.g. GROQ_MODEL=qwen/qwen3.6-27b). Providers without a key are skipped.
"""

from collections.abc import Callable

from app.config import Settings
from app.llm import FallbackLLM, Provider

# name: (display name, base url, default model)
CATALOG = {
    "gemini": ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-flash-latest"),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "nvidia/nemotron-3-super-120b-a12b:free"),
    "mistral": ("Mistral", "https://api.mistral.ai/v1", "mistral-large-latest"),
    "cloudflare": ("Cloudflare", "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
}


def build_providers(s: Settings) -> list[Provider]:
    keys = {
        "gemini": s.gemini_api_key or s.google_api_key,
        "groq": s.groq_api_key,
        "openrouter": s.openrouter_api_key,
        "mistral": s.mistral_api_key,
        # Cloudflare's URL contains the account id, so the provider only counts as configured with both.
        "cloudflare": s.cloudflare_api_token if s.cloudflare_account_id else None,
    }
    providers = []
    for name in [n.strip().lower() for n in s.llm_providers.split(",") if n.strip()]:
        if name not in CATALOG:
            raise ValueError(f"Unknown provider {name!r} in LLM_PROVIDERS; choose from {', '.join(CATALOG)}")
        label, base_url, default_model = CATALOG[name]
        model = getattr(s, f"{name}_model") or default_model
        fast_model = getattr(s, f"{name}_fast_model")
        providers.append(Provider(
            name=name,
            label=f"{label} · {model.removeprefix('@cf/')}",
            base_url=base_url.format(account_id=s.cloudflare_account_id or ""),
            model=model,
            api_key=keys[name],
            fast_model=fast_model,
            fast_label=f"{label} · {fast_model.removeprefix('@cf/')}" if fast_model else None,
        ))
    return providers


def build_llm(s: Settings, budget: Callable[[str], bool] | None = None) -> FallbackLLM:
    return FallbackLLM(build_providers(s), timeout=s.llm_timeout_s, budget=budget)
