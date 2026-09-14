import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Sidebar } from "./Sidebar";
import { I18nProvider } from "@delta/i18n/I18nContext";
import type { SessionInfo } from "../types";

const api = vi.hoisted(() => ({
  getAutomations: vi.fn().mockResolvedValue([]),
}));

vi.mock("../api", () => ({
  AUTOMATIONS_CHANGED: "delta:automations-changed",
  getAutomations: api.getAutomations,
}));

const SESSIONS: SessionInfo[] = [
  {
    session_id: "s-research-doe",
    title: "DOE — factor screening",
    workspace: "C:/Delta/s-research-doe",
    agent: "delta",
    model: "m",
    mode: "interactive",
    updated_at: "2026-07-01",
    messages: 4,
  },
  {
    session_id: "s-office-batch",
    title: "batch rename 100 images",
    workspace: "C:/Delta/s-office-batch",
    agent: "delta",
    model: "m",
    mode: "interactive",
    updated_at: "2026-06-29",
    messages: 2,
  },
];

const baseProps = {
  sessions: SESSIONS,
  activeSession: "s-research-doe",
  onNewSession: vi.fn(),
  onSelectSession: vi.fn(),
  onRenameSession: vi.fn(),
  onDeleteSession: vi.fn(),
  onArchiveSession: vi.fn(),
  onTogglePin: vi.fn(),
  onSetReasoningEffort: vi.fn(),
  onManage: vi.fn(),
  onOpenScheduled: vi.fn(),
  onOpenAutomation: vi.fn(),
  onOpenIntegrations: vi.fn(),
  onOpenAudit: vi.fn(),
  onOpenInbox: vi.fn(),
  scheduledActive: false,
  integrationsActive: false,
  auditActive: false,
  inboxActive: false,
};

function renderSidebar(props = baseProps) {
  return render(
    <I18nProvider locale="en-US">
      <Sidebar {...props} />
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  api.getAutomations.mockResolvedValue([]);
});

describe("Delta task sidebar", () => {
  it("starts and selects Delta tasks without an identity picker", () => {
    renderSidebar();

    fireEvent.click(screen.getByText("New task"));
    expect(baseProps.onNewSession).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByText("batch rename 100 images"));
    expect(baseProps.onSelectSession).toHaveBeenCalledWith(
      "s-office-batch",
      "C:/Delta/s-office-batch",
    );
  });

  it("keeps rename, pin, archive, reasoning and confirmed delete behind the row menu", () => {
    renderSidebar();
    const openMenu = () => fireEvent.click(screen.getAllByTestId("row-menu")[0]);

    openMenu();
    fireEvent.click(screen.getByTestId("row-menu-rename"));
    const input = screen.getByDisplayValue("DOE — factor screening");
    fireEvent.change(input, { target: { value: "factor screening final" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(baseProps.onRenameSession).toHaveBeenCalledWith(
      "s-research-doe",
      "factor screening final",
    );

    openMenu();
    fireEvent.click(screen.getByTestId("row-menu-pin"));
    expect(baseProps.onTogglePin).toHaveBeenCalledWith("s-research-doe", true);

    openMenu();
    fireEvent.click(screen.getByTestId("row-menu-archive"));
    expect(baseProps.onArchiveSession).toHaveBeenCalledWith("s-research-doe", true);

    openMenu();
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Deep" }));
    expect(baseProps.onSetReasoningEffort).toHaveBeenCalledWith("s-research-doe", "high");

    openMenu();
    fireEvent.click(screen.getByTestId("row-menu-delete"));
    expect(baseProps.onDeleteSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("row-menu-delete"));
    expect(baseProps.onDeleteSession).toHaveBeenCalledWith("s-research-doe");
  });

  it("renders Slack origin inside Recent rather than creating an identity band", () => {
    const slack: SessionInfo = {
      ...SESSIONS[0],
      session_id: "s-slack-1",
      title: "#general — check the deploy?",
      updated_at: "2026-07-13",
      origin: "slack",
      origin_label: "#general · T0AB",
    };
    renderSidebar({ ...baseProps, sessions: [...SESSIONS, slack] });

    const row = screen.getByText("#general — check the deploy?").closest(".group");
    expect(row?.querySelector('[data-logo="slack"]')).toBeTruthy();
    expect(screen.queryByTestId("from-slack-toggle")).toBeNull();
  });
});
