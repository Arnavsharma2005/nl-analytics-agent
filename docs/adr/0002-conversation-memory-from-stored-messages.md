# 2. Read follow-ups from the stored messages rather than a LangGraph checkpointer

Date: 2026-10-22. Status: accepted.

## Context

I4 adds follow-up questions ("and for 2024?"). The roadmap named LangGraph's `PostgresSaver` for conversation
memory: the graph would checkpoint its state per conversation (`thread_id`) and each new question would continue
from the last checkpoint.

The `/v1` API already keeps every question and its answer in `app.messages` (for idempotent retries, feedback and
explanations), keyed by conversation and scoped to the visitor's session.

## Decision

Build the history from `app.messages` (`Messages.history`: the last three answered turns of the conversation, from
the same session) and pass it into the graph as input. A new `followup` node, run only when there is history,
rewrites the question to stand on its own; everything after it sees only the rewrite.

## Consequences

- A first question takes exactly the path it took before: no extra node, no extra model call, the same cache.
- No second store. A checkpointer would write the whole graph state (rows included) after every node, into new
  tables, on a free database with limited storage, and keep it on its own retention schedule.
- What the model sees is small and explicit: earlier questions as they were understood, their SQL and their
  summaries. That is also what the eval feeds it (`eval/conversations.yaml`), so the eval measures the app.
- Retries stay idempotent: the history is read when a question is first claimed, from answers already stored.
- If the store is down the follow-up is answered on its own rather than failing.
- A follow-up skips the answer-cache lookup (the same words can mean different things in different conversations),
  but its answer is cached under the rewritten question, so asking that directly later is a hit.
- If later work needs to pause and resume a graph mid-run (human approval of a query, long Deep Analysis runs), a
  checkpointer is the right tool for that, and can be added for those graphs without changing this.
