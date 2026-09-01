// Custom-config-first provider flow (F1 + F2): the Models tab shows ONE first-class
// "Custom provider" card with the create form inline (alias + protocol dropdown +
// fields + Fetch models + Create & save) — no "Add" button to click first. Fill →
// Fetch models (auto-adds `alias:{id}`) → Create & save → the new provider's card
// appears below with alias as identity and protocol/status as secondary information.
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

  test("add a custom OpenAI provider, fetch models, save", async ({ page }) => {
    // 1. The inline create form is already open with the OpenAI protocol selected.
    await expect(page.getByTestId("set-protocol")).toHaveValue("openai");

    // 2. Type the alias.
    await page.getByTestId("set-alias").fill("myapi");

    // 3. Fill a fixture-only mock key (never sent to a real service).
    await page.getByTestId("set-field-api_key").fill("mock-provider-key");

    // 4. Fetch models → success message + the fetched model list stays visible (the form
    // does NOT reset and wipe the chips the instant they arrived). The alias is registered
    // and the key stored by the fetch itself, so creation is complete.
    await page.getByTestId("set-fetch").click();
    await expect(page.getByTestId("set-fetch-msg")).toContainText("Fetched 2 model(s)");
    await expect(page.getByTestId("fetched-models")).toBeVisible();

    // 5. Fetch itself registers the alias and stores the key (backend parity), so the
    // gallery card reflects the saved state. The create form stays open so the user can
    // pick a default model from the chips, then close it when done.
    const card = page.getByTestId("set-provider-myapi");
    await expect(card).toContainText("myapi");
    await expect(card).toContainText("Saved");
  });
});
