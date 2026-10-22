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

test("a follow-up shows how it was understood with the earlier questions", async ({ page }) => {
  const api = await mockApi(page, {
    answer: (key, conversation, question) => {
      const m = message(key, conversation, question);
      if (question === "and in 2024?") m.answer.standalone_question = "Revenue by region in 2024";
      return { body: sse(answerEvents(m)) };
    },
  });
  await askStarter(page);
  await expect(page.getByText("Understood as:")).toHaveCount(0);
  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill("and in 2024?");
  await box.press("Enter");
  await expect(page.getByText("Understood as: Revenue by region in 2024")).toBeVisible();
  expect(new Set(api.asked.map((a) => a.conversation)).size).toBe(1); // both asked in one conversation
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

test("New conversation clears the answers and forgets the earlier questions", async ({ page }) => {
  const api = await mockApi(page);
  await askStarter(page);
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(page.getByRole("heading", { name: "What do you want to know about the store's sales?" })).toBeVisible();
  await page.getByRole("button", { name: STARTERS[0] }).click(); // on the same page, not a reload
  await expect(page.getByText("Ask next")).toBeVisible();
  expect(api.asked[1].conversation).not.toBe(api.asked[0].conversation);
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

const ARNAV = { login: "arnav", name: "Arnav", avatar_url: null };

test("signing in is offered, and goes to GitHub and back to this page", async ({ page }) => {
  await mockApi(page);
  let started = "";
  await page.route("**/v1/auth/github**", (route) => {
    started = route.request().url();
    return route.fulfill({ status: 200, contentType: "text/html", body: "<p>GitHub</p>" });
  });
  await page.goto("/");
  await page.getByRole("link", { name: "Sign in with GitHub" }).click();
  await expect(page.getByText("GitHub", { exact: true })).toBeVisible();
  expect(new URL(started).searchParams.get("next")).toBe("/");
});

test("an earlier conversation can be opened from the history and carried on", async ({ page }) => {
  const conversation = "6f1c2b7e-1d1a-4a51-9a39-3a4f7b2c9d10";
  const first = message("0b3e3a8e-5c1d-4d5e-8f7a-1b2c3d4e5f60", conversation, "Revenue by region");
  const api = await mockApi(page, {
    user: ARNAV,
    history: [{ conversation: { id: conversation, title: "Revenue by region", messages: 1, updated_at: new Date().toISOString() }, messages: [first] }],
  });
  await page.goto("/");
  await page.getByRole("button", { name: "History" }).click();
  const dialog = page.getByRole("dialog", { name: "Your conversations" });
  await expect(dialog.getByText("Signed in as arnav")).toBeVisible();
  await dialog.getByRole("button", { name: /Revenue by region/ }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("heading", { level: 2, name: "Revenue by region" })).toBeVisible();
  await expect(page.locator(".figure").first()).toHaveText("₹1.23 Cr");

  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill("and in 2024?");
  await box.press("Enter");
  await expect(page.getByRole("heading", { level: 2, name: "and in 2024?" })).toBeVisible();
  expect(api.asked.at(-1)!.conversation).toBe(conversation); // the follow-up is asked in the opened conversation
});

test("signing out from the history", async ({ page }) => {
  const api = await mockApi(page, { user: ARNAV });
  await page.goto("/");
  await page.getByRole("button", { name: "History" }).click();
  await expect(page.getByText("Nothing yet.")).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("link", { name: "Sign in with GitHub" })).toBeVisible();
  expect(api.signedOut).toBe(true);
});

test("a sign-in that did not finish is explained", async ({ page }) => {
  await mockApi(page);
  await page.goto("/?signin=failed");
  await expect(page.getByRole("alert")).toContainText("Signing in with GitHub didn't finish");
  await expect(page).toHaveURL(/\/$/);
  await page.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("the history has no accessibility violations and fits a phone @mobile", async ({ page }) => {
  const conversation = "6f1c2b7e-1d1a-4a51-9a39-3a4f7b2c9d10";
  await mockApi(page, {
    user: ARNAV,
    history: [{
      conversation: { id: conversation, title: "Which region grew revenue the fastest from 2024 to 2025, in percent?", messages: 3, updated_at: new Date(Date.now() - 3 * 86_400_000).toISOString() },
      messages: [],
    }],
  });
  await askStarter(page);
  await page.getByRole("button", { name: "History" }).click();
  await expect(page.getByText("3 days ago, 3 questions")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBe(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
