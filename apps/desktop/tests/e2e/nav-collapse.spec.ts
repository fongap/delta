// R6 left-nav polish: collapse (⌘B / brand button → reveal button docks it back) and the
// identity-collapsed Recent list, with no legacy persona grouping controls.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("collapse hides the sidebar and reclaims the width; reveal button docks it back", async ({
  page,
}) => {
  await page.goto("/");
  const app = page.locator(".app");
  await expect(page.locator(".sidebar")).toBeVisible();

  // Collapse via the brand button.
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(app).toHaveClass(/nav-collapsed/);
  // The floating reveal affordance appears; clicking it docks the nav back.
  const reveal = page.getByRole("button", { name: "Show sidebar" });
  await expect(reveal).toBeVisible();
  await reveal.click();
  await expect(app).not.toHaveClass(/nav-collapsed/);
});

test("⌘B toggles the sidebar collapse", async ({ page }) => {
  await page.goto("/");
  // Wait for boot to finish: the ⌘B keydown handler mounts only after the splash clears,
  // so a press fired mid-boot is silently dropped (the very first `.app` paint carries
  // `boot-splash`). Same guard as session-shell.spec.ts.
  await expect(page.locator(".app")).not.toHaveClass(/boot-splash/);
  const app = page.locator(".app");
  await page.keyboard.press("Meta+b");
  await expect(app).toHaveClass(/nav-collapsed/);
  await page.keyboard.press("Meta+b");
  await expect(app).not.toHaveClass(/nav-collapsed/);
});

test("Recent is a fixed flat list without legacy persona grouping controls", async ({
  page,
}) => {
  await page.goto("/");
  const header = page.getByTestId("recent-header");
  await expect(header).toContainText("Recent");

  await expect(header.getByRole("button", { name: "Group and filter conversations" })).toHaveCount(0);
  await expect(page.getByTestId("group-filter-menu")).toHaveCount(0);
  await expect(page.getByTitle("Weekly plan 1")).toBeVisible();
});
