// P0 security regression: HTML artifact previews render in a fully locked iframe —
// no scripts, no same-origin. An escaped artifact must never reach the main webview's
// origin (where the sidecar token and the native bridge live).
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { RightRail } from "./RightRail";
import { I18nProvider } from "@delta/i18n/I18nContext";
import type { JSX } from "react";

const { getArtifactsMock, readArtifactMock } = vi.hoisted(() => ({
  getArtifactsMock: vi.fn(),
  readArtifactMock: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return {
    ...original,
    getArtifacts: (...a: unknown[]) => getArtifactsMock(...a),
    readArtifact: (...a: unknown[]) => readArtifactMock(...a),
  };
});

const HTML_ARTIFACT = {
  path: "report.html",
  name: "report.html",
  abs_path: "/tmp/ws/report.html",
};

const props = () => ({
  active: true,
  sessionId: "s1",
  refreshKey: 0,
  toolNames: [],
  todo: [] as never[],
  running: false,
  showArtifacts: true,
});

const wrap = (el: JSX.Element) => <I18nProvider locale="en-US">{el}</I18nProvider>;

describe("HTML artifact sandbox isolation", () => {
  beforeEach(() => {
    getArtifactsMock.mockResolvedValue([HTML_ARTIFACT]);
  });

  const openPreview = async () => {
    render(wrap(<RightRail {...props()} />));
    fireEvent.click(await screen.findByRole("button", { name: /report\.html/i }));
  };

  it("renders the preview in a fully locked frame: no scripts, no same-origin", async () => {
    readArtifactMock.mockResolvedValue({ kind: "html", content: "<p>hi</p>" });
    await openPreview();

    const frame = await waitFor(() => {
      const el = document.querySelector<HTMLIFrameElement>("iframe.artifact-frame");
      expect(el).toBeTruthy();
      return el!;
    });
    const tokens = frame.getAttribute("sandbox")?.split(/\s+/).filter(Boolean) ?? [];
    expect(tokens).not.toContain("allow-scripts");
    expect(tokens).not.toContain("allow-same-origin");
    // Locked entirely: an empty sandbox attribute denies everything by default.
    expect(tokens).toEqual([]);
  });

  it("a malicious artifact body cannot change its own sandbox", async () => {
    const malicious =
      '<script>parent.__DELTA_API_TOKEN__ = "stolen"; window.top.location = "https://evil.example";</script><p>pwn</p>';
    readArtifactMock.mockResolvedValue({ kind: "html", content: malicious });
    await openPreview();

    const frame = await waitFor(() => {
      const el = document.querySelector<HTMLIFrameElement>("iframe.artifact-frame");
      expect(el).toBeTruthy();
      return el!;
    });
    const tokens = frame.getAttribute("sandbox")?.split(/\s+/).filter(Boolean) ?? [];
    // The attribute is set by React, not by the embedded document; with the empty
    // sandbox the script inside can neither run nor touch parent/top.
    expect(tokens).toEqual([]);
  });
});
