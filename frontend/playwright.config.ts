/**
 * Playwright smoke tests. Start the backend (port 8000) and the frontend (port 3000) first,
 * or let `webServer` below start them (reuses servers that are already running).
 */

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 90_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    viewport: { width: 1440, height: 900 },
    colorScheme: "dark",
  },
  // Every test runs in Chromium; Firefox and WebKit (Safari's engine) run the page smoke tests.
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    {
      name: "firefox",
      testMatch: /smoke\.spec\.ts/,
      use: { ...devices["Desktop Firefox"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "webkit",
      testMatch: /smoke\.spec\.ts/,
      use: { ...devices["Desktop Safari"], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: [
    {
      command: "..\\backend\\.venv\\Scripts\\python.exe -m uvicorn causaltwin.api.app:app --port 8000",
      cwd: "../backend",
      url: "http://localhost:8000/health",
      reuseExistingServer: true,
      timeout: 180_000,
    },
    {
      command: "npx next start -p 3000",
      url: "http://localhost:3000",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
