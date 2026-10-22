import type { AskResult, Step, TableInfo } from "./types";

export async function getExamples(): Promise<string[]> {
  const r = await fetch("/api/examples");
  return r.ok ? r.json() : [];
}

export async function getSchema(): Promise<TableInfo[]> {
  const r = await fetch("/api/schema");
  return r.ok ? r.json() : [];
}

/** POST a question and read the server-sent event stream (EventSource only supports GET). */
export async function askStream(question: string, onStep: (s: Step) => void, signal?: AbortSignal): Promise<AskResult> {
  const r = await fetch("/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!r.ok || !r.body) {
    // Errors are problem details (docs/errors.md); show the human-readable part.
    const problem = await r.json().catch(() => null);
    throw new Error(problem?.detail ?? problem?.title ?? `Request failed (${r.status})`);
  }
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
      else if (event === "error") throw new Error(payload.error);
    }
  }
  throw new Error("The stream ended before a result arrived.");
}
