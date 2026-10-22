import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { message, mockApi, shareOf, STARTERS } from "./mock";

test("an answer can be shared as a link, and stopped", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const api = await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: STARTERS[0] }).click();
  await page.getByRole("button", { name: "Share" }).click();
  await expect(page.getByText("Link copied. Anyone with it can see this answer")).toBeVisible();
  const link = await page.getByRole("textbox", { name: "Share link" }).inputValue();
  expect(link).toMatch(/\/s\/share0{10}1$/);
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(link);
  expect(api.shared).toHaveLength(1);

  await page.getByRole("button", { name: "Stop sharing" }).click();
  await expect(page.getByRole("button", { name: "Share" })).toBeVisible();
  expect(api.shares).toEqual({});
});

test("a shared answer opens read-only, with a way to ask your own", async ({ page }) => {
  const share = shareOf(message("0b3e3a8e-5c1d-4d5e-8f7a-1b2c3d4e5f60", "c"));
  const api = await mockApi(page, { shares: { [share.id]: share } });
  await page.goto(`/s/${share.id}`);
  await expect(page.getByRole("heading", { level: 2, name: "Revenue by region" })).toBeVisible();
  await expect(page.getByText(/A shared answer about the store's sales, asked on/)).toBeVisible();
  await expect(page.locator(".figure").first()).toHaveText("₹1.23 Cr");
  await expect(page.locator("figure canvas")).toBeVisible();
  for (const name of ["Good answer", "Bad answer", "How this was answered", "Share"]) {
    await expect(page.getByRole("button", { name })).toHaveCount(0);
  }
  await expect(page.getByText("Ask next")).toHaveCount(0);
  await page.getByText("The SQL behind this answer").click();
  await expect(page.locator("pre")).toContainText("SUM(oi.quantity * oi.unit_price)");
  await expect(page).toHaveTitle("Revenue by region · Sales analyst");
  expect(api.asked).toEqual([]);

  await page.getByRole("link", { name: "Ask your own question" }).click();
  await expect(page.getByRole("heading", { name: "What do you want to know about the store's sales?" })).toBeVisible();
});

test("a link that no longer works says so", async ({ page }) => {
  await mockApi(page);
  await page.goto("/s/ZZZZZZZZZZZZZZZZ");
  await expect(page.getByRole("alert")).toContainText("This shared answer is not available");
});

for (const scheme of ["light", "dark"] as const) {
  test(`a shared answer has no accessibility violations (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    const share = shareOf(message("m", "c"), "AbCdEfGhIjKlMnOp", "shop_sales.csv");
    await mockApi(page, { shares: { [share.id]: share } });
    await page.goto(`/s/${share.id}`);
    await expect(page.getByText("A shared answer about shop_sales.csv")).toBeVisible();
    await expect(page.locator("figure canvas")).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}

test("a shared answer fits a phone @mobile", async ({ page }) => {
  const share = shareOf(message("m", "c"));
  await mockApi(page, { shares: { [share.id]: share } });
  await page.goto(`/s/${share.id}`);
  await expect(page.locator("figure canvas")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBe(0);
});
