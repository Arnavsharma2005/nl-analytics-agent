/** A fake API for the browser tests: answers /v1 and /healthz from fixtures, and records what the page sent. */
import type { Page, Route } from "@playwright/test";
import type { Candidate, ConversationSummary, CuratorOverview, Dataset, Insights, Message, Share, StreamEvents, User } from "../src/api/types";

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
      chart: { type: "bar", x: "region", y: ["revenue"], series: null, band: null, title: "Revenue by region" },
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
      analysis: null,
      confidence: {
        signals: {
          verified: false, nearest_example: null, attempts: 2, invalid_attempts: 0, failed_runs: 1, empty_retries: 0,
          empty_result: false, rows: 3, assumptions: 1, follow_up: false, summary_checked: true, joins: 0,
          window_functions: false, subqueries: 0,
        },
        probability: null,
        label: null,
        model: null,
      },
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
  source: null,
  created_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
};

export const ARNAV: User = { login: "arnav", name: "Arnav", avatar_url: null, curator: false };

/** An answer rated thumbs up, waiting in the Curator. */
export const CANDIDATE: Candidate = {
  message_id: "7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
  question: "How many customers signed up in each region in 2025?",
  asked: "and by region?",
  sql: "SELECT r.name AS region, COUNT(*) AS customers\nFROM customers c JOIN regions r USING (region_id)\nGROUP BY r.name",
  summary: "North signed up the most customers.",
  comment: "Exactly what I needed",
  rated_at: "2026-10-22T09:30:00Z",
};

export const SHEET_URL = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=0";

/** An imported Google Sheet, as the API returns it. */
export const SHEET: Dataset = {
  ...SALES_CSV,
  id: "5e2f8a1b-3c4d-4e5f-8a9b-0c1d2e3f4a5b",
  name: "Shop Sales - Q1.csv",
  table: "shop_sales_q1",
  source: SHEET_URL,
};

/** A shared snapshot of message `m`, as the API returns it. */
export function shareOf(m: Message, id = "AbCdEfGhIjKlMnOp", about: string | null = null): Share {
  return {
    id, question: m.question, about, asked_at: m.created_at, expires_at: "2027-01-20T10:00:00Z",
    answer: { ...m.answer, llm_calls: [], failures: [], timings: {} },
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
  /** Google Sheets links imported and how an import is answered; ids of datasets refreshed and how that is answered. */
  imported: string[];
  importSheet: (url: string) => { status: number; body: unknown };
  refreshed: string[];
  refresh: (dataset: Dataset) => { status: number; body: unknown };
  /** What the Curator shows (null: not a curator), the decisions sent, and how exporting is answered. */
  curator: CuratorOverview | null;
  reviews: { id: string; decision: string; question?: string }[];
  exportPr: () => { status: number; body: unknown };
  /** Shared answers by link id, and the message ids that were shared. */
  shares: Record<string, Share>;
  shared: string[];
  /** The insights feed the nightly job left. */
  insights: Insights;
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
    imported: [],
    importSheet: () => ({ status: 201, body: SHEET }),
    refreshed: [],
    refresh: (d) => ({ status: 200, body: { ...d, rows: d.rows + 10 } }),
    shares: {},
    shared: [],
    insights: { week_start: null, generated_at: null, insights: [] },
    curator: null,
    reviews: [],
    exportPr: () => ({ status: 200, body: { url: "https://github.com/arnav/nl-analytics-agent/pull/31", count: 1 } }),
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
  await page.route("**/v1/insights", (route) => json(route, api.insights));
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
  await page.route("**/v1/datasets/sheets", (route) => {
    const { url } = route.request().postDataJSON();
    api.imported.push(url);
    const r = api.importSheet(url);
    if (r.status < 400) api.datasets = [r.body as Dataset, ...api.datasets];
    return json(route, r.body, r.status);
  });
  await page.route("**/v1/datasets/*/refresh", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    const found = api.datasets.find((d) => d.id === id);
    if (!found) return json(route, { status: 404, code: "dataset_not_found", title: "This uploaded file is no longer available" }, 404);
    api.refreshed.push(id);
    const r = api.refresh(found);
    if (r.status < 400) api.datasets = api.datasets.map((d) => (d.id === id ? (r.body as Dataset) : d));
    return json(route, r.body, r.status);
  });
  const notCurator = { status: 403, code: "not_a_curator", title: "Only curators can see this" };
  await page.route("**/v1/curator", (route) => (api.curator ? json(route, api.curator) : json(route, notCurator, 403)));
  await page.route("**/v1/curator/*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    const c = api.curator;
    if (!c) return json(route, notCurator, 403);
    if (id === "export.yml") {
      const yaml = c.approved.map((a) => `  - question: ${a.question}\n    verified_by: ${a.reviewer}\n`).join("\n");
      return route.fulfill({ status: 200, contentType: "text/plain", body: yaml });
    }
    if (id === "export") {
      const r = api.exportPr();
      if (r.status < 400) c.approved = [];
      return json(route, r.body, r.status);
    }
    const body = route.request().postDataJSON();
    api.reviews.push({ id, ...body });
    const found = c.queue.find((q) => q.message_id === id)!;
    c.queue = c.queue.filter((q) => q !== found);
    const curated = { message_id: id, status: body.decision === "approve" ? "approved" : "rejected", question: body.question ?? found.question,
      sql: found.sql, reviewer: "arnav", reviewed_at: new Date().toISOString(), pr_url: null } as const;
    if (body.decision === "approve") c.approved = [...c.approved, curated];
    return json(route, curated);
  });
  await page.route("**/v1/messages/*/share", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    api.shared.push(id);
    const share = shareOf(message(id, "c"), `share${String(api.shared.length).padStart(11, "0")}`);
    api.shares[share.id] = share;
    return json(route, share);
  });
  await page.route("**/v1/shares/*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    if (!api.shares[id]) return json(route, { status: 404, code: "share_not_found", title: "This shared answer is not available", detail: "Shared answers are kept for 90 days." }, 404);
    if (route.request().method() === "DELETE") {
      delete api.shares[id];
      return route.fulfill({ status: 204 });
    }
    return json(route, api.shares[id]);
  });
  await page.route("**/v1/messages/*/feedback", (route) => {
    api.feedback.push(route.request().postDataJSON());
    return route.fulfill({ status: 204 });
  });
  await page.route("**/v1/messages/*/explain", (route) =>
    json(route, { steps: ["Add up the sales of completed orders.", "Split them by region."] }));
  return api;
}
