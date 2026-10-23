/**
 * A client for the analytics agent's /v1 API: ask questions about the data (whole or streamed), read the weekly
 * insights, and run Deep Analysis reports. Types come from the API's OpenAPI spec (src/schema.d.ts, `npm run gen`).
 *
 *   const analyst = new Analyst({ baseUrl: "https://nl-analytics-agent.onrender.com", apiKey: "aak_..." });
 *   const chat = analyst.conversation();
 *   const first = await chat.ask("Revenue by region in 2025");
 *   for await (const event of chat.stream("and in 2024?")) if (event.type === "token") process.stdout.write(event.text);
 *
 * Works in Node 22+ and browsers (anything with fetch). In a web page, use a key that lists the page's origin.
 */
import type { components } from "./schema.d.ts";

type Schemas = components["schemas"];
export type Message = Schemas["MessageV1"];
export type Answer = Schemas["Answer"];
export type ChartSpec = Schemas["ChartSpec"];
export type Insights = Schemas["InsightsV1"];
export type ReportStatus = Schemas["ReportStatusV1"];

/** What a streamed answer sends, in order: `message` first, `done` (or `error`) last. */
export type StreamEvent =
  | { type: "message"; id: string; conversation_id: string }
  | { type: "step"; node: string; label: string }
  | { type: "table"; columns: string[]; rows: unknown[][]; truncated: boolean }
  | { type: "chart"; spec: ChartSpec }
  | { type: "token"; text: string; replace: boolean }
  | { type: "done"; message: Message };

export const USER_AGENT = "nl-analytics-sdk-ts/0.1.0";

/** The API refused or failed a request. `code` is stable ("quota_exceeded", "invalid_api_key", ...). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly title: string;
  readonly detail?: string;
  /** Seconds to wait before trying again, when the API says. */
  readonly retryAfter?: number;

  constructor(status: number, code: string, title: string, detail?: string, retryAfter?: number) {
    super(`${status} ${code}: ${title}${detail ? ` (${detail})` : ""}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.title = title;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }

  static async from(r: Response): Promise<ApiError> {
    const body = (await r.json().catch(() => ({}))) as { code?: string; title?: string; detail?: string };
    const retry = Number(r.headers.get("retry-after"));
    return new ApiError(r.status, body.code ?? "http_error", body.title ?? r.statusText, body.detail, retry > 0 ? retry : undefined);
  }
}

export interface Options {
  /** The app's address, e.g. "https://nl-analytics-agent.onrender.com". */
  baseUrl: string;
  /** Made at /keys on the web app. Without one, a browser on the app's own site uses its session cookie. */
  apiKey?: string;
  /** Replaces the global fetch (for tests or proxies). */
  fetch?: typeof fetch;
}

export interface AskOptions {
  conversationId?: string;
  datasetId?: string;
  /** The same key with the same conversation returns the first answer instead of asking twice. */
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export class Analyst {
  private readonly base: string;
  private readonly headers: Record<string, string>;
  private readonly fetch: typeof fetch;

  constructor(options: Options) {
    let base = options.baseUrl;
    while (base.endsWith("/")) base = base.slice(0, -1);
    this.base = base;
    this.headers = options.apiKey ? { Authorization: `Bearer ${options.apiKey}` } : {};
    if (typeof window === "undefined") this.headers["User-Agent"] = USER_AGENT; // browsers set their own
    this.fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  private async request(method: string, path: string, init: { body?: unknown; headers?: Record<string, string>; signal?: AbortSignal } = {}): Promise<Response> {
    const r = await this.fetch(this.base + path, {
      method,
      headers: { Accept: "application/json", ...(init.body === undefined ? {} : { "Content-Type": "application/json" }), ...this.headers, ...init.headers },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      credentials: this.headers.Authorization ? "omit" : "include",
      signal: init.signal,
    });
    if (!r.ok) throw await ApiError.from(r);
    return r;
  }

  private ask_(question: string, o: AskOptions, accept: string): Promise<Response> {
    const body: Schemas["AskV1"] = { question, ...(o.datasetId ? { dataset_id: o.datasetId } : {}) };
    return this.request("POST", `/v1/conversations/${o.conversationId ?? crypto.randomUUID()}/messages`, {
      body,
      headers: { Accept: accept, "Idempotency-Key": o.idempotencyKey ?? crypto.randomUUID() },
      signal: o.signal,
    });
  }

  /** Ask one question and wait for the whole answer. */
  async ask(question: string, options: AskOptions = {}): Promise<Message> {
    return (await this.ask_(question, options, "application/json")).json() as Promise<Message>;
  }

  /** Ask one question and get the answer as it is made: steps, the table, the chart and the summary's text. */
  async *stream(question: string, options: AskOptions = {}): AsyncGenerator<StreamEvent> {
    const r = await this.ask_(question, options, "text/event-stream");
    for await (const { event, data } of serverSentEvents(r)) {
      const parsed = JSON.parse(data);
      if (event === "error") throw new ApiError(parsed.status ?? 500, parsed.code ?? "stream_error", parsed.title ?? "The answer failed", parsed.detail);
      if (event === "done") yield { type: "done", message: parsed as Message };
      else if (event === "chart") yield { type: "chart", spec: parsed as ChartSpec };
      else if (event === "message" || event === "step" || event === "table" || event === "token") yield { type: event, ...parsed };
    }
  }

  /** A conversation, so follow-up questions are understood with the earlier ones. */
  conversation(id: string = crypto.randomUUID()): Conversation {
    return new Conversation(this, id);
  }

  /** The latest week's most notable moves in the data, found nightly. */
  async insights(): Promise<Insights> {
    return (await this.request("GET", "/v1/insights")).json() as Promise<Insights>;
  }

  /** Start a Deep Analysis report on a broad question (signed-in users only, a few a day). */
  async startReport(question: string): Promise<ReportStatus> {
    return (await this.request("POST", "/v1/reports", { body: { question } })).json() as Promise<ReportStatus>;
  }

  async report(id: string): Promise<ReportStatus> {
    return (await this.request("GET", `/v1/reports/${id}`)).json() as Promise<ReportStatus>;
  }

  /** Poll a report until it is done or failed. */
  async waitForReport(id: string, { every = 3000, timeout = 600_000 } = {}): Promise<ReportStatus> {
    const deadline = Date.now() + timeout;
    for (;;) {
      const status = await this.report(id);
      if (status.status !== "running") return status;
      if (Date.now() > deadline) throw new Error(`report ${id} is still running after ${Math.round(timeout / 1000)}s`);
      await new Promise((resolve) => setTimeout(resolve, every));
    }
  }
}

export class Conversation {
  private readonly analyst: Analyst;
  readonly id: string;

  constructor(analyst: Analyst, id: string) {
    this.analyst = analyst;
    this.id = id;
  }

  ask(question: string, options: Omit<AskOptions, "conversationId"> = {}): Promise<Message> {
    return this.analyst.ask(question, { ...options, conversationId: this.id });
  }

  stream(question: string, options: Omit<AskOptions, "conversationId"> = {}): AsyncGenerator<StreamEvent> {
    return this.analyst.stream(question, { ...options, conversationId: this.id });
  }
}

/** The events of a text/event-stream response body. */
export async function* serverSentEvents(r: Response): AsyncGenerator<{ event: string; data: string }> {
  if (!r.body) return;
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer = (buffer + (value ?? "")).replace(/\r\n?/g, "\n");
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
      if (data.length) yield { event, data: data.join("\n") };
    }
    if (done) return;
  }
}
