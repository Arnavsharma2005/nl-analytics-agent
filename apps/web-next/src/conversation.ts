/**
 * A conversation: the questions asked on this page and their answers, built up as each answer streams in.
 *
 * `useConversation()` is the one thing the UI needs: the turns so far, `ask(question)`, `retry(turn)`, `reset()`,
 * `open(id, messages, subject)` to carry on an earlier conversation, and `begin(subject)` to start asking about an
 * uploaded file (or the demo data again). Streaming, retries and cancelling a question when the conversation
 * changes all happen in here.
 */
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ask as send } from "./api/ask";
import { ApiError } from "./api/http";
import type { ChartSpec, Dataset, Message, StreamEvents } from "./api/types";

/** What questions are about: the demo's sales data (null), an uploaded file, or an uploaded file since deleted. */
export type Subject = Dataset | "gone" | null;

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
  | { type: "reset" }
  | { type: "load"; turns: Turn[] };

const fresh = (key: string, question: string): Turn => ({ key, question, status: "streaming", steps: [], summary: "" });

export function reducer(turns: Turn[], action: Action): Turn[] {
  if (action.type === "reset") return [];
  if (action.type === "load") return action.turns;
  if (action.type === "ask") return [...turns, fresh(action.key, action.question)];
  return turns.map((t) => (t.key === action.key ? update(t, action) : t));
}

function update(t: Turn, a: Exclude<Action, { type: "ask" | "reset" | "load" }>): Turn {
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
    case "done":
      return answered(t, a.message);
    case "failed":
      return { ...t, status: "failed", error: a.error };
  }
}

/** A turn with its finished answer: from the end of a stream, or from an earlier conversation. */
function answered(t: Turn, message: Message): Turn {
  const answer = message.answer;
  return {
    ...t,
    status: "done",
    message,
    summary: answer.summary ?? "",
    chart: answer.chart ?? undefined,
    table: answer.intent === "data_query" && !answer.error
      ? { columns: answer.columns, rows: answer.rows as StreamEvents["table"]["rows"], truncated: answer.truncated }
      : undefined,
  };
}

const asApiError = (e: unknown) =>
  e instanceof ApiError ? e : new ApiError({ code: "internal_error", title: "Something went wrong", detail: String(e), status: 0 });

export function useConversation() {
  const [turns, dispatch] = useReducer(reducer, []);
  const conversation = useRef<string>(crypto.randomUUID());
  const [conversationId, setConversationId] = useState(conversation.current);
  const controller = useRef(new AbortController());
  const [subject, setSubject] = useState<Subject>(null);
  const datasetId = useRef<string | null>(null);

  useEffect(() => {
    controller.current = new AbortController(); // a fresh one each mount: StrictMode mounts twice in development
    return () => controller.current.abort();
  }, []);

  const run = useCallback(async (key: string, question: string) => {
    const { signal } = controller.current;
    try {
      const message = await send(conversation.current, question, datasetId.current, {
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

  /** Stop whatever is streaming and continue in conversation `id` (a new one when not given) about `about`. */
  const switchTo = useCallback((about: Subject, id: string = crypto.randomUUID()) => {
    controller.current.abort();
    controller.current = new AbortController();
    conversation.current = id;
    setConversationId(id);
    datasetId.current = about && about !== "gone" ? about.id : null;
    setSubject(about);
  }, []);

  /** Start a new conversation about the same data (the demo's, if the uploaded file is gone). */
  const reset = useCallback(() => {
    switchTo(subject === "gone" ? null : subject);
    dispatch({ type: "reset" });
  }, [switchTo, subject]);

  /** Start a new conversation about an uploaded file, or the demo data when `about` is null. */
  const begin = useCallback((about: Dataset | null) => {
    switchTo(about);
    dispatch({ type: "reset" });
  }, [switchTo]);

  /** Show an earlier conversation; questions asked next carry it on, follow-ups included. */
  const open = useCallback((id: string, messages: Message[], about: Subject) => {
    switchTo(about, id);
    dispatch({ type: "load", turns: messages.map((m) => answered(fresh(m.id, m.question), m)) });
  }, [switchTo]);

  return {
    turns, conversationId, subject, ask, retry, reset, begin, open,
    busy: turns.some((t) => t.status === "streaming"),
  };
}
