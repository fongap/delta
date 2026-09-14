// OPE-27 — auto-compaction GUI: the Settings card's two overrides + summarizer-model
// pin POST through, and the "context compacted" divider renders inline mid-session
// (driven by the fixtures' scripted `compacted` event) without touching the transcript.
import { expect } from "@playwright/test";
import { test, readMockState } from "./fixtures";

test("Settings: Context compaction card edits threshold, cap, and summarizer model", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("sidebar-footer-settings").click();
  await page.getByRole("button", { name: "Models", exact: true }).click();

  const card = page.getByTestId("compaction-card");
  await expect(card).toBeVisible();
  await expect(card.getByText("Context compaction")).toBeVisible();

  // Defaults render when the backend doesn't send the fields (older-backend robustness).
  await expect(card.getByTestId("compaction-threshold")).toHaveValue("80");
  await expect(card.getByTestId("compaction-cap")).toHaveValue("250000");
  await expect(card.getByTestId("compaction-model")).toHaveValue("");

  // Threshold edits POST as a fraction, clamped to 10–95%.
  await card.getByTestId("compaction-threshold").fill("70");
  await expect.poll(async () => (await readMockState<any>(page)).settings.compaction_threshold_pct).toBe(0.7);

  await card.getByTestId("compaction-cap").fill("100000");
  await expect.poll(async () => (await readMockState<any>(page)).settings.compaction_cap_tokens).toBe(100000);

  // Summarizer pin: the picker offers the session-default plus the configured models.
  await card.getByTestId("compaction-model").selectOption("gpt-4o-mini");
  await expect.poll(async () => (await readMockState<any>(page)).settings.compaction_model).toBe("gpt-4o-mini");
});

test("the compacted divider renders mid-session and the transcript stays intact", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask Delta/);

  // An earlier exchange that must survive the compaction marker (transcript intact).
  await box.fill("remember the launch date");
  await box.press("Enter");
  await expect(page.getByText("Echo: remember the launch date").first()).toBeVisible({
    timeout: 10_000,
  });

  await box.fill("compact the context");
  await box.press("Enter");
  // The transient signal shows while the summarizer runs, then yields to the divider.
  await expect(page.getByText("Compacting context…").first()).toBeVisible({
    timeout: 10_000,
  });
  await expect(
    page.getByText("Context compacted — earlier turns were summarized").first(),
  ).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Compacting context…")).toHaveCount(0);
  await expect(
    page.getByText("Still on it — continuing where I left off.").first(),
  ).toBeVisible();
  // Outbound-only: everything before the divider is still on screen.
  await expect(page.getByText("Echo: remember the launch date").first()).toBeVisible();
});
