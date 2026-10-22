import { ApiError, apiError, ensureSession } from "./session";
import type { AskResult, Step, TableInfo } from "./types";

export async function getExamples(): Promise<string[]> {
  const r = await fetch("/api/examples");
  return r.ok ? r.json() : [];
}

export async function getSchema(): Promise<TableInfo[]> {
  const r = await fetch("/api/schema");
  return r.ok ? r.json() : [];
}

function postQuestion(question: string, signal?: AbortSignal): Promise<Response> {
  return fetch("/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
}

/** POST a question and read the server-sent event stream (EventSource only supports GET). */
export async function askStream(question: string, onStep: (s: Step) => void, signal?: AbortSignal): Promise<AskResult> {
  await ensureSession();
  let r = await postQuestion(question, signal);
  if (r.status === 403) {
    // The session expired or was never started: run the bot check again, then retry once.
    await ensureSession(true);
    r = await postQuestion(question, signal);
  }
  if (!r.ok || !r.body) throw await apiError(r);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (!data.length) continue;
      const payload = JSON.parse(data.join("\n"));
      if (event === "step") onStep(payload);
      else if (event === "result") return payload;
      else if (event === "error") throw new ApiError(payload.error, payload.code ?? null, payload.status ?? 500);
    }
  }
  throw new Error("The stream ended before a result arrived.");
}
