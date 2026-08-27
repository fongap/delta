// Session-screen cleanup (§22): the contextual top-left cluster ([sidebar][+], rendered
// ONLY while the sidebar is collapsed), the title-only topbar, and the model picker in the
// composer.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("top-left cluster renders only while the sidebar is collapsed", async ({ page }) => {
  await page.goto("/");

  // Expanded sidebar owns those actions — no duplicate cluster.
  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.getByTestId("topbar-cluster")).toHaveCount(0);

  // Collapse → the cluster appears with both actions; the floating reveal button does NOT
  // double up on the session surface (the cluster's sidebar button replaces it).
  await page.keyboard.press("Meta+b");
  const cluster = page.getByTestId("topbar-cluster");
  await expect(cluster).toBeVisible();
  await expect(cluster.getByRole("button", { name: "Show sidebar" })).toBeVisible();
  await expect(cluster.getByRole("button", { name: "New task" })).toBeVisible();
  await expect(page.locator(".nav-reveal-btn")).toHaveCount(0);

  // The cluster's sidebar button docks the nav back — and the cluster leaves with it.
  await cluster.getByRole("button", { name: "Show sidebar" }).click();
  await expect(page.locator(".app")).not.toHaveClass(/nav-collapsed/);
  await expect(page.getByTestId("topbar-cluster")).toHaveCount(0);
});

test("session topbar keeps only the title while the model picker remains available", async ({
  page,
}) => {
  await page.goto("/");

  // No subtitle or old About-persona button; the model remains a live picker in the composer.
  await expect(page.getByTestId("session-subtitle")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "About this persona" })).toHaveCount(0);
  await expect(page.locator(".dd").filter({ hasText: "Claude Opus 4.8" })).toBeVisible();

  // A completed turn must not add a second title row or reserve subtitle space.
  const box = page.getByPlaceholder(/Ask Delta/);
  await box.fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: hello/)).toBeVisible();

  await expect(page.getByTestId("session-subtitle")).toHaveCount(0);
  await expect(page.locator(".dd").filter({ hasText: "Claude Opus 4.8" })).toBeVisible();
  const title = page.locator(".main-title-text");
  await expect(title).toHaveText("Draft the launch note");
  await expect(title).toHaveCSS("text-overflow", "ellipsis");
  await expect(page.locator(".main-title").locator(":scope > *")).toHaveCount(1);
});

test("composer is three controls (+ attach · Mode · send); folder and branch chips are gone", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  await expect(page.getByRole("button", { name: "Attach" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Mode", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  // The folder/roots popover trigger and the standalone Inbox control left the composer (§22).
  await expect(page.getByTitle(/director(y|ies) the agent can use/)).toHaveCount(0);
  await expect(page.getByTitle("Inbox routing")).toHaveCount(0);
  await expect(page.locator(".wschip")).toHaveCount(0);
  await expect(page.locator(".wsbranch")).toHaveCount(0);
});
