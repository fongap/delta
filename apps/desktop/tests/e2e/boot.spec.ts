// Cold-boot fixes (owner-hit 2026-07-23): the splash wears the real Delta mark
// (the 4-point sparkle logo SVG, not the ✦ text glyph that read as another product's logo),
// and the model picker recovers when the mount-time settings fetch loses the race against
// native runtime boot — previously "Loading models…" stuck until the user visited Settings.
import { expect } from "@playwright/test";
import { test, overrideMockCommand, patchMockState } from "./fixtures";

test("boot splash shows the Delta mark, not a text glyph", async ({ page }) => {
  // Hold health long enough to observe the splash.
  await overrideMockCommand(page, "health", `async () => { await new Promise((resolve) => setTimeout(resolve, 1500)); return $state.health; }`);
  await page.goto("/");
  const mark = page.locator(".boot-mark");
  await expect(mark).toBeVisible();
  await expect(mark.locator("svg")).toBeVisible(); // the Icon logo, not a text glyph
  await expect(mark).not.toContainText("✦");
  await expect(page.getByText(/Starting Delta|Restoring your session/)).toBeVisible();
});

test("model picker recovers when settings fetches fail during native runtime boot", async ({ page }) => {
  // Real cold-start shape: settings reads fail until the native runtime is ready,
  // then everything answers. The mount-time settings fetches all lose that race and are
  // swallowed — the post-health reload must populate the picker without a Settings visit.
  await patchMockState(page, { runtimeReady: false });
  await overrideMockCommand(page, "health", `async () => { await new Promise((resolve) => setTimeout(resolve, 700)); $state.runtimeReady = true; return $state.health; }`);
  await overrideMockCommand(page, "settings_get", `async () => { if (!$state.runtimeReady) throw new Error("runtime starting"); return { ...$state.settings }; }`);
  await page.goto("/");
  await expect(page.locator(".dd").filter({ hasText: "Claude Opus 4.8" })).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("models-loading")).toHaveCount(0);
});
