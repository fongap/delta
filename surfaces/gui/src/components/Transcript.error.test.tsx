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
});
