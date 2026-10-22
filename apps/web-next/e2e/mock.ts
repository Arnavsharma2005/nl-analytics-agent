/** A fake API for the browser tests: answers /v1 and /healthz from fixtures, and records what the page sent. */
import type { Page, Route } from "@playwright/test";
import type { ConversationSummary, Dataset, Message, StreamEvents, User } from "../src/api/types";

export const STARTERS = ["Revenue by region", "Monthly revenue trend in 2025", "Top 5 products by revenue"];

export const ROWS = [["North", 12345678.9], ["South", 4530000], ["West", 2210000.5]];

export function message(id: string, conversation: string, question = "Revenue by region"): Message {
  return {
    id,
    conversation_id: conversation,
    dataset_id: null,
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
      standalone_question: null,
    },
  };
}

export const SALES_CSV: Dataset = {
  id: "3c9a7d2e-8b1f-4e6a-9c3d-5f7e1a2b4c6d",
  name: "shop_sales.csv",
  table: "shop_sales",
  rows: 1250,
  columns: [
    { name: "order_date", header: "Order Date", kind: "date", empty: 0, distinct: 300, low: "2025-01-01", high: "2025-06-30", examples: [], personal: null },
    { name: "city", header: "City", kind: "text", empty: 3, distinct: 12, low: null, high: null, examples: ["Bengaluru", "Mumbai", "Delhi"], personal: null },
    { name: "contact", header: "Contact", kind: "text", empty: 0, distinct: 900, low: null, high: null, examples: [], personal: "email address" },
    { name: "amount_inr", header: "Amount (INR)", kind: "number", empty: 0, distinct: 1100, low: 99.9, high: 22000, examples: [], personal: null },
  ],
  suggestions: ["Total amount inr by city", "Amount inr by month", "How many rows are there for each city?"],
  created_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
};

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
  asked: { question: string; key: string | null; conversation: string; dataset: string | null }[];
  feedback: unknown[];
  /** Replace how a question is answered: return the response body, or a problem for an error. */
  answer: (key: string, conversation: string, question: string, attempt: number) => { status?: number; body: string; headers?: Record<string, string> };
  healthDelayMs: number;
  /** Who is signed in (null: nobody), and their earlier conversations with the messages in each. */
  user: User | null;
  history: { conversation: ConversationSummary; messages: Message[] }[];
  signedOut: boolean;
  /** Uploaded files that haven't expired, the names of files uploaded, and how an upload is answered. */
  datasets: Dataset[];
  uploaded: string[];
  upload: (name: string) => { status: number; body: unknown };
}

export async function mockApi(page: Page, overrides: Partial<Api> = {}): Promise<Api> {
  const api: Api = {
    asked: [],
    feedback: [],
    answer: (key, conversation, question) => ({ body: sse(answerEvents(message(key, conversation, question))) }),
    healthDelayMs: 0,
    user: null,
    history: [],
    signedOut: false,
    datasets: [],
    uploaded: [],
    upload: () => ({ status: 201, body: SALES_CSV }),
    ...overrides,
  };
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: status >= 400 ? "application/problem+json" : "application/json", body: JSON.stringify(body) });

  await page.route("**/healthz", async (route) => {
    if (api.healthDelayMs) await new Promise((r) => setTimeout(r, api.healthDelayMs));
    await json(route, { status: "ok" }).catch(() => {});
  });
  await page.route("**/v1/config", (route) =>
    json(route, { turnstile_site_key: null, starters: STARTERS, features: { voice: false, explain: true, feedback: true, sign_in: true, uploads: true }, upload_max_mb: 5 }));
  await page.route("**/v1/me", (route) => json(route, { user: api.user }));
  await page.route("**/v1/auth/logout", (route) => {
    api.user = null;
    api.signedOut = true;
    return route.fulfill({ status: 204 });
  });
  await page.route("**/v1/conversations", (route) => json(route, api.user ? api.history.map((h) => h.conversation) : []));
  await page.route("**/v1/session", (route) => json(route, { ok: true }));
  const attempts = new Map<string, number>();
  await page.route("**/v1/conversations/*/messages", (route) => {
    const req = route.request();
    if (req.method() === "GET") {
      const id = new URL(req.url()).pathname.split("/")[3];
      return json(route, api.history.find((h) => h.conversation.id === id)?.messages ?? []);
    }
    const key = req.headers()["idempotency-key"] ?? null;
    const { question, dataset_id = null } = req.postDataJSON();
    const conversation = new URL(req.url()).pathname.split("/")[3];
    api.asked.push({ question, key, conversation, dataset: dataset_id });
    const attempt = (attempts.get(key ?? "") ?? 0) + 1;
    attempts.set(key ?? "", attempt);
    const r = api.answer(key!, conversation, question, attempt);
    return route.fulfill({
      status: r.status ?? 200,
      headers: r.headers,
      contentType: (r.status ?? 200) >= 400 ? "application/problem+json" : "text/event-stream",
      body: r.body,
    });
  });
  await page.route("**/v1/datasets", (route) => {
    if (route.request().method() === "GET") return json(route, api.datasets);
    const name = /filename="([^"]+)"/.exec(route.request().postData() ?? "")?.[1] ?? "";
    api.uploaded.push(name);
    const r = api.upload(name);
    if (r.status < 400) api.datasets = [r.body as Dataset, ...api.datasets];
    return json(route, r.body, r.status);
  });
  await page.route("**/v1/datasets/*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    const found = api.datasets.find((d) => d.id === id);
    if (!found) return json(route, { status: 404, code: "dataset_not_found", title: "This uploaded file is no longer available" }, 404);
    if (route.request().method() === "DELETE") {
      api.datasets = api.datasets.filter((d) => d.id !== id);
      return route.fulfill({ status: 204 });
    }
    return json(route, found);
  });
  await page.route("**/v1/messages/*/feedback", (route) => {
    api.feedback.push(route.request().postDataJSON());
    return route.fulfill({ status: 204 });
  });
  await page.route("**/v1/messages/*/explain", (route) =>
    json(route, { steps: ["Add up the sales of completed orders.", "Split them by region."] }));
  return api;
}
