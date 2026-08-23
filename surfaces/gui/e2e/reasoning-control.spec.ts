import { expect, test } from "./fixtures";

test("composer reasoning control updates the session and survives reload", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const trigger = page.getByTestId("reasoning-menu-trigger");
  await expect(trigger).toContainText("Default");
  await trigger.click();
  await page.getByRole("menuitemradio", { name: /High/ }).click();
  await expect(trigger).toContainText("High");

  await page.reload();
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByTestId("reasoning-menu-trigger")).toContainText("High");
});
