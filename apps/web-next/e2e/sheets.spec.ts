import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi, SALES_CSV, SHEET, SHEET_URL } from "./mock";

const bar = (page: Page) => page.getByRole("region", { name: "Uploaded file" });
const link = (page: Page) => page.getByRole("textbox", { name: "Or paste a link to a Google Sheet" });

async function importSheet(page: Page, url = SHEET_URL) {
  await link(page).fill(url);
  await page.getByRole("button", { name: "Import the sheet" }).click();
}

test("a Google Sheet is imported by its link and asked about like an uploaded file", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/");
  await importSheet(page);
  await expect(page.getByRole("heading", { level: 2, name: "What do you want to know about Shop Sales - Q1.csv?" })).toBeVisible();
  await expect(bar(page)).toContainText("Asking about Shop Sales - Q1.csv: 1,250 rows");
  expect(api.imported).toEqual([SHEET_URL]);

  await bar(page).getByText(/See the columns/).click();
  await expect(bar(page).getByRole("link", { name: "this Google Sheet" })).toHaveAttribute("href", SHEET_URL);
  await page.getByRole("button", { name: "Total amount inr by city" }).click();
  await expect(page.getByText("Ask next")).toBeVisible();
  expect(api.asked.at(-1)!.dataset).toBe(SHEET.id);
});

test("refreshing a sheet keeps the conversation and asks about what it holds now", async ({ page }) => {
  const api = await mockApi(page, { datasets: [SHEET] });
  await page.goto("/");
  await page.getByRole("button", { name: /^Shop Sales - Q1\.csv/ }).click();
  await page.getByRole("button", { name: "Total amount inr by city" }).click();
  await expect(page.getByText("Ask next")).toBeVisible();

  await bar(page).getByRole("button", { name: "Refresh from the sheet" }).click();
  await expect(bar(page)).toContainText("1,260 rows");
  await expect(page.getByRole("status").filter({ hasText: "Refreshed: 1,260 rows" })).toBeAttached();
  expect(api.refreshed).toEqual([SHEET.id]);
  await expect(page.getByRole("heading", { level: 2, name: "Total amount inr by city" })).toBeVisible();

  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill("and by month?");
  await box.press("Enter");
  await expect.poll(() => api.asked.length).toBe(2);
  expect(api.asked.map((a) => a.dataset)).toEqual([SHEET.id, SHEET.id]);
  expect(new Set(api.asked.map((a) => a.conversation)).size).toBe(1);
});

test("an uploaded CSV file has nothing to refresh", async ({ page }) => {
  await mockApi(page, { datasets: [SALES_CSV] });
  await page.goto("/");
  await page.getByRole("button", { name: /^shop_sales\.csv/ }).click();
  await expect(bar(page)).toContainText("Asking about shop_sales.csv");
  await expect(bar(page).getByRole("button", { name: "Refresh from the sheet" })).toHaveCount(0);
});

test("a link that isn't a Google Sheet is refused before it is sent", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/");
  await importSheet(page, "https://example.com/data.csv");
  await expect(page.getByRole("alert")).toContainText("This isn't a Google Sheets link");
  expect(api.imported).toEqual([]);
});

test("a sheet that isn't shared says how to share it", async ({ page }) => {
  await mockApi(page, {
    importSheet: () => ({
      status: 422,
      body: { status: 422, code: "sheet_not_public", title: "This sheet isn't shared by link", detail: "In Google Sheets, choose Share, set General access to \"Anyone with the link\", then try again." },
    }),
  });
  await page.goto("/");
  await importSheet(page);
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("This sheet isn't shared by link");
  await expect(alert).toContainText("Anyone with the link");
  await expect(link(page)).toHaveValue(SHEET_URL); // kept, to try again once shared
});

test("a refresh that fails leaves the sheet as it was and says why", async ({ page }) => {
  await mockApi(page, {
    datasets: [SHEET],
    refresh: () => ({ status: 502, body: { status: 502, code: "sheet_unavailable", title: "Google Sheets didn't answer", detail: "Try again in a minute." } }),
  });
  await page.goto("/");
  await page.getByRole("button", { name: /^Shop Sales - Q1\.csv/ }).click();
  await bar(page).getByRole("button", { name: "Refresh from the sheet" }).click();
  await expect(bar(page).getByRole("alert")).toContainText("Google Sheets didn't answer");
  await expect(bar(page)).toContainText("1,250 rows");
});

for (const scheme of ["light", "dark"] as const) {
  test(`importing a sheet has no accessibility violations (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await mockApi(page);
    await page.goto("/");
    await expect(link(page)).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await importSheet(page);
    await bar(page).getByText(/See the columns/).click();
    await expect(bar(page).getByRole("link", { name: "this Google Sheet" })).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}

test("the sheet link form fits a phone @mobile", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await link(page).fill(SHEET_URL);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBe(0);
  await page.getByRole("button", { name: "Import the sheet" }).click();
  await expect(bar(page).getByRole("button", { name: "Refresh from the sheet" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
});
