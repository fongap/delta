import { defineConfig, devices } from "@playwright/test";

// E2E harness for the GUI. Tests are hermetic: Tauri invoke/listen is mocked in-page before the
// SPA loads (see tests/e2e/fixtures.ts), so they run without native state or external services.
const PORT = 5199;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // Dev server on a dedicated port so it never collides with a running `npm run dev` (5173).
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
