// Sheet preview regression (P1 security fix): workbooks are parsed server-side and the
// GUI renders only the structured JSON payload — it must never parse xlsx itself.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { fireEvent, waitFor } from "@testing-library/react";
import { RightRail } from "./RightRail";
import { I18nProvider } from "../i18n/I18nContext";

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

const SHEET_ARTIFACT = {
  path: "report.xlsx",
  name: "report.xlsx",
  abs_path: "/tmp/ws/report.xlsx",
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

describe("SheetViewer renders server-side sheet JSON", () => {
  beforeEach(() => {
    getArtifactsMock.mockResolvedValue([SHEET_ARTIFACT]);
  });

  const openPreview = async () => {
    render(wrap(<RightRail {...props()} />));
    fireEvent.click(await screen.findByRole("button", { name: /report\.xlsx/i }));
  };

  it("renders sheet tabs and grid cells from the JSON payload", async () => {
    readArtifactMock.mockResolvedValue({
      ok: true,
      path: "report.xlsx",
      kind: "sheet",
      sheets: [
        { name: "Data", rows: [["Item", "Qty"], ["Widget", 3]], total_rows: 2, truncated: false },
        { name: "Notes", rows: [], total_rows: 0, truncated: false },
      ],
    });
    await openPreview();

    expect(await screen.findByText("Widget")).toBeTruthy();
    expect(screen.getByText("Item")).toBeTruthy();
    // Multi-sheet workbook shows tabs; switch to the empty one.
    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    await waitFor(() => expect(screen.getByText("Empty sheet.")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    await waitFor(() => expect(screen.getByText("Widget")).toBeTruthy());
  });

  it("shows a row-cap note for truncated sheets", async () => {
    const body = Array.from({ length: 500 }, (_, i) => [`r${i}`]);
    readArtifactMock.mockResolvedValue({
      ok: true,
      path: "report.xlsx",
      kind: "sheet",
      sheets: [{ name: "Big", rows: [["h"], ...body], total_rows: 1200, truncated: true }],
    });
    await openPreview();

    await screen.findByText("r499");
    expect(screen.getByText(/Showing 500 of 1199 rows/)).toBeTruthy();
  });
});
