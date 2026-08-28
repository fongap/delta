import { expect, test } from "./fixtures";

test("composer reasoning control updates the session and survives reload", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const trigger = page.getByTestId("reasoning-menu-trigger");
  await expect(trigger).toContainText("Default");
  await trigger.click();
  await page.getByRole("menuitemradio", { name: /Deep/ }).click();
  await expect(trigger).toContainText("Deep");

  await page.reload();
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByTestId("reasoning-menu-trigger")).toContainText("Deep");
});
