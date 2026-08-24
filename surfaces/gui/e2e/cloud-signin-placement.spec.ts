// Regression guard (shipped once, 2026-07-09; reshaped by §26): cloud sign-in must be
// reachable by a FRESH user. The sidebar account row is the permanent sign-in home —
// always visible, never below any fold — and every signed-out one-click pane carries a
// real Sign-in button, not a hint pointing at another page.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openConnectors(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click(); // signed out → triggers login directly
  await expect(page.getByTestId("account-row")).toContainText("Rohit", { timeout: 10_000 });
  await page.getByTestId("account-row").click(); // now signed in → opens account menu
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
}

test("the account row is always visible and signs in directly when clicked", async ({ page }) => {
  await page.goto("/");
  const row = page.getByTestId("account-row");
  await expect(row).toBeVisible();
  await expect(row).toContainText("Not signed in");

  // Signed out: clicking the row triggers login directly (no account-sign-in button).
  await row.click();
  await expect(row).toContainText("Rohit", { timeout: 10_000 });

  // Sign out is right there in the account menu once signed in.
  await row.click();
  await expect(
    page.getByTestId("account-menu").getByRole("button", { name: "Sign out" }),
  ).toBeVisible();
});

test("fresh user: sign-in from the account row, then the connector connects one-click", async ({ page }) => {
  await openConnectors(page);
  // §26 nav: reaching Connectors means the fresh user already signed in via the
  // account row (the old signed-out inline sign-in pane inside the modal is only
  // reachable if cloud state flips while the modal is open — not a UI path anymore).
  await page
    .getByTestId("connector-gmail")
    .getByRole("button", { name: "Connect", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /Connect Gmail with one click/i }),
  ).toBeVisible({ timeout: 10_000 });
});
