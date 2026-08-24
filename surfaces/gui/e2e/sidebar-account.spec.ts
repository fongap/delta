// The sidebar bottom is now four uniform icon buttons in a row: Inbox, Activity, the
// account row (Sign-in / Account), and Settings. Contract under test: no "Settings &
// more" button; the inbox badge lives on the inbox footer icon (clicks STRAIGHT to
// Inbox, never opens the menu); the account-row triggers login directly when signed out
// and opens an account menu (Connectors + Sign out only) when signed in.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("the bottom is four uniform footer icons — the old rows are gone", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("sidebar-footer-inbox")).toBeVisible();
  await expect(page.getByTestId("sidebar-footer-activity")).toBeVisible();
  await expect(page.getByTestId("account-row")).toBeVisible();
  await expect(page.getByTestId("sidebar-footer-settings")).toBeVisible();
  await expect(page.getByRole("button", { name: /Settings & more/i })).toHaveCount(0);
  // The ONLY "Inbox" button in the sidebar is the footer icon — no standalone nav row.
  await expect(page.locator(".sidebar").getByRole("button", { name: "Inbox", exact: true })).toHaveCount(1);
  await expect(page.locator(".sidebar").getByRole("button", { name: "Inbox", exact: true })).toHaveAttribute("data-testid", "sidebar-footer-inbox");
});

test("pending items: the inbox badge clicks straight to Inbox — no menu", async ({
  page,
}) => {
  await page.goto("/");
  const inbox = page.getByTestId("sidebar-footer-inbox");
  await expect(inbox).toBeVisible(); // fixtures seed pending attention → accent count
  await inbox.click();
  await expect(page.getByTestId("account-menu")).toHaveCount(0); // never opens the menu
  await expect(page.getByText("Approve: run_shell")).toBeVisible(); // Inbox opened directly
});

test("the account menu (when signed in) contains only Connectors + Sign out", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("account-row").click(); // triggers login directly
  await expect(page.getByTestId("account-row")).toContainText("Rohit", { timeout: 10_000 });
  await page.getByTestId("account-row").click(); // signed in → opens account menu
  const menu = page.getByTestId("account-menu");
  await expect(menu).toContainText("rohit@openworker.com");
  await expect(menu.getByRole("button", { name: "Connectors", exact: true })).toBeVisible();
  await expect(menu.getByRole("button", { name: "Sign out" })).toBeVisible();
  // The old menu items are gone — Inbox/Activity/Settings/Automations are now footer icons.
  await expect(menu.getByRole("button", { name: "Inbox", exact: true })).toHaveCount(0);
  await expect(menu.getByRole("button", { name: "Activity", exact: true })).toHaveCount(0);
  await expect(menu.getByRole("button", { name: "Settings", exact: true })).toHaveCount(0);
  await expect(menu.getByRole("button", { name: "Automations", exact: true })).toHaveCount(0);
});

test("Activity is a footer icon (audit log); Connectors ▸ MCP servers; Unrouted under Inbox ▸ Configure", async ({
  page,
}) => {
  await page.goto("/");
  // Activity is now a direct footer icon, not in the menu.
  await page.getByTestId("sidebar-footer-activity").click();
  await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();

  // §28: Messaging routing left the Connectors sub-nav entirely (Connectors · MCP only)…
  await page.getByTestId("account-row").click(); // login
  await expect(page.getByTestId("account-row")).toContainText("Rohit", { timeout: 10_000 });
  await page.getByTestId("account-row").click(); // menu
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
  await expect(page.getByRole("button", { name: "MCP servers" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Messaging routing/ })).toHaveCount(0);

  // …and Unrouted rides the Inbox's Configure tab.
  await page.getByTestId("sidebar-footer-inbox").click();
  await page.getByTestId("inbox-tab-configure").click();
  await expect(page.getByTestId("unrouted-section")).toBeVisible();
});
