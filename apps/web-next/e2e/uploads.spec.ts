import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { ARNAV, message, mockApi, SALES_CSV, STARTERS } from "./mock";

const CSV = { name: "shop_sales.csv", mimeType: "text/csv", buffer: Buffer.from("Order Date,City,Contact,Amount (INR)\n2025-01-01,Bengaluru,a@b.in,120\n") };

async function uploadCsv(page: Page, file = CSV) {
  await page.getByLabel("Upload a CSV file").setInputFiles(file);
}

const bar = (page: Page) => page.getByRole("region", { name: "Uploaded file" });

test("an uploaded file is asked about with questions that fit its columns", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/");
  await uploadCsv(page);
  await expect(page.getByRole("heading", { level: 2, name: "What do you want to know about shop_sales.csv?" })).toBeVisible();
  await expect(bar(page)).toContainText("Asking about shop_sales.csv: 1,250 rows and 4 columns, 1 kept private");
  expect(api.uploaded).toEqual(["shop_sales.csv"]);

  await bar(page).getByText(/See the columns/).click();
  await expect(bar(page).getByRole("row", { name: /contact/ })).toContainText("Hidden from answers: email address");
  await expect(bar(page).getByRole("row", { name: /amount_inr/ })).toContainText("Amount (INR)");
  await expect(page.getByRole("textbox", { name: "Your question" })).toHaveAttribute("placeholder", "Ask a question about shop_sales.csv");

  await page.getByRole("button", { name: "Total amount inr by city" }).click();
  await expect(page.getByText("Ask next")).toBeVisible();
  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill("and by month?");
  await box.press("Enter");
  await expect.poll(() => api.asked.map((a) => a.dataset)).toEqual([SALES_CSV.id, SALES_CSV.id]);
  expect(new Set(api.asked.map((a) => a.conversation)).size).toBe(1);

  // A new conversation stays with the file; going back to the demo leaves it.
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "What do you want to know about shop_sales.csv?" })).toBeVisible();
  await bar(page).getByRole("button", { name: "Back to the sales demo" }).click();
  await expect(page.getByRole("heading", { name: "What do you want to know about the store's sales?" })).toBeVisible();
  await expect(bar(page)).toHaveCount(0);
  await page.getByRole("button", { name: STARTERS[0] }).click();
  await expect(page.getByText("Ask next")).toBeVisible();
  expect(api.asked.at(-1)!.dataset).toBeNull();
});

test("a file that can't be used says why and what to do", async ({ page }) => {
  await mockApi(page, {
    upload: () => ({ status: 422, body: { status: 422, code: "not_csv", title: "This looks like an Excel file", detail: "Save it as CSV (File > Save As > CSV UTF-8) and upload that." } }),
  });
  await page.goto("/");
  await uploadCsv(page, { ...CSV, name: "book.xlsx" });
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("This looks like an Excel file");
  await expect(alert).toContainText("Save it as CSV");
  await expect(page.getByRole("heading", { name: "What do you want to know about the store's sales?" })).toBeVisible();
});

test("a file over the size limit is refused before it is sent", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/");
  await uploadCsv(page, { ...CSV, buffer: Buffer.alloc(5 * 1024 * 1024 + 1, "a") });
  await expect(page.getByRole("alert")).toContainText("Files can be up to 5 MB.");
  expect(api.uploaded).toEqual([]);
});

test("files uploaded earlier can be asked about again or deleted", async ({ page }) => {
  const older = { ...SALES_CSV, id: "8d1e6f3a-2b4c-4d5e-9f60-718293a4b5c6", name: "returns.csv", rows: 40 };
  const api = await mockApi(page, { datasets: [SALES_CSV, older] });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your uploads" })).toBeVisible();
  await page.getByRole("button", { name: "Delete returns.csv" }).click();
  await expect(page.getByRole("button", { name: /^returns\.csv/ })).toHaveCount(0);
  expect(api.datasets.map((d) => d.id)).toEqual([SALES_CSV.id]);

  await page.getByRole("button", { name: /^shop_sales\.csv/ }).click();
  await expect(bar(page)).toContainText("Asking about shop_sales.csv");
});

const conversation = "6f1c2b7e-1d1a-4a51-9a39-3a4f7b2c9d10";

function historyAbout(datasetId: string) {
  const first = { ...message("0b3e3a8e-5c1d-4d5e-8f7a-1b2c3d4e5f60", conversation, "Total amount inr by city"), dataset_id: datasetId };
  return [{
    conversation: { id: conversation, title: "Total amount inr by city", messages: 1, dataset_id: datasetId, updated_at: new Date().toISOString() },
    messages: [first],
  }];
}


test("an earlier conversation about an uploaded file is carried on about that file", async ({ page }) => {
  const api = await mockApi(page, { user: ARNAV, datasets: [SALES_CSV], history: historyAbout(SALES_CSV.id) });
  await page.goto("/");
  await page.getByRole("button", { name: "History" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /Total amount inr by city/ }).click();
  await expect(bar(page)).toContainText("Asking about shop_sales.csv");
  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill("only Bengaluru");
  await box.press("Enter");
  await expect.poll(() => api.asked.at(-1)).toMatchObject({ conversation, dataset: SALES_CSV.id });
});

test("an earlier conversation whose file was deleted shows its answers but asks nothing more", async ({ page }) => {
  const api = await mockApi(page, { user: ARNAV, history: historyAbout(SALES_CSV.id) });
  await page.goto("/");
  await page.getByRole("button", { name: "History" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /Total amount inr by city/ }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Total amount inr by city" })).toBeVisible();
  await expect(bar(page)).toContainText("The file this conversation was about has been deleted");
  await expect(page.getByRole("textbox", { name: "Your question" })).toBeDisabled();

  await bar(page).getByRole("button", { name: "Ask about the sales demo instead" }).click();
  await expect(page.getByRole("heading", { name: "What do you want to know about the store's sales?" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Your question" })).toBeEnabled();
  expect(api.asked).toEqual([]);
});

for (const scheme of ["light", "dark"] as const) {
  test(`uploading has no accessibility violations (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await mockApi(page, { datasets: [SALES_CSV] });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Your uploads" })).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await uploadCsv(page);
    await bar(page).getByText(/See the columns/).click();
    await expect(bar(page).getByRole("table")).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}

test("an uploaded file's columns fit a phone @mobile", async ({ page }) => {
  await mockApi(page, { upload: () => ({ status: 201, body: { ...SALES_CSV, name: "a_really_long_export_name_from_the_billing_system_2025.csv" } }) });
  await page.goto("/");
  await uploadCsv(page);
  await bar(page).getByText(/See the columns/).click();
  await expect(bar(page).getByRole("table")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBe(0);
});
