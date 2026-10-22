/** A fake API for the browser tests: answers /v1 and /healthz from fixtures, and records what the page sent. */
import type { Page, Route } from "@playwright/test";
import type { Message, StreamEvents } from "../src/api/types";

export const STARTERS = ["Revenue by region", "Monthly revenue trend in 2025", "Top 5 products by revenue"];

export const ROWS = [["North", 12345678.9], ["South", 4530000], ["West", 2210000.5]];

export function message(id: string, conversation: string, question = "Revenue by region"): Message {
  return {
    id,
    conversation_id: conversation,
    question,
    created_at: "2026-10-22T10:00:00Z",
    suggestions: ["Revenue by region by month", "Which region has the highest margin?"],
    answer: {
      question,
      intent: "data_query",
      answer: null,
      sql: "SELECT r.name AS region, SUM(oi.quantity * oi.unit_price) AS revenue FROM regions r GROUP BY 1",
      sql_reasoning: "Revenue is quantity times unit price on completed orders.",
      columns: ["region", "revenue"],
      rows: ROWS,
      truncated: false,
      chart: { type: "bar", x: "region", y: ["revenue"], series: null, title: "Revenue by region" },
      chart_source: "llm",
      summary: "North led with ₹1.23 Cr, well ahead of South at ₹45.3 L.",
      error: null,
      attempts: 2,
      failures: [{ sql: "SELECT nme FROM regions", error: "column nme does not exist", stage: "execute" }],
      timings: { router: 0.4, sql_agent: 1.2, execute: 0.1, chart_agent: 0.5, summary_agent: 0.8 },
      llm_calls: [
        { step: "sql_agent", provider: "Groq · gpt-oss-120b", ok: true, seconds: 1.2 },
        { step: "summary_agent", provider: "Gemini · flash", ok: false, error: "rate limited", seconds: 0.2 },
      ],
      cached: false,
      verified_question: null,
      assumptions: ["revenue means completed orders only"],
      summary_verified: true,
    },
  };
}

type Events = [keyof StreamEvents, unknown][];

export const sse = (events: Events) => events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");

export function answerEvents(m: Message): Events {
  const half = m.answer.summary!.length >> 1;
  return [
    ["message", { id: m.id, conversation_id: m.conversation_id }],
    ["step", { node: "router", label: "Understanding the question" }],
    ["step", { node: "sql_agent", label: "Writing SQL" }],
    ["table", { columns: m.answer.columns, rows: m.answer.rows, truncated: false }],
    ["chart", m.answer.chart],
    ["token", { text: m.answer.summary!.slice(0, half), replace: false }],
    ["token", { text: m.answer.summary!.slice(half), replace: false }],
    ["done", m],
  ];
}

export interface Api {
  asked: { question: string; key: string | null }[];
  feedback: unknown[];
  /** Replace how a question is answered: return the response body, or a problem for an error. */
  answer: (key: string, conversation: string, question: string, attempt: number) => { status?: number; body: string; headers?: Record<string, string> };
  healthDelayMs: number;
}

export async function mockApi(page: Page, overrides: Partial<Api> = {}): Promise<Api> {
  const api: Api = {
    asked: [],
    feedback: [],
    answer: (key, conversation, question) => ({ body: sse(answerEvents(message(key, conversation, question))) }),
    healthDelayMs: 0,
    ...overrides,
  };
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: status >= 400 ? "application/problem+json" : "application/json", body: JSON.stringify(body) });

  await page.route("**/healthz", async (route) => {
    if (api.healthDelayMs) await new Promise((r) => setTimeout(r, api.healthDelayMs));
    await json(route, { status: "ok" }).catch(() => {});
  });
  await page.route("**/v1/config", (route) =>
    json(route, { turnstile_site_key: null, starters: STARTERS, features: { voice: false, explain: true, feedback: true } }));
  await page.route("**/v1/session", (route) => json(route, { ok: true }));
  const attempts = new Map<string, number>();
  await page.route("**/v1/conversations/*/messages", (route) => {
    const req = route.request();
    const key = req.headers()["idempotency-key"] ?? null;
    const { question } = req.postDataJSON();
    api.asked.push({ question, key });
    const attempt = (attempts.get(key ?? "") ?? 0) + 1;
    attempts.set(key ?? "", attempt);
    const conversation = new URL(req.url()).pathname.split("/")[3];
    const r = api.answer(key!, conversation, question, attempt);
    return route.fulfill({
      status: r.status ?? 200,
      headers: r.headers,
      contentType: (r.status ?? 200) >= 400 ? "application/problem+json" : "text/event-stream",
      body: r.body,
    });
  });
  await page.route("**/v1/messages/*/feedback", (route) => {
    api.feedback.push(route.request().postDataJSON());
    return route.fulfill({ status: 204 });
  });
  await page.route("**/v1/messages/*/explain", (route) =>
    json(route, { steps: ["Add up the sales of completed orders.", "Split them by region."] }));
  return api;
}
