import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";
import { I18nProvider } from "@delta/i18n/I18nContext";

const baseProps = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "fong:code-max",
  models: ["fong:code-max"],
  running: false,
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});
const renderComposer = (extra: Partial<Parameters<typeof Composer>[0]> = {}) =>
  render(
    <I18nProvider locale="en-US">
      <Composer {...baseProps(extra)} />
    </I18nProvider>,
  );

afterEach(cleanup);

describe("composer runtime controls", () => {
  it("shows the active reasoning effort beside the model and updates the session setting", () => {
    const onReasoningEffortChange = vi.fn();
    renderComposer({ reasoningEffort: "low", onReasoningEffortChange });
    const trigger = screen.getByTestId("reasoning-menu-trigger");
    expect(trigger.textContent).toContain("Light");
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Deep/ }));
    expect(onReasoningEffortChange).toHaveBeenCalledWith("high");
  });

  it("disables reasoning changes while a task is running", () => {
    renderComposer({ reasoningEffort: "low", onReasoningEffortChange: vi.fn(), running: true });
    expect(screen.getByTestId("reasoning-menu-trigger").hasAttribute("disabled")).toBe(true);
  });
});

describe("composer attachment entry points", () => {
  it("labels the constrained picker honestly", () => {
    renderComposer();
    fireEvent.click(screen.getByLabelText("Attach files"));
    expect(screen.getByText("Text and code")).toBeTruthy();
    expect(screen.getByText(/Office files, archives/)).toBeTruthy();
  });

  it("picker, drop, and paste show the same unsupported-format rejection", async () => {
    const { container } = renderComposer();
    const zip = new File(["binary"], "archive.zip", { type: "application/zip" });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [zip] } });
    await screen.findByText(/archive\.zip: Unsupported format/);

    fireEvent.drop(container.querySelector(".composer")!, {
      dataTransfer: { files: [zip] },
    });
    await waitFor(() => expect(screen.getByTestId("attach-notice").textContent).toContain("archive.zip: Unsupported format"));

    fireEvent.paste(container.querySelector("textarea")!, {
      clipboardData: {
        items: [{ kind: "file", type: "application/zip", getAsFile: () => zip }],
      },
    });
    await waitFor(() => expect(screen.getByTestId("attach-notice").textContent).toContain("archive.zip: Unsupported format"));
  });
});
