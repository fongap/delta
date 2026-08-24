// Cloud sign-in (§26: the sidebar account row is the sign-in home) + managed one-click
// connectors. Product invariant under test: manual token setup is always present; managed
// one-click is an ADDITION that appears only when signed in.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openConnectors(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click(); // signed out → triggers login directly
  await expect(page.getByTestId("account-row")).toContainText("Rohit", { timeout: 10_000 });
  await page.getByTestId("account-row").click(); // now signed in → opens account menu
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Connectors" })).toBeVisible();
}

async function signIn(page) {
  await page.getByTestId("account-row").click(); // signed out → triggers login directly
  await expect(page.getByTestId("account-row")).toContainText("Rohit", { timeout: 10_000 });
}

test("signed out: the account row is the sign-in home; signed in it opens the account menu", async ({
  page,
}) => {
  await page.goto("/");
  const row = page.getByTestId("account-row");
  await expect(row).toContainText("Not signed in");

  // Signed out → clicking triggers login directly (no menu).
  await row.click();
  await expect(row).toContainText("Rohit", { timeout: 10_000 });

  // Signed in → clicking opens the account menu (identity + Connectors + Sign out).
  await row.click();
  const menu = page.getByTestId("account-menu");
  await expect(menu).toContainText("rohit@openworker.com");
  // The manual-vs-one-click connector invariant lives on the connector pages
  // (see github/hubspot specs): one-click requires the signed-in state this flow creates.
});

test("signed in: account row shows the name; one-click appears; sign out from the menu", async ({
  page,
}) => {
  await openConnectors(page);

  await page.getByTestId("connector-gmail").getByRole("button", { name: "Connect", exact: true }).click();
  const modal = page.getByTestId("add-connection-modal");
  await expect(modal.getByRole("button", { name: /Connect Gmail with one click/i })).toBeVisible();
  // the manual path must still be offered alongside
  await expect(modal.getByTestId("managed-connect")).toContainText("or connect manually");
  await page.keyboard.press("Escape");

  // The menu header carries the email; Sign out flips the row back.
  await page.getByTestId("account-row").click();
  const menu = page.getByTestId("account-menu");
  await expect(menu).toContainText("rohit@openworker.com");
  await menu.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByTestId("account-row")).toContainText("Not signed in", { timeout: 10_000 });
});

test("telemetry/Privacy card is gone from Settings (owner ask 2026-07-22), signed in or out", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("sidebar-footer-settings").click();
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
  await expect(page.getByTestId("telemetry-toggle")).toHaveCount(0);
  await expect(page.getByText("Privacy", { exact: true })).toHaveCount(0);

  await signIn(page);
  await page.getByTestId("sidebar-footer-settings").click();
  await expect(page.getByTestId("telemetry-toggle")).toHaveCount(0);
  await expect(page.getByText("Privacy", { exact: true })).toHaveCount(0);
});
