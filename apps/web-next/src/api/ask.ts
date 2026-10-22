/**
 * Asking a question: POST it and read the answer as it streams.
 *
 * Every question gets an idempotency key. If the connection drops, the same request is sent again with the same
 * key, and the API replays the answer it worked out (or says it is still working on it) instead of asking twice.
 */
import { ApiError, offline, problemFrom, sleep } from "./http";
import { ensureSession } from "./session";
import type { Message, StreamEvents } from "./types";

export type Handlers = { [K in Exclude<keyof StreamEvents, "done" | "error">]?: (data: StreamEvents[K]) => void } & {
  /** The connection dropped and the request is being sent again: forget what has arrived so far. */
  restart?: () => void;
};

class StreamBroken extends Error {}

const MAX_TRIES = 3;

/** Ask `question` in conversation `conversationId`, about the uploaded file `datasetId` or else the demo data. */
export async function ask(
  conversationId: string,
  question: string,
  datasetId: string | null,
  on: Handlers,
  signal?: AbortSignal,
): Promise<Message> {
  await ensureSession();
  const key = crypto.randomUUID();
  let renewed = false;
  for (let tries = 1, waits = 0; ; ) {
    let r: Response;
    try {
      r = await fetch(`/v1/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream", "Idempotency-Key": key },
        body: JSON.stringify(datasetId ? { question, dataset_id: datasetId } : { question }),
        signal,
      });
    } catch (e) {
      if (signal?.aborted) throw e;
      if (tries++ >= MAX_TRIES) throw offline();
      await sleep(1000 * tries, signal);
      continue;
    }
    if (r.status === 403 && !renewed) {
      renewed = true; // the session expired: run the bot check again, then send the same request
      await ensureSession(true);
      continue;
    }
    if (r.status === 409 && waits++ < 30) {
      await sleep(1000 * Number(r.headers.get("retry-after") ?? 2), signal); // the first try is still being answered
      continue;
    }
    if (!r.ok || !r.body) throw await problemFrom(r);
    try {
      return await read(r.body, on);
    } catch (e) {
      if (!(e instanceof StreamBroken) || signal?.aborted || tries++ >= MAX_TRIES) throw e instanceof StreamBroken ? offline() : e;
      on.restart?.();
    }
  }
}

async function read(body: ReadableStream<Uint8Array>, on: Handlers): Promise<Message> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch {
      throw new StreamBroken();
    }
    if (chunk.done) throw new StreamBroken(); // ended without `done`
    buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (!data.length) continue;
      const payload = JSON.parse(data.join("\n"));
      if (event === "done") return payload as Message;
      if (event === "error") throw new ApiError(payload);
      (on[event as keyof Handlers] as ((d: unknown) => void) | undefined)?.(payload);
    }
  }
}
