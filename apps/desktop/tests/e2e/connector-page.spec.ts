// Slack config is a detail SUBPAGE under Connectors (UX-DECISIONS §21): the list row
// navigates to it, and the §19 flows — parked senders (Allow & deliver / Allow / ×)
// and "listening" sessions — are filed under the workspace they belong to.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openSlackPage(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click(); // triggers login
  await expect(page.getByTestId("account-row")).toContainText("Rohit", { timeout: 10_000 });
  await page.getByTestId("account-row").click(); // now signed in → opens menu
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
  await page.getByTestId("connector-slack").click();
}

test("list row status + navigation to the Slack page", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("account-row").click(); // triggers login
  await expect(page.getByTestId("account-row")).toContainText("Rohit", { timeout: 10_000 });
  await page.getByTestId("account-row").click(); // now signed in → opens menu
  await page.getByRole("button", { name: "Connectors", exact: true }).click();

  const row = page.getByTestId("connector-slack");
  await expect(row).toContainText("2 workspaces · relay");
  await row.click();
  await expect(page.getByTestId("slack-workspaces")).toBeVisible();
  // Signed in (the nav requires it) → the relay badge reports the live state;
  // "sign-in paused" only shows for a signed-out cloud account.
  await expect(page.getByTestId("slack-mode-badge")).toContainText("Live");
});

test("parked sender files under ITS workspace; Allow & deliver adds to that allow-list only", async ({
  page,
}) => {
  await openSlackPage(page);

  // pk1 belongs to T1DL — its Waiting row renders in that workspace's group only.
  const t1 = page.getByTestId("slack-workspace-T1DL");
  await expect(t1.getByTestId("waiting-pk1")).toContainText("Maya");
  await expect(t1.getByTestId("waiting-pk1")).toContainText("in #delta-test");
  await expect(t1.getByTestId("waiting-pk1")).toContainText("hey ocw, can you summarize this thread?");
  await expect(page.getByTestId("slack-workspace-T2AC").getByTestId("waiting-pk1")).toHaveCount(0);

  await page.getByTestId("parked-allow-deliver-pk1").click();
  await expect(page.getByTestId("waiting-pk1")).toHaveCount(0);
  // The sender lands on the T1DL allow-list; the sibling workspace stays empty.
  await expect(t1).toContainText("U0NEW");
  await expect(page.getByTestId("slack-workspace-T2AC")).not.toContainText("U0NEW");
});

test("parked sender can be dismissed without allowing", async ({ page }) => {
  await openSlackPage(page);
  await page.getByTestId("parked-dismiss-pk1").click();
  await expect(page.getByTestId("waiting-pk1")).toHaveCount(0);
  await expect(page.getByTestId("slack-workspace-T1DL")).not.toContainText("U0NEW");
});

test("sessions listening in a workspace: listed with unsubscribe", async ({ page }) => {
  await openSlackPage(page);

  const t1 = page.getByTestId("slack-workspace-T1DL");
  await expect(t1.getByTestId("listening-slack")).toContainText("Weekly plan 1");
  await expect(t1.getByTestId("listening-slack")).toContainText("#delta-test");

  await t1.getByTitle("Unsubscribe this session").click();
  await expect(t1.getByTestId("listening-slack")).toHaveCount(0); // row hides when empty
});
