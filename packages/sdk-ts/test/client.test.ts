import assert from "node:assert/strict";
import { test } from "node:test";
import { Analyst, ApiError, type StreamEvent } from "../src/index.ts";

type Call = { url: string; init: RequestInit };

function fake(...responses: Response[]) {
  const calls: Call[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return responses.shift()!;
  }) as typeof globalThis.fetch;
  return { calls, fetch };
}

const MESSAGE = { id: "m1", conversation_id: "c1", question: "How many orders?", answer: { rows: [[42]] } };

test("ask sends the key, the question and an idempotency key, and returns the message", async () => {
  const { calls, fetch } = fake(Response.json(MESSAGE));
  const analyst = new Analyst({ baseUrl: "https://example.test/", apiKey: "aak_test", fetch });
  const message = await analyst.ask("How many orders?", { conversationId: "c1" });
  assert.equal(message.id, "m1");
  const { url, init } = calls[0];
  assert.equal(url, "https://example.test/v1/conversations/c1/messages");
  const headers = init.headers as Record<string, string>;
  assert.equal(headers.Authorization, "Bearer aak_test");
  assert.equal(headers.Accept, "application/json");
  assert.match(headers["Idempotency-Key"], /^[0-9a-f-]{36}$/);
  assert.equal(init.credentials, "omit");
  assert.deepEqual(JSON.parse(init.body as string), { question: "How many orders?" });
});

test("a conversation keeps its id across questions", async () => {
  const { calls, fetch } = fake(Response.json(MESSAGE), Response.json(MESSAGE));
  const chat = new Analyst({ baseUrl: "https://example.test", fetch }).conversation();
  await chat.ask("Revenue by region");
  await chat.ask("and last year?");
  assert.equal(calls[0].url, calls[1].url);
  assert.ok(calls[0].url.includes(chat.id));
  assert.equal(calls[0].init.credentials, "include"); // no key: the site's own session cookie
});

test("a refusal becomes an ApiError with the API's code and retry time", async () => {
  const { fetch } = fake(new Response(JSON.stringify({ code: "quota_exceeded", title: "Limit reached", status: 429 }), {
    status: 429, headers: { "content-type": "application/problem+json", "retry-after": "30" },
  }));
  const error = await new Analyst({ baseUrl: "https://example.test", fetch }).ask("Revenue?").catch((e) => e);
  assert.ok(error instanceof ApiError);
  assert.equal(error.code, "quota_exceeded");
  assert.equal(error.retryAfter, 30);
});

function sse(text: string, chunk = 7): Response {
  const bytes = new TextEncoder().encode(text);
  const body = new ReadableStream({
    start(controller) {
      for (let i = 0; i < bytes.length; i += chunk) controller.enqueue(bytes.slice(i, i + chunk));
      controller.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream" } });
}

test("a streamed answer arrives as typed events, however the bytes are split", async () => {
  const { calls, fetch } = fake(sse(
    'event: message\r\ndata: {"id": "m1", "conversation_id": "c1"}\r\n\r\n' +
    'event: step\ndata: {"node": "sql", "label": "Writing the query"}\n\n' +
    'event: token\ndata: {"text": "North ", "replace": false}\n\n' +
    `event: done\ndata: ${JSON.stringify(MESSAGE)}\n\n`,
  ));
  const events: StreamEvent[] = [];
  for await (const event of new Analyst({ baseUrl: "https://example.test", fetch }).stream("Revenue?")) events.push(event);
  assert.deepEqual(events.map((e) => e.type), ["message", "step", "token", "done"]);
  assert.deepEqual(events[2], { type: "token", text: "North ", replace: false });
  assert.equal((events[3] as { message: { id: string } }).message.id, "m1");
  assert.equal((calls[0].init.headers as Record<string, string>).Accept, "text/event-stream");
});

test("an error event ends the stream with an ApiError", async () => {
  const { fetch } = fake(sse('event: message\ndata: {"id": "m1", "conversation_id": "c1"}\n\nevent: error\ndata: {"code": "llm_unavailable", "title": "No model answered", "status": 503}\n\n'));
  const seen: string[] = [];
  await assert.rejects(async () => {
    for await (const event of new Analyst({ baseUrl: "https://example.test", fetch }).stream("Revenue?")) seen.push(event.type);
  }, (e: unknown) => e instanceof ApiError && e.code === "llm_unavailable" && e.status === 503);
  assert.deepEqual(seen, ["message"]);
});

test("waiting for a report polls until it is no longer running", async () => {
  const running = { id: "r1", status: "running" }, done = { id: "r1", status: "done" };
  const { calls, fetch } = fake(Response.json(running), Response.json(running), Response.json(done));
  const status = await new Analyst({ baseUrl: "https://example.test", fetch }).waitForReport("r1", { every: 0 });
  assert.equal(status.status, "done");
  assert.equal(calls.length, 3);
});
