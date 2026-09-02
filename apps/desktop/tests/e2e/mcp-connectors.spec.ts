// MCP-backed connectors (UX-DECISIONS §42): monday/asana/jira connect through the
// vendor's hosted MCP server via a fully LOCAL OAuth flow — one-click without any
// cloud sign-in — and agents get only the PINNED tool subset, surfaced on the
// connector detail page like any other curated tool set.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openConnectors(page) {
  await page.goto("/");
  await page.getByTestId("sidebar-footer-integrations").click();
}

test("monday: one-click MCP connect without cloud sign-in; card flips connected", async ({
  page,
}) => {
  await openConnectors(page);

  // MCP one-click needs no cloud account — the local OAuth against monday's hosted MCP
  // server runs entirely on this computer (ADR-004 D-3, UX-DECISIONS §42).
  await page
    .getByTestId("connector-monday")
    .getByRole("button", { name: "Connect" })
    .click();
  const modal = page.getByTestId("add-connection-modal");
  await expect(modal).toBeVisible();
  // Single-mode: no cloud sign-in gate — just the one-click button.
  await expect(modal.getByTestId("mcp-one-click")).toBeVisible();

  await modal.getByTestId("mcp-one-click").click();
  // The mock flow completes instantly; the modal's poll closes it and the card flips.
  await expect(page.getByTestId("add-connection-modal")).toHaveCount(0, {
    timeout: 10_000,
  });
  await expect(page.getByTestId("connector-monday")).toContainText("Connected");
});

test("monday detail page shows the pinned tool subset with approval badges", async ({
  page,
}) => {
  await openConnectors(page);
  await page.getByTestId("connector-monday").click();
  await expect(page.getByText("2 tools this connector adds")).toBeVisible();
  await page.getByText("View", { exact: true }).click();
  await expect(page.getByText("Read board", { exact: true })).toBeVisible();
  await expect(page.getByText("Create item", { exact: true })).toBeVisible();
});
