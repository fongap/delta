// First-run onboarding (UX-DECISIONS §24 → §29 → §39): model → your tools → go.
// Step 1 shows ONLY the inline "Custom provider" create card plus any user-defined
// custom-provider cards — built-in providers are no longer listed. Next arms once a
// custom provider is configured inline; the tools-page tests below create one first.
// Entered via the REPLAY path (Settings ▸ Appearance ▸ "Run setup again") — which is
// itself under test.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openOnboarding(page) {
  await page.goto("/");
  await page.getByTestId("sidebar-footer-settings").click();
  await page.getByRole("button", { name: "Run setup again" }).click();
  await expect(page.getByTestId("ob-step-model")).toBeVisible();
}

// The gallery now lists no built-in providers, so Next is disabled until a custom
// provider is configured. Create one inline to arm Next, then continue.
async function createCustomToArmNext(page) {
  await page.getByTestId("ob-alias").fill("myapi");
  await page.getByTestId("ob-field-api_key").fill("sk-myapi-realkey");
  await page.getByTestId("ob-create-save").click();
  // Saved providers show a "· Saved" chip (the old "✓ Connected" label was replaced).
  await expect(page.getByTestId("ob-provider-myapi")).toContainText("Saved");
}

test("tools page: sign-in morphs the page into the connector gallery; a card connects one-click", async ({
  page,
}) => {
  await openOnboarding(page);
  await createCustomToArmNext(page);
  await page.getByTestId("ob-continue").click();
  await expect(page.getByTestId("ob-step-tools")).toBeVisible();

  // Pre-sign-in (§41): the benefit rows are already there (no Connect buttons yet),
  // the combined Google row says Coming soon, the band asks for sign-in, and the one
  // footer button is the quiet "Continue without sign-in".
  await expect(page.getByText("Chat can only advise")).toBeVisible();
  await expect(page.getByTestId("ob-tool-outlook")).toContainText("Email, from your inbox");
  await expect(page.getByTestId("ob-tool-outlook").getByRole("button")).toHaveCount(0);
  await expect(page.getByTestId("ob-tool-attio")).toContainText("Your team's CRM");
  await expect(page.getByTestId("ob-tool-google-soon")).toContainText("Coming soon");
  await expect(page.getByText("Sign in for one-click connections")).toBeVisible();
  await expect(page.getByTestId("ob-tools-skip")).toContainText("Continue without sign-in");

  // Sign-in lands out-of-band; the band's SLOT stays put and flips to the congrats
  // (zero layout shift), and every row grows its Connect pill.
  await page.getByTestId("ob-cloud-signin").click();
  await expect(page.getByTestId("ob-tools-signedin")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("ob-tools-signedin")).toContainText("You’re signed in");
  await expect(
    page.getByTestId("ob-tool-attio").getByRole("button", { name: "Connect" }),
  ).toBeVisible();
  await expect(page.getByTestId("ob-tool-google-soon").getByRole("button")).toHaveCount(0);

  // One-click connect: the consent completes in the (mock) browser; the poll flips the
  // row to ✓ Connected. Next was armed the whole time — connecting is optional.
  await page.getByTestId("ob-tool-outlook").getByRole("button", { name: "Connect" }).click();
  await expect(page.getByTestId("ob-tool-outlook")).toContainText("✓ Connected", {
    timeout: 10_000,
  });
  await expect(page.getByTestId("ob-continue-tools")).toBeEnabled();
  await page.getByTestId("ob-continue-tools").click();

  // Done step: the automation CTA lands on the Automations quickstart.
  await expect(page.getByTestId("ob-step-done")).toBeVisible();
  await page.getByTestId("ob-cta-automation").click();
  await expect(page.getByTestId("onboarding")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Automations" })).toBeVisible();
});

test("tools page skips cleanly; Start working lands in a session with the panel open", async ({
  page,
}) => {
  await openOnboarding(page);
  await createCustomToArmNext(page);
  await page.getByTestId("ob-continue").click();
  await page.getByTestId("ob-tools-skip").click();
  await expect(page.getByTestId("ob-step-done")).toBeVisible();
  await page.getByTestId("ob-start").click();
  await expect(page.getByTestId("onboarding")).toHaveCount(0);
  // §32: "Start working" lands with the rail's Access section expanded (the drawer is gone).
  await expect(page.getByRole("region", { name: "Session access" })).toBeVisible();
});
