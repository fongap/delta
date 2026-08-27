import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Transcript } from "./Transcript";
import { I18nProvider } from "../i18n/I18nContext";
import type { Item } from "../types";

afterEach(cleanup);

const items: Item[] = [{
  kind: "notice",
  tone: "warn",
  text: "Error: Gateway misconfigured: NODES_CONFIG is missing",
  retriable: true,
}];

const view = (running: boolean, onRetry = vi.fn()) => (
  <I18nProvider locale="en-US">
    <Transcript items={items} running={running} onApprove={() => {}} onRetry={onRetry} />
  </I18nProvider>
);

describe("provider error and retry presentation", () => {
  it("keeps infrastructure detail folded behind a localized summary", () => {
    const onRetry = vi.fn();
    render(view(false, onRetry));
    expect(screen.getByText("Provider temporarily unavailable. You can retry.")).toBeTruthy();
    expect(screen.queryByText(/NODES_CONFIG/)).toBeNull();
    fireEvent.click(screen.getByText("Show details"));
    expect(screen.getByText(/NODES_CONFIG/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("notice-retry"));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("shows the current attempt and removes the retry action while running", () => {
    render(view(true));
    expect(screen.getByTestId("notice-retrying").textContent).toContain("Retrying");
    expect(screen.queryByTestId("notice-retry")).toBeNull();
  });

  it("does not relabel ordinary warnings as provider failures", () => {
    render(
      <I18nProvider locale="en-US">
        <Transcript
          items={[{ kind: "notice", tone: "warn", text: "Interrupted." }]}
          running={false}
          onApprove={() => {}}
        />
      </I18nProvider>,
    );
    expect(screen.getByText("Interrupted.")).toBeTruthy();
    expect(screen.queryByText("Provider temporarily unavailable. You can retry.")).toBeNull();
  });

  it("merges consecutive provider errors into one notice with a retry count", () => {
    const onRetry = vi.fn();
    const err = (text: string) => ({ kind: "notice", tone: "warn", text, retriable: true }) as const;
    render(
      <I18nProvider locale="en-US">
        <Transcript
          items={[err("Error: boom 1"), err("Error: boom 2"), err("Error: boom 3")]}
          running={false}
          onApprove={() => {}}
          onRetry={onRetry}
        />
      </I18nProvider>,
    );
    // One block, not three: a single summary + one "retried" suffix.
    expect(screen.getAllByText(/Provider temporarily unavailable/)).toHaveLength(1);
    expect(screen.getByText(/retried 2×/)).toBeTruthy();
    // The latest raw error stays expandable; the single retry action still works.
    fireEvent.click(screen.getByText("Show details"));
    expect(screen.getByText(/boom 3/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("notice-retry"));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders model-switch markers as compact runtime events", () => {
    render(
      <I18nProvider locale="en-US">
        <Transcript
          items={[
            { kind: "notice", tone: "info", text: "Model switched to FongAI:pro", modelSwitchModel: "FongAI:pro" },
          ]}
          running={false}
          onApprove={() => {}}
        />
      </I18nProvider>,
    );
    const el = screen.getByText("Switched to FongAI:pro");
    expect(el.className).toContain("notice-event");
  });
});
