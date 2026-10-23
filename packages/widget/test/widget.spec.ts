import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type Route, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const WIDGET = readFileSync(new URL("../dist/nl-analyst.js", import.meta.url), "utf8");
const SITE = "https://shop.example";
const API = "https://api.example";

const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Shop</title></head><body><main>
<h1>Our sales</h1><nl-analyst base-url="${API}" api-key="aak_example" heading="Ask our sales data"></nl-analyst>
<script type="module" src="/nl-analyst.js"></script></main></body></html>`;

const rows = Array.from({ length: 10 }, (_, i) => [`Region ${i + 1}`, 1000.5 * (10 - i)]);
const MESSAGE = {
  id: "m1", conversation_id: "c1", question: "Revenue by region",
  answer: { question: "Revenue by region", intent: "data_query", summary: "North leads with 10,005.", columns: ["region", "revenue"], rows, sql: "SELECT region, SUM(revenue) FROM sales GROUP BY 1", error: null, answer: null },
};

function sse(events: [string, unknown][]) {
  return events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");
}

async function site(page: Page, api: (route: Route) => Promise<void>) {
  await page.route(`${SITE}/`, (route) => route.fulfill({ contentType: "text/html", body: PAGE }));
  await page.route(`${SITE}/nl-analyst.js`, (route) => route.fulfill({ contentType: "text/javascript", body: WIDGET }));
  await page.route(`${API}/**`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      return route.fulfill({ status: 204, headers: { "access-control-allow-origin": SITE, "access-control-allow-methods": "POST", "access-control-allow-headers": "authorization, content-type, accept, idempotency-key" } });
    }
    return api(route);
  });
  await page.goto(`${SITE}/`);
}

test("a question streams into a summary, a table and the SQL, and follow-ups stay in one conversation", async ({ page }) => {
  const asked: { url: string; auth: string | null; body: unknown }[] = [];
  await site(page, async (route) => {
    const r = route.request();
    asked.push({ url: r.url(), auth: await r.headerValue("authorization"), body: r.postDataJSON() });
    await route.fulfill({
      headers: { "content-type": "text/event-stream", "access-control-allow-origin": SITE },
      body: sse([["message", { id: "m1", conversation_id: "c1" }], ["step", { node: "sql", label: "Writing the query" }],
        ["token", { text: "North leads", replace: false }], ["done", MESSAGE]]),
    });
  });

  await expect(page.getByRole("heading", { name: "Ask our sales data" })).toBeVisible();
  const question = page.getByLabel("Your question");
  await question.fill("Revenue by region");
  await question.press("Enter");

  await expect(page.getByText("North leads with 10,005.")).toBeVisible();
  await expect(page.getByRole("table", { name: "Result for: Revenue by region" }).getByRole("row")).toHaveCount(9);
  await expect(page.getByText("First 8 of 10 rows.")).toBeVisible();
  await page.getByText("The SQL behind this answer").click();
  await expect(page.getByText("SELECT region, SUM(revenue)")).toBeVisible();
  await expect(question).toHaveValue("");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await question.fill("and last year?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("and last year?")).toBeVisible();
  await expect.poll(() => asked.length).toBe(2);
  expect(asked[0].auth).toBe("Bearer aak_example");
  expect(asked[0].body).toEqual({ question: "Revenue by region" });
  expect(asked[0].url).toBe(asked[1].url); // the same conversation
});

test("a refused key says why", async ({ page }) => {
  await site(page, (route) => route.fulfill({
    status: 403, headers: { "access-control-allow-origin": SITE }, contentType: "application/problem+json",
    body: JSON.stringify({ code: "origin_not_allowed", title: "This key cannot be used from this site", status: 403 }),
  }));
  await page.getByLabel("Your question").fill("Revenue by region");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("This key cannot be used from this site")).toBeVisible();
  await expect(page.getByRole("button", { name: "Ask" })).toBeEnabled();
});
