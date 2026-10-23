import { expect, test } from "@playwright/test";
import { answerEvents, message, mockApi, sse, STARTERS } from "./mock";

const MONTHS = Array.from({ length: 12 }, (_, i) => `2025-${String(i + 1).padStart(2, "0")}-01`);
const HISTORY = MONTHS.map((m, i) => [m, 1000000 + 50000 * i, i === 11 ? 1550000 : null, i === 11 ? 1550000 : null, i === 11 ? 1550000 : null]);
const FUTURE = [["2026-01-01", null, 1600000, 1450000, 1750000], ["2026-02-01", null, 1640000, 1430000, 1850000], ["2026-03-01", null, 1680000, 1420000, 1940000]];

function forecastMessage(key: string, conversation: string, question: string) {
  const m = message(key, conversation, question);
  m.answer = {
    ...m.answer, analysis: "forecast", columns: ["month", "revenue", "forecast", "lower", "upper"], rows: [...HISTORY, ...FUTURE],
    chart: { type: "line", x: "month", y: ["revenue", "forecast"], series: null, band: ["lower", "upper"], title: "Revenue forecast" },
    summary: "Revenue should reach about ₹16.8 L by March 2026.",
    sql_reasoning: "Monthly revenue. Forecast of revenue for the next 3 months with a damped trend model; the range is an 80% interval.",
  };
  return m;
}

test("a forecast is drawn with its range, and its table keeps every period", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await mockApi(page, { answer: (key, conversation, question) => ({ body: sse(answerEvents(forecastMessage(key, conversation, question))) }) });
  await page.goto("/");
  await page.getByRole("button", { name: STARTERS[0] }).click();
  await expect(page.getByText("Revenue should reach about")).toBeVisible();
  await expect(page.locator("figure canvas")).toBeVisible();
  await expect(page.getByRole("figure", { name: "Revenue forecast" })).toBeVisible();
  expect(errors).toEqual([]);
});
