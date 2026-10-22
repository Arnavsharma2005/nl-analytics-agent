import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { EvalReportData } from "../src/api/evalReport";
import { mockApi } from "./mock";

const score = (correct: number, questions: number) => ({ correct, questions, accuracy_pct: Math.round((1000 * correct) / questions) / 10 });

const RUN = {
  file: "2026-10-21-1200-abc1234.json", run_at: "2026-10-21T12:00:00+00:00", commit: "abc1234", set: "questions" as const,
  models: ["Groq", "Gemini"], sql_written_by: { groq: 60, gemini: 10 }, features: [], median_latency_s: 3.2,
  all: score(110, 120), held_out: score(64, 70),
};

const REPORT: EvalReportData = {
  held_out_questions: { questions: 70, conversations: 13 },
  runs: [
    { ...RUN, file: "2026-10-22-0900-def5678.json", run_at: "2026-10-22T09:00:00+00:00", commit: "def5678", all: score(9, 10), held_out: null },
    RUN,
  ],
  latest: {
    questions: {
      ...RUN, held_out: score(64, 70), repaired_by_retry: 3,
      by_difficulty: { hard: score(14, 19), easy: score(15, 15), medium: score(35, 36) },
      by_category: { joins: score(22, 24), time: score(15, 16), simple: score(10, 10), advanced: score(17, 20) },
      questions: [
        { id: 51, question: "Revenue by region in 2025", category: "joins", difficulty: "easy", correct: true, attempts: 1, sql: "SELECT r.name, SUM(1) FROM regions r", error: null },
        { id: 52, question: "Which month had the most cancellations?", category: "time", difficulty: "hard", correct: false, attempts: 3, sql: "SELECT month FROM orders", error: "column \"month\" does not exist" },
      ],
    },
  },
};

const EMPTY: EvalReportData = { held_out_questions: { questions: 70, conversations: 13 }, runs: [], latest: {} };

async function withReport(page: Page, report: EvalReportData) {
  await mockApi(page);
  await page.route("**/eval-report.json", (route) => route.fulfill({ json: report }));
}

test("the accuracy page is a link away from the app", async ({ page }) => {
  await withReport(page, REPORT);
  await page.goto("/");
  await page.getByRole("link", { name: "Accuracy" }).click();
  await expect(page).toHaveURL(/\/eval$/);
  await expect(page.getByRole("heading", { name: "How often it gets it right" })).toBeVisible();
  await expect(page).toHaveTitle("Accuracy · Sales analyst");
});

test("the latest full held-out run leads, broken down and listed question by question", async ({ page }) => {
  await withReport(page, REPORT);
  await page.goto("/eval");
  await expect(page.getByText("91.4%", { exact: true })).toBeVisible();
  await expect(page.getByText("64 of 70 questions it was never tuned on answered correctly.")).toBeVisible();
  await expect(page.getByRole("link", { name: "abc1234" }).first()).toHaveAttribute("href", /\/commit\/abc1234$/);
  await expect(page.getByText("3 were right only after the agent fixed its own failed query.")).toBeVisible();

  const difficulty = page.getByRole("region", { name: "By difficulty" });
  await expect(difficulty.locator("dt")).toHaveText(["easy", "medium", "hard"]);
  await expect(difficulty).toContainText("14 of 19");

  const list = page.getByRole("region", { name: "Every held-out question" });
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await list.getByLabel("Only the 1 it got wrong").check();
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await list.getByText("Which month had the most cancellations?").click();
  await expect(list.getByText("column \"month\" does not exist")).toBeVisible();
  await expect(list.getByLabel("SQL it ran")).toHaveText("SELECT month FROM orders");

  const runs = page.getByRole("region", { name: "Every published run" });
  await expect(runs.getByRole("row")).toHaveCount(3);
  await expect(runs.getByRole("row").nth(1)).toContainText("Not all 70 run");
  await expect(runs.getByRole("row").nth(2)).toContainText("64 of 70 (91.4%)");
});

test("before any full run is published the page says so, and still explains the method", async ({ page }) => {
  await withReport(page, EMPTY);
  await page.goto("/eval");
  await expect(page.getByText("No run over all 70 held-out questions has been published yet.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Every published run" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "How this is measured" })).toBeVisible();
});

test("the committed results load as they are deployed", async ({ page }) => {
  await mockApi(page);
  await page.goto("/eval");
  await expect(page.getByRole("heading", { name: "How this is measured" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("the accuracy page has no accessibility violations and fits a phone @mobile", async ({ page }) => {
  await withReport(page, REPORT);
  await page.goto("/eval");
  await expect(page.getByText("91.4%", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("the accuracy page has no accessibility violations", async ({ page }) => {
  await withReport(page, REPORT);
  await page.goto("/eval");
  await page.getByText("Which month had the most cancellations?").click();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
