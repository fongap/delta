// Slack config is a detail SUBPAGE under Connectors (UX-DECISIONS §21).
// P1 cleanup: the managed-relay multi-workspace model is gone; Slack is now
// single-workspace manual Socket Mode. The "workspace" panels are kept for
// parity (a future Federation Adapter may reintroduce multi-workspace), but
// fixtures and asserts are rewritten to match the new model.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openSlackPage(page) {
  await page.goto("/");
  await page.getByTestId("sidebar-footer-integrations").click();
  await page.getByTestId("connector-slack").click();
}

test("list row status + navigation to the Slack page", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("sidebar-footer-integrations").click();

  const row = page.getByTestId("connector-slack");
  // P1: manual Socket Mode (single workspace). The status line is the
  // workspace account name; no "2 workspaces · relay" suffix.
  await expect(row).toContainText("Slack");
  await row.click();
  await expect(page.getByTestId("slack-workspaces")).toBeVisible();
  // The mode badge reports "Connected (Socket Mode)" for the manual path.
  await expect(page.getByTestId("slack-mode-badge")).toContainText("Socket");
});

test("parked sender files under the connected workspace; Allow & deliver adds to that allow-list", async ({
  page,
}) => {
  await openSlackPage(page);

  // P1: only one workspace (T1DL) is connected. pk1's Waiting row renders there.
  const t1 = page.getByTestId("slack-workspace-T1DL");
  await expect(t1.getByTestId("waiting-pk1")).toContainText("Maya");
  await expect(t1.getByTestId("waiting-pk1")).toContainText("in #delta-test");
  await expect(t1.getByTestId("waiting-pk1")).toContainText("hey ocw, can you summarize this thread?");

  await page.getByTestId("parked-allow-deliver-pk1").click();
  await expect(page.getByTestId("waiting-pk1")).toHaveCount(0);
  await expect(t1).toContainText("U0NEW");
});

test("parked sender can be dismissed without allowing", async ({ page }) => {
  await openSlackPage(page);
  await page.getByTestId("parked-dismiss-pk1").click();
  await expect(page.getByTestId("waiting-pk1")).toHaveCount(0);
  await expect(page.getByTestId("slack-workspace-T1DL")).not.toContainText("U0NEW");
});

test("sessions listening in the workspace: listed with unsubscribe", async ({ page }) => {
  await openSlackPage(page);

  const t1 = page.getByTestId("slack-workspace-T1DL");
  await expect(t1.getByTestId("listening-slack")).toContainText("Weekly plan 1");
  await expect(t1.getByTestId("listening-slack")).toContainText("#delta-test");

  await t1.getByTitle("Unsubscribe this session").click();
  await expect(t1.getByTestId("listening-slack")).toHaveCount(0); // row hides when empty
});
