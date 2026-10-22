/**
 * A conversation: the questions asked on this page and their answers, built up as each answer streams in.
 *
 * `useConversation()` is the one thing the UI needs: the turns so far, `ask(question)`, `retry(turn)` and
 * `reset()`. Streaming, retries and cancelling a question when the conversation is reset all happen in here.
 */
import { useCallback, useEffect, useReducer, useRef } from "react";
import { ask as send } from "./api/ask";
import { ApiError } from "./api/http";
import type { ChartSpec, Message, StreamEvents } from "./api/types";

export interface Turn {
  key: string;
  question: string;
  status: "streaming" | "done" | "failed";
  steps: StreamEvents["step"][];
  table?: StreamEvents["table"];
  chart?: ChartSpec;
  summary: string;
  message?: Message;
  error?: ApiError;
}

type Action =
  | { type: "ask"; key: string; question: string }
  | { type: "step"; key: string; step: StreamEvents["step"] }
  | { type: "table"; key: string; table: StreamEvents["table"] }
  | { type: "chart"; key: string; chart: ChartSpec }
  | { type: "token"; key: string; token: StreamEvents["token"] }
  | { type: "restart"; key: string }
  | { type: "done"; key: string; message: Message }
  | { type: "failed"; key: string; error: ApiError }
  | { type: "reset" };

const fresh = (key: string, question: string): Turn => ({ key, question, status: "streaming", steps: [], summary: "" });

export function reducer(turns: Turn[], action: Action): Turn[] {
  if (action.type === "reset") return [];
  if (action.type === "ask") return [...turns, fresh(action.key, action.question)];
  return turns.map((t) => (t.key === action.key ? update(t, action) : t));
}

function update(t: Turn, a: Exclude<Action, { type: "ask" | "reset" }>): Turn {
  switch (a.type) {
    case "step":
      return { ...t, steps: [...t.steps, a.step] };
    case "table":
      return { ...t, table: a.table };
    case "chart":
      return { ...t, chart: a.chart };
    case "token":
      return { ...t, summary: a.token.replace ? a.token.text : t.summary + a.token.text };
    case "restart":
      return fresh(t.key, t.question);
    case "done": {
      const answer = a.message.answer;
      return {
        ...t,
        status: "done",
        message: a.message,
        summary: answer.summary ?? "",
        chart: answer.chart ?? undefined,
        table: answer.intent === "data_query" && !answer.error
          ? { columns: answer.columns, rows: answer.rows as StreamEvents["table"]["rows"], truncated: answer.truncated }
          : undefined,
      };
    }
    case "failed":
      return { ...t, status: "failed", error: a.error };
  }
}

const asApiError = (e: unknown) =>
  e instanceof ApiError ? e : new ApiError({ code: "internal_error", title: "Something went wrong", detail: String(e), status: 0 });

export function useConversation() {
  const [turns, dispatch] = useReducer(reducer, []);
  const conversation = useRef(crypto.randomUUID());
  const controller = useRef(new AbortController());

  useEffect(() => {
    controller.current = new AbortController(); // a fresh one each mount: StrictMode mounts twice in development
    return () => controller.current.abort();
  }, []);

  const run = useCallback(async (key: string, question: string) => {
    const { signal } = controller.current;
    try {
      const message = await send(conversation.current, question, {
        step: (step) => dispatch({ type: "step", key, step }),
        table: (table) => dispatch({ type: "table", key, table }),
        chart: (chart) => dispatch({ type: "chart", key, chart }),
        token: (token) => dispatch({ type: "token", key, token }),
        restart: () => dispatch({ type: "restart", key }),
      }, signal);
      if (!signal.aborted) dispatch({ type: "done", key, message });
    } catch (e) {
      if (!signal.aborted) dispatch({ type: "failed", key, error: asApiError(e) });
    }
  }, []);

  const ask = useCallback((question: string) => {
    const key = crypto.randomUUID();
    dispatch({ type: "ask", key, question });
    void run(key, question);
  }, [run]);

  /** Ask a failed question again, as a new turn at the end. */
  const retry = useCallback((turn: Turn) => ask(turn.question), [ask]);

  const reset = useCallback(() => {
    controller.current.abort();
    controller.current = new AbortController();
    conversation.current = crypto.randomUUID();
    dispatch({ type: "reset" });
  }, []);

  return { turns, ask, retry, reset, busy: turns.some((t) => t.status === "streaming") };
}
