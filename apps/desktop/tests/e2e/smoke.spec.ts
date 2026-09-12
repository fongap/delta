import { test, expect } from "./fixtures";

test("app loads with the persona nav and composer", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Delta").first()).toBeVisible();
  // New task + Search are the fixed top nav.
  await expect(page.getByRole("button", { name: /New task/i })).toBeVisible();
  // Delta is the only persona surface (R6.0).
  await expect(page.getByText("Delta", { exact: true })).toHaveCount(1);
});
