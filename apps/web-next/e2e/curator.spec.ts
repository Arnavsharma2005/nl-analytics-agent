import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { ARNAV, CANDIDATE, mockApi } from "./mock";

const CURATOR = { ...ARNAV, curator: true };
const queue = (n = 1) => ({ queue: [CANDIDATE, { ...CANDIDATE, message_id: "11111111-2222-4333-8444-555555555555", question: "Top 3 products by units sold in 2025", asked: "Top 3 products by units sold in 2025", comment: null }].slice(0, n), approved: [], can_open_pr: true });

test("a curator finds the Curator from the header", async ({ page }) => {
  await mockApi(page, { user: CURATOR, curator: queue() });
  await page.goto("/");
  await page.getByRole("link", { name: "Review answers" }).click();
  await expect(page).toHaveURL(/\/curator$/);
  await expect(page.getByRole("heading", { name: "Answers to review" })).toBeVisible();
});

test("other visitors see no way to the Curator, and are turned away from it", async ({ page }) => {
  await mockApi(page, { user: ARNAV });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "History" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Review answers" })).toHaveCount(0);
  await page.goto("/curator");
  await expect(page.getByRole("alert")).toContainText("Only curators can see this");
  await expect(page.getByRole("link", { name: "Sign in with GitHub" })).toHaveAttribute("href", "/v1/auth/github?next=%2Fcurator");
});

test("an answer is approved under clearer wording, the other rejected, and the approvals opened as a pull request", async ({ page }) => {
  const api = await mockApi(page, { user: CURATOR, curator: queue(2) });
  await page.goto("/curator");
  const first = page.getByRole("article", { name: CANDIDATE.question });
  await expect(first).toContainText("Asked as “and by region?”, rated");
  await expect(first).toContainText("Exactly what I needed");
  await expect(first.getByLabel("SQL")).toContainText("GROUP BY r.name");

  await first.getByLabel("Approve as the question").fill("How many customers signed up per region in 2025?");
  await first.getByRole("button", { name: "Approve" }).click();
  await page.getByRole("article", { name: "Top 3 products by units sold in 2025" }).getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Nothing to review.")).toBeVisible();
  expect(api.reviews).toEqual([
    { id: CANDIDATE.message_id, decision: "approve", question: "How many customers signed up per region in 2025?" },
    { id: "11111111-2222-4333-8444-555555555555", decision: "reject" },
  ]);

  const approved = page.getByRole("region", { name: "Approved, not in the library yet" });
  await expect(approved).toContainText("How many customers signed up per region in 2025?approved by arnav");
  await approved.getByRole("button", { name: "Open a pull request" }).click();
  await expect(approved.getByRole("link", { name: "arnav/nl-analytics-agent/pull/31" })).toHaveAttribute("href", "https://github.com/arnav/nl-analytics-agent/pull/31");
});

test("approving keeps the question as understood unless it is reworded", async ({ page }) => {
  const api = await mockApi(page, { user: CURATOR, curator: queue() });
  await page.goto("/curator");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect.poll(() => api.reviews).toEqual([{ id: CANDIDATE.message_id, decision: "approve" }]);
});

test("without a GitHub token the approvals are copied as YAML", async ({ page }) => {
  await mockApi(page, { user: CURATOR, curator: { ...queue(0), can_open_pr: false, approved: [{ message_id: CANDIDATE.message_id, status: "approved", question: CANDIDATE.question, sql: CANDIDATE.sql, reviewer: "arnav", reviewed_at: "2026-10-22T10:00:00Z", pr_url: null }] } });
  await page.goto("/curator");
  const approved = page.getByRole("region", { name: "Approved, not in the library yet" });
  await expect(approved.getByRole("button", { name: "Open a pull request" })).toHaveCount(0);
  await approved.getByRole("button", { name: "Copy as YAML" }).click();
  await expect(approved.locator("pre")).toContainText(`- question: ${CANDIDATE.question}`);
  await expect(approved.getByText(/apps\/api\/semantic\/queries\.yml/)).toBeVisible();
});

test("a pull request that can't be opened says why and keeps the approvals", async ({ page }) => {
  await mockApi(page, {
    user: CURATOR,
    curator: { ...queue(0), approved: [{ message_id: CANDIDATE.message_id, status: "approved", question: CANDIDATE.question, sql: CANDIDATE.sql, reviewer: "arnav", reviewed_at: "2026-10-22T10:00:00Z", pr_url: null }] },
    exportPr: () => ({ status: 502, body: { status: 502, code: "export_failed", title: "The pull request could not be opened", detail: "GitHub said 403." } }),
  });
  await page.goto("/curator");
  await page.getByRole("button", { name: "Open a pull request" }).click();
  await expect(page.getByRole("alert")).toContainText("The pull request could not be opened");
  await expect(page.getByRole("region", { name: "Approved, not in the library yet" }).getByRole("listitem")).toHaveCount(1);
});

for (const scheme of ["light", "dark"] as const) {
  test(`the Curator has no accessibility violations (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await mockApi(page, { user: CURATOR, curator: queue(2) });
    await page.goto("/curator");
    await expect(page.getByRole("article")).toHaveCount(2);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}

test("the Curator fits a phone @mobile", async ({ page }) => {
  await mockApi(page, { user: CURATOR, curator: queue(2) });
  await page.goto("/curator");
  await expect(page.getByRole("article")).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
});
