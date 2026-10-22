import { defineConfig, devices } from "@playwright/test";

// The tests run against the production build with the API mocked (e2e/mock.ts), so they need no backend.
// PW_CHROMIUM points at a browser that is already installed, instead of downloading one.
const executablePath = process.env.PW_CHROMIUM || undefined;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { baseURL: "http://localhost:4173", trace: "retain-on-failure", launchOptions: { executablePath } },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], launchOptions: { executablePath } }, grepInvert: /@mobile/ },
    { name: "mobile", use: { ...devices["Pixel 7"], launchOptions: { executablePath } }, grep: /@mobile/ },
  ],
  webServer: { command: "npm run preview", url: "http://localhost:4173", reuseExistingServer: !process.env.CI },
});
