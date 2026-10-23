import { expect, test } from "@playwright/test";
import { mockApi, STARTERS } from "./mock";

const QUESTION = "Why did orders for marketplace change in the week of 22 Dec 2025?";

test("last week's unusual moves are offered as questions", async ({ page }) => {
  const api = await mockApi(page, {
    insights: {
      week_start: "2025-12-22", generated_at: "2025-12-29T01:30:00Z",
      insights: [{ headline: "Marketplace orders doubled to 240, from a usual 115.", metric: "Orders", dimension: "Channel",
        group: "marketplace", value: 240, usual: 115, z: 6.2, sql: "SELECT 1", question: QUESTION }],
    },
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /What changed in the week of/ })).toBeVisible();
  await page.getByRole("button", { name: /Marketplace orders doubled/ }).click();
  await expect.poll(() => api.asked.map((a) => a.question)).toEqual([QUESTION]);
});

test("before the first nightly run there is no insights section", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: STARTERS[0] })).toBeVisible();
  await expect(page.getByRole("heading", { name: /What changed/ })).toHaveCount(0);
});
