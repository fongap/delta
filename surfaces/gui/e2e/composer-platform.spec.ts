import { test, expect } from "./fixtures";

// The macOS overlay layout (traffic-light insets) must never apply on Windows —
// Windows keeps its native title bar (alignment bug, 2026-07-21). The shell injects
// __OCW_PLATFORM__ and (with withGlobalTauri off) the __TAURI_INTERNALS__ global that
// isTauri() keys off — the @tauri-apps/api package no longer reads a window.__TAURI__
// global. These specs simulate the desktop shell's injection of both.
test("windows platform gets no tauri-overlay layout", async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__TAURI_INTERNALS__ = {}; // simulate the desktop shell
    (window as any).__OCW_PLATFORM__ = "windows";
  });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-platform", "windows");
  await expect(page.locator(".app.tauri-overlay")).toHaveCount(0);
});

test("macos platform keeps the overlay layout", async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).__TAURI_INTERNALS__ = {};
    (window as any).__OCW_PLATFORM__ = "macos";
  });
  await page.goto("/");
  await expect(page.locator(".app.tauri-overlay").first()).toBeVisible();
});
