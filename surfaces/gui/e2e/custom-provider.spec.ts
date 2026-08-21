// Custom-config-first provider flow (F1 + F2): the Models tab shows ONE first-class
// "Custom provider" card with the create form inline (alias + protocol dropdown +
// fields + Fetch models + Create & save) — no "Add" button to click first. Fill →
// Fetch models (auto-adds `alias:{id}`) → Create & save → the new provider's card
// appears below with its ✓ Connected state.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test.describe("custom provider", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sidebar-footer-settings").click();
    await page.getByRole("button", { name: "Models", exact: true }).click();
    // Built-in providers are no longer listed — readiness is the always-visible inline
    // create form's alias field (no "Add custom provider" button to click first).
    await expect(page.getByTestId("set-alias")).toBeVisible();
  });

  test("add a custom OpenAI-compatible provider, fetch models, save", async ({ page }) => {
    // 1. The inline create form is already open (alias + protocol dropdown default OpenAI-compatible).
    await expect(page.getByTestId("set-protocol")).toHaveValue("openai-compatible");

    // 2. Type the alias.
    await page.getByTestId("set-alias").fill("myapi");

    // 3. Fill the API key field (non-"bad" so verify/fetch succeed).
    await page.getByTestId("set-field-api_key").fill("sk-myapi-realkey");

    // 4. Fetch models → success message + models auto-added.
    await page.getByTestId("set-fetch").click();
    await expect(page.getByTestId("set-fetch-msg")).toContainText("Fetched 2 model(s)");

    // 5. Create & save → the alias is registered, verified, and the card appears.
    await page.getByTestId("set-create-save").click();
    await expect(page.getByTestId("set-provider-myapi")).toContainText("✓ Connected");
  });
});
