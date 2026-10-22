import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { answerEvents, message, mockApi, sse, STARTERS } from "./mock";

const problem = (status: number, code: string, title: string, extra: object = {}) =>
  ({ status, body: JSON.stringify({ status, code, title, ...extra }) });

async function askStarter(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: STARTERS[0] }).click();
  await expect(page.getByRole("heading", { level: 2, name: STARTERS[0] })).toBeVisible();
  await expect(page.getByText("Ask next")).toBeVisible();
}

test("a question streams into an answer with marked figures, a chart and follow-ups", async ({ page }) => {
  const api = await mockApi(page);
  await askStarter(page);

  const summary = page.locator("article p.text-lg").first();
  await expect(summary).toHaveText("North led with ₹1.23 Cr, well ahead of South at ₹45.3 L.");
  await expect(summary.locator(".figure")).toHaveText(["₹1.23 Cr", "₹45.3 L"]);
  await expect(page.getByText("Read as: revenue means completed orders only")).toBeVisible();
  await expect(page.locator("figure canvas")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save chart" })).toBeVisible();

  expect(api.asked).toHaveLength(1);
  expect(api.asked[0].key).toMatch(/^[0-9a-f-]{36}$/);

  await page.getByRole("button", { name: "Which region has the highest margin?" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Which region has the highest margin?" })).toBeVisible();
  expect(api.asked.map((a) => a.question)).toEqual([STARTERS[0], "Which region has the highest margin?"]);
});

test("typing a question and pressing Enter asks it", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/");
  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill("Top 5 customers by revenue");
  await box.press("Enter");
  await expect(page.getByRole("heading", { level: 2, name: "Top 5 customers by revenue" })).toBeVisible();
  await expect(box).toHaveValue("");
  expect(api.asked[0].question).toBe("Top 5 customers by revenue");
});

test("a dropped stream is sent again with the same idempotency key", async ({ page }) => {
  const api = await mockApi(page, {
    answer: (key, conversation, question, attempt) => {
      const events = answerEvents(message(key, conversation, question));
      return { body: sse(attempt === 1 ? events.slice(0, 4) : events) }; // the first stream ends before `done`
    },
  });
  await askStarter(page);
  expect(api.asked).toHaveLength(2);
  expect(api.asked[1].key).toBe(api.asked[0].key);
  await expect(page.locator(".figure").first()).toHaveText("₹1.23 Cr");
});

test("a thumbs down asks what was wrong and sends it", async ({ page }) => {
  const api = await mockApi(page);
  await askStarter(page);
  await page.getByRole("button", { name: "Bad answer" }).click();
  await page.getByText("The chart doesn't fit").click();
  await page.getByPlaceholder("Anything else? (optional)").fill("a line chart would be clearer");
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByText("Thanks, noted.")).toBeVisible();
  expect(api.feedback).toEqual([{ rating: "down", category: "wrong_chart", comment: "a line chart would be clearer" }]);
});

test("the details show the steps, models, failed tries, SQL and a plain-English explanation", async ({ page }) => {
  await mockApi(page);
  await askStarter(page);
  await page.getByRole("button", { name: "How this was answered" }).click();
  const panel = page.getByRole("complementary");
  await expect(panel.getByText("Total")).toBeVisible();
  await expect(panel.getByText("rate limited")).toBeVisible();
  await expect(panel.getByText("Fixed itself after 1 failed try")).toBeVisible();
  await expect(panel.locator("pre").last()).toContainText("SUM(oi.quantity * oi.unit_price)");
  await panel.getByRole("button", { name: "Explain this query in plain English" }).click();
  await expect(panel.getByRole("listitem").filter({ hasText: "Split them by region." })).toBeVisible();
  await panel.getByRole("button", { name: "Close details" }).click();
  await expect(panel).toBeHidden();
});

test("running out of questions says when to come back, without a retry button", async ({ page }) => {
  await mockApi(page, { answer: () => problem(429, "quota_exceeded", "Too many questions", { retry_after: 1200 }) });
  await page.goto("/");
  await page.getByRole("button", { name: STARTERS[0] }).click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("You've reached the question limit for now");
  await expect(alert).toContainText("in 20 minutes");
  await expect(alert.getByRole("button")).toHaveCount(0);
});

test("a failure that may pass can be asked again", async ({ page }) => {
  let fail = true;
  await mockApi(page, {
    answer: (key, conversation, question) => {
      if (fail) return { body: sse([["message", { id: key, conversation_id: conversation }], ["error", { status: 502, code: "all_providers_failed", title: "x" }]]) };
      return { body: sse(answerEvents(message(key, conversation, question))) };
    },
  });
  await page.goto("/");
  await page.getByRole("button", { name: STARTERS[0] }).click();
  await expect(page.getByRole("alert")).toContainText("Every model provider is busy");
  fail = false;
  await page.getByRole("button", { name: "Ask again" }).click();
  await expect(page.locator(".figure").first()).toHaveText("₹1.23 Cr");
});

test("a sleeping server is announced while it wakes", async ({ page }) => {
  await mockApi(page, { healthDelayMs: 6000 });
  await page.goto("/");
  await expect(page.getByText("The server is waking up")).toBeVisible();
});

test("New conversation clears the answers", async ({ page }) => {
  await mockApi(page);
  await askStarter(page);
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(page.getByRole("heading", { name: "What do you want to know about the store's sales?" })).toBeVisible();
});

for (const scheme of ["light", "dark"] as const) {
  test(`no accessibility violations (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("button", { name: STARTERS[0] })).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.getByRole("button", { name: STARTERS[0] }).click();
    await expect(page.getByText("Ask next")).toBeVisible();
    await page.getByRole("button", { name: "How this was answered" }).click();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}

test("on a phone the page fits the screen and the details open as a sheet @mobile", async ({ page }) => {
  await mockApi(page);
  await askStarter(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBe(0);
  await page.getByRole("button", { name: "How this was answered" }).click();
  const sheet = page.getByRole("dialog", { name: "How this was answered" });
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
});
