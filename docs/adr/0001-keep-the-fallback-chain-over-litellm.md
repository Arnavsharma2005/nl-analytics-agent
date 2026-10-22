# 1. Keep our own fallback chain rather than adopting LiteLLM

Date: 2026-10-22. Status: accepted.

## Context

The v2 roadmap proposed replacing the hand-written provider fallback chain (`app/llm.py`) with the LiteLLM router,
for model tiers, per-provider budgets, cooldowns and retries. It also said to measure memory first, because the
app runs on a 512 MB free instance.

Measured on Python 3.12, in a clean environment with the app's model client already imported:

| | Resident memory after import | Installed size |
| --- | --- | --- |
| `langchain-openai` (what the app uses today) | 86 MB | |
| plus `litellm` | 184 MB (+97 MB) | 366 MB for the environment |

## Decision

Keep `FallbackLLM`. Add what the router would have given us directly:

- per-provider daily budgets (`DailyBudget` in `app/usage.py`, counted in the app database so they survive restarts),
- a distinct `daily_limit_reached` error when every budget is spent,
- model tiers next (a cheaper model for routing and charts, the strongest for SQL).

Cooldowns after rate limits, failover on timeouts, bad keys and unusable output, and the per-step call log already
exist and are tested.

## Consequences

- About 100 MB of headroom stays free for DuckDB uploads (phase I4), which matter more on this instance size.
- We maintain about 200 lines of routing code ourselves, covered by `tests/test_fallback.py` and `tests/test_usage.py`.
- If the app moves to a larger instance, or needs a provider that is not OpenAI-compatible, revisit this:
  `FallbackLLM` sits behind the small `LLM` protocol, so a LiteLLM-backed implementation can replace it without
  touching the pipeline.
