import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

async function expectContainedLayout(page: Page) {
  const state = await page.evaluate(() => {
    const selectors = [".app", ".main", ".main-workspace", ".main-chat", ".main-scroll"];
    const bounds = (element: Element) => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, right: rect.right };
    };
    return {
      page: {
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
      },
      boxes: selectors.map((selector) => {
        const element = document.querySelector<HTMLElement>(selector)!;
        return { selector, client: element.clientWidth, scroll: element.scrollWidth };
      }),
      mainScrollOverflowX: getComputedStyle(
        document.querySelector<HTMLElement>(".main-scroll")!,
      ).overflowX,
      prose: bounds(document.querySelector<HTMLElement>("[data-testid=overflow-prose]")!),
      transcript: bounds(document.querySelector<HTMLElement>(".transcript")!),
      thinking: bounds(document.querySelector<HTMLElement>("[data-testid=thinking-body]")!),
    };
  });

  expect(state.page.scroll).toBeLessThanOrEqual(state.page.client + 1);
  for (const box of state.boxes) {
    expect(box.scroll, box.selector).toBeLessThanOrEqual(box.client + 1);
  }
  expect(state.mainScrollOverflowX).toBe("hidden");
  expect(state.prose.left).toBeGreaterThanOrEqual(state.transcript.left - 1);
  expect(state.prose.right).toBeLessThanOrEqual(state.transcript.right + 1);
  expect(state.thinking.left).toBeGreaterThanOrEqual(state.transcript.left - 1);
  expect(state.thinking.right).toBeLessThanOrEqual(state.transcript.right + 1);
}

test("long transcript content stays inside the chat while code and tables self-scroll", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1180, height: 760 });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".app")).not.toHaveClass(/boot-splash/);

  // Mount a deterministic transcript-shaped fixture into the real chat scroller. This keeps
  // the regression about CSS containment, independent of WebSocket timing or model behavior.
  await page.locator(".main-scroll").evaluate((scroller) => {
    const transcript = document.createElement("div");
    transcript.className = "transcript";

    const thinkingWrap = document.createElement("div");
    thinkingWrap.className = "thinking";
    const thinking = document.createElement("div");
    thinking.className = "thinking-body";
    thinking.dataset.testid = "thinking-body";
    thinking.textContent = "超长思考内容".repeat(180) + " reasoning-without-breaks".repeat(120);
    thinkingWrap.append(thinking);
    transcript.append(thinkingWrap);

    const fixture = document.createElement("div");
    fixture.className = "bubble-assistant layout-overflow-fixture";
    const markdown = document.createElement("div");
    markdown.className = "md";

    const prose = document.createElement("p");
    prose.dataset.testid = "overflow-prose";
    prose.textContent =
      "普通中文正文".repeat(100) +
      " EnglishContentWithoutWhitespace".repeat(70) +
      " https://example.test/" +
      "very-long-url-segment".repeat(90);
    markdown.append(prose);

    const pre = document.createElement("pre");
    pre.dataset.testid = "overflow-code";
    const code = document.createElement("code");
    code.textContent = `const token = "${"code-without-breaks".repeat(100)}";`;
    pre.append(code);
    markdown.append(pre);

    const table = document.createElement("table");
    table.dataset.testid = "overflow-table";
    const row = table.insertRow();
    for (let index = 0; index < 12; index += 1) {
      const cell = document.createElement("th");
      cell.textContent = `column-${index}-${"wide".repeat(20)}`;
      row.append(cell);
    }
    markdown.append(table);
    fixture.append(markdown);
    transcript.append(fixture);
    scroller.replaceChildren(transcript);
  });

  // Default state: both the left nav and right rail are visible.
  await expect(page.locator(".main")).toHaveClass(/rail-open/);
  await expectContainedLayout(page);

  const code = page.getByTestId("overflow-code");
  const table = page.getByTestId("overflow-table");
  for (const localScroller of [code, table]) {
    await expect(localScroller).toHaveCSS("overflow-x", "auto");
    const dimensions = await localScroller.evaluate((element) => ({
      client: element.clientWidth,
      scroll: element.scrollWidth,
    }));
    expect(dimensions.scroll).toBeGreaterThan(dimensions.client);
  }

  // Hide/show the rail and collapse/restore the nav: every supported width combination stays bounded.
  await page.getByRole("button", { name: "Hide side panel" }).click();
  await expectContainedLayout(page);
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expectContainedLayout(page);
  await page.getByRole("button", { name: "Show side panel" }).click();
  await expectContainedLayout(page);
  await page.getByRole("button", { name: "Show sidebar" }).click();
  await expectContainedLayout(page);
});
