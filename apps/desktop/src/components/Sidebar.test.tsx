import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Sidebar } from "./Sidebar";
import { I18nProvider } from "@delta/i18n/I18nContext";
import type { SessionInfo } from "../types";

// All Sidebar renders go through the provider so the sidebar's useI18n() calls resolve.
function renderSidebar(ui: React.ReactElement) {
  return render(<I18nProvider locale="en-US">{ui}</I18nProvider>);
}

// Hermetic fetch stub routing by URL substring + method; records calls for POST assertions.
type Call = { url: string; method: string; body: any };

function stubFetch(routes: { match: string; method?: string; json: any }[]) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    for (const r of routes) {
      if (url.includes(r.match) && (!r.method || r.method === method)) {
        return { ok: true, json: async () => r.json } as Response;
      }
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

// Delta is the only enabled persona (R6.0); a disabled "legacy" row is included to exercise the
// enabled-filter without creating a second product surface.
const PERSONAS = {
  personas: [
    { id: "delta", name: "Delta", icon: "delta", tagline: "office, research, content, scripts", family: "knowledge", enabled: true, surfaced: true, default: true },
    { id: "legacy", name: "Disabled Legacy", icon: "delta", tagline: "off", family: "knowledge", enabled: false, surfaced: false, default: false },
  ],
};

const SESSIONS: SessionInfo[] = [
  { session_id: "s-research-doe", title: "DOE — factor screening", workspace: "", agent: "delta", model: "m", mode: "interactive", updated_at: "2026-07-01", messages: 4 },
  { session_id: "s-office-batch", title: "batch rename 100 images", workspace: "", agent: "delta", model: "m", mode: "interactive", updated_at: "2026-06-29", messages: 2 },
];

const baseProps = {
  agent: "delta",
  workspace: "",
  surfaces: { delta: true },
  sessions: SESSIONS,
  projects: [],
  activeSession: "s-research-doe",
  onSwitchAgent: vi.fn(),
  onNewSession: vi.fn(),
  onSelectSession: vi.fn(),
  onNewProject: vi.fn(),
  onRenameSession: vi.fn(),
  onDeleteSession: vi.fn(),
  onArchiveSession: vi.fn(),
  onTogglePin: vi.fn(),
    onSetReasoningEffort: vi.fn(),
  onManage: vi.fn(),
  onOpenPersona: vi.fn(),
  onManagePersonas: vi.fn(),
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Sidebar group/filter control", () => {
  it("choosing Persona persists via setNavLayout and switches to the per-persona accordion", async () => {
    const calls = stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
      { match: "/v1/settings/nav-layout", method: "POST", json: { ok: true, nav_layout: "grouped" } },
    ]);
    renderSidebar(<Sidebar {...baseProps} />);

    const control = await screen.findByLabelText("Group and filter conversations");
    fireEvent.click(control);
    fireEvent.click(await screen.findByText("By persona"));

    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST" && c.url.includes("/v1/settings/nav-layout"));
      expect(post).toBeTruthy();
      expect(post!.body).toMatchObject({ nav_layout: "grouped" });
    });

    fireEvent.click(control);

    // Grouped view = the per-persona accordion. Delta is the only group; the active surface's
    // body renders its sessions. Both Delta sessions stay visible inside the expanded group.
    expect(screen.getByText("DOE — factor screening")).toBeTruthy();
    expect(screen.getByText("batch rename 100 images")).toBeTruthy();
  });
});

describe("Chronological list row actions (⋮ menu)", () => {
  // The Recent list sorts by updated_at desc; index 0 = s-research-doe (most recent).
  const openMenu = () => fireEvent.click(screen.getAllByTestId("row-menu")[0]);

  it("rename / pin / archive / two-step delete all live behind the row's single kebab", async () => {
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
    ]);
    renderSidebar(<Sidebar {...baseProps} />);
    await screen.findByText("DOE — factor screening");

    openMenu();
    fireEvent.click(screen.getByTestId("row-menu-rename"));
    const input = screen.getByDisplayValue("DOE — factor screening");
    fireEvent.change(input, { target: { value: "factor screening final" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(baseProps.onRenameSession).toHaveBeenCalledWith("s-research-doe", "factor screening final");

    openMenu();
    fireEvent.click(screen.getByTestId("row-menu-pin"));
    expect(baseProps.onTogglePin).toHaveBeenCalledWith("s-research-doe", true);

    openMenu();
    fireEvent.click(screen.getByTestId("row-menu-archive"));
    expect(baseProps.onArchiveSession).toHaveBeenCalledWith("s-research-doe", true);

    openMenu();
    fireEvent.click(screen.getByTestId("row-menu-delete"));
    expect(baseProps.onDeleteSession).not.toHaveBeenCalled();
    expect(screen.getByTestId("row-menu-delete").textContent).toContain("Delete?");
    fireEvent.click(screen.getByTestId("row-menu-delete"));
    expect(baseProps.onDeleteSession).toHaveBeenCalledWith("s-research-doe");
  });

  it("the kebab and its menu never select the row; Escape closes the menu", async () => {
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
    ]);
    renderSidebar(<Sidebar {...baseProps} />);
    await screen.findByText("DOE — factor screening");

    openMenu();
    fireEvent.click(screen.getByTestId("row-menu-pin"));
    expect(baseProps.onSelectSession).not.toHaveBeenCalled();

    openMenu();
    expect(screen.getByTestId("row-menu-rename")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("row-menu-rename")).toBeNull();
  });
});

describe("From Slack group (§31)", () => {
  const SLACK_SESSION: SessionInfo = {
    session_id: "s-slack-1",
    title: "#general — check the deploy?",
    workspace: "",
    agent: "delta",
    model: "m",
    mode: "interactive",
    updated_at: "2026-07-13",
    messages: 2,
    origin: "slack",
    origin_label: "#general · T0AB",
  };

  it("mention-spawned sessions list chronologically in Recent with the platform icon (no band)", async () => {
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
    ]);
    renderSidebar(<Sidebar {...baseProps} sessions={[...SESSIONS, SLACK_SESSION]} />);
    await screen.findByText("DOE — factor screening");

    expect(screen.queryByTestId("from-slack-toggle")).toBeNull();
    const row = await screen.findByText("#general — check the deploy?");
    expect(screen.getAllByText("#general — check the deploy?")).toHaveLength(1);

    const cluster = row.closest(".group");
    expect(cluster?.querySelector('[data-logo="slack"]')).toBeTruthy();
  });
});

describe("New-session button", () => {
  it("collapses to a plain button when only one persona is enabled (Delta-only)", async () => {
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
    ]);
    const { container } = renderSidebar(<Sidebar {...baseProps} />);
    await screen.findByText("DOE — factor screening");

    // No ▾ — nothing to pick; the primary button starts the sole enabled persona.
    await waitFor(() => expect(screen.queryByLabelText("Choose a persona")).toBeNull());
    fireEvent.click(container.querySelector(".newsplit-primary")!);
    expect(baseProps.onNewSession).toHaveBeenCalledWith("delta");
  });

  it("hides Manage personas… while the launch flag is off (the default)", async () => {
    localStorage.removeItem("delta.flag.personas");
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
    ]);
    renderSidebar(<Sidebar {...baseProps} />);
    await screen.findByLabelText("Group and filter conversations");
    // Only Delta enabled → no ▾ persona menu at all.
    expect(screen.queryByLabelText("Choose a persona")).toBeNull();
  });
});