import { defineConfig, devices } from "@playwright/test";

// The widget runs on a made-up site (https://shop.example) against a mocked API (https://api.example): both are
// served by page.route in the tests, so nothing listens locally. Build first: `npm run build`.
const executablePath = process.env.PW_CHROMIUM || undefined;

export default defineConfig({
  testDir: "test",
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { ...devices["Desktop Chrome"], trace: "retain-on-failure", launchOptions: { executablePath } },
});
