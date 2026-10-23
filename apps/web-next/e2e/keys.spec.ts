import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { ApiKey } from "../src/api/types";
import { mockApi } from "./mock";

const KEY: ApiKey = {
  id: "0b6f7c1e-2d3a-4b5c-8d9e-0f1a2b3c4d5e", name: "Shop widget", hint: "aak_exam", origins: ["https://shop.example"],
  created_at: "2026-10-20T08:00:00Z", last_used_at: null,
};

test("a new key is shown once and can be revoked", async ({ page }) => {
  await mockApi(page);
  let keys: ApiKey[] = [];
  let made: unknown = null;
  await page.route("**/v1/keys", (route) => {
    if (route.request().method() === "POST") {
      made = route.request().postDataJSON();
      keys = [KEY];
      return route.fulfill({ status: 201, json: { ...KEY, key: "aak_example" } });
    }
    return route.fulfill({ json: keys });
  });
  await page.route(`**/v1/keys/${KEY.id}`, (route) => {
    keys = [];
    return route.fulfill({ status: 204 });
  });

  await page.goto("/keys");
  await expect(page.getByText("None yet.")).toBeVisible();
  await page.getByLabel("What it is for").fill("Shop widget");
  await page.getByLabel("Sites that may use it in a browser").fill("https://shop.example");
  await page.getByRole("button", { name: "Make a key" }).click();

  await expect(page.getByText("aak_example", { exact: true })).toBeVisible();
  await expect(page.getByText('<nl-analyst base-url="http://localhost:4173" api-key="aak_example">')).toBeVisible();
  expect(made).toEqual({ name: "Shop widget", origins: ["https://shop.example"] });
  await expect(page.getByText("never used, for https://shop.example")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await page.getByRole("button", { name: "Revoke Shop widget" }).click();
  await expect(page.getByText("None yet.")).toBeVisible();
  await expect(page.getByText("aak_example")).toHaveCount(0);
});

test("making a key needs a sign-in", async ({ page }) => {
  await mockApi(page);
  await page.route("**/v1/keys", (route) =>
    route.fulfill({ status: 401, contentType: "application/problem+json", json: { code: "sign_in_required", title: "Sign in to manage API keys", status: 401 } }));
  await page.goto("/keys");
  await expect(page.getByText("Sign in to manage API keys").first()).toBeVisible();
});
