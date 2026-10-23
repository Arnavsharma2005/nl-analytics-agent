import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { ReportStatus } from "../src/api/types";
import { message, mockApi } from "./mock";

const ID = "6f1c2b9e-1d2a-4c3b-9e8f-0a1b2c3d4e5f";
const QUESTION = "How did 2025 go, and where should we focus next year?";

function status(overrides: Partial<ReportStatus>): ReportStatus {
  return {
    id: ID, question: QUESTION, status: "running", stage: "planning", steps_done: 0, steps_total: null, report: null,
    error: null, created_at: "2026-10-23T08:00:00Z", updated_at: "2026-10-23T08:00:00Z", ...overrides,
  };
}

const step1 = message("m1", "c1", "Revenue by region").answer;
const step2 = { ...message("m2", "c2", "Completed orders by year").answer, error: "No working query", rows: [], sql: null };
const DONE = status({
  status: "done", stage: "done", steps_done: 2, steps_total: 2,
  report: {
    question: QUESTION, title: "2025 in review", summary: "Revenue is concentrated in two regions.",
    sections: [
      { heading: "Where revenue comes from", text: "North led with ₹1.2 Cr.", steps: [1], unsupported: [] },
      { heading: "Growth", text: "Revenue grew 40%.", steps: [1], unsupported: ["40%"] },
    ],
    steps: [
      { question: "Revenue by region", purpose: "where revenue comes from", answer: step1 },
      { question: "Completed orders by year", purpose: "the trend", answer: step2 },
    ],
  },
});

test("a broad question becomes a report whose sections cite their steps", async ({ page }) => {
  await mockApi(page);
  const polls = [status({ stage: "step", steps_done: 1, steps_total: 2 }), DONE];
  let started: unknown = null;
  await page.route("**/v1/reports", (route) => {
    if (route.request().method() === "POST") {
      started = route.request().postDataJSON();
      return route.fulfill({ status: 202, json: status({}) });
    }
    return route.fulfill({ json: [] });
  });
  await page.route(`**/v1/reports/${ID}`, (route) => route.fulfill({ json: polls.length > 1 ? polls.shift() : polls[0] }));

  await page.goto("/");
  await page.getByRole("link", { name: "Deep Analysis" }).click();
  await expect(page.getByRole("heading", { name: "Deep Analysis" })).toBeVisible();
  await page.getByRole("button", { name: QUESTION }).click();
  await page.getByRole("button", { name: "Write the report" }).click();
  expect(started).toEqual({ question: QUESTION });

  await expect(page.getByText("Answering step 2 of 2…")).toBeVisible();
  await expect(page.getByRole("heading", { name: "2025 in review" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("These figures could not be found in the cited results, so check them: 40%.")).toBeVisible();
  await page.getByRole("link", { name: "step 1" }).first().click();
  await expect(page).toHaveURL(new RegExp(`/reports/${ID}#step-1$`));
  await expect(page.getByText("This step could not be answered, so the report does not rely on it.")).toBeVisible();

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

test("a failed report says why, and the list shows each report's state", async ({ page }) => {
  await mockApi(page);
  await page.route(`**/v1/reports/${ID}`, (route) => route.fulfill({ json: status({ status: "failed", error: "The report was interrupted. Please try again." }) }));
  await page.goto(`/reports/${ID}`);
  await expect(page.getByText("The report was interrupted. Please try again.")).toBeVisible();

  await page.route("**/v1/reports", (route) => route.fulfill({ json: [status({ stage: "writing", steps_done: 3, steps_total: 3 })] }));
  await page.goto("/reports");
  await expect(page.getByRole("link", { name: /Writing the report/ })).toBeVisible();
});

test("signed out, starting a report asks you to sign in", async ({ page }) => {
  await mockApi(page);
  await page.route("**/v1/reports", (route) =>
    route.fulfill({ status: 401, contentType: "application/problem+json", json: { code: "sign_in_required", title: "Sign in to use Deep Analysis", status: 401 } }));
  await page.goto("/reports");
  await page.getByLabel("Your question").fill("How are sales going?");
  await page.getByRole("button", { name: "Write the report" }).click();
  await expect(page.getByText("Sign in to use Deep Analysis").first()).toBeVisible();
});
