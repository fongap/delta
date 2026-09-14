import { test as base, expect, type Page } from "@playwright/test";
import { installMockRuntimeInitScript } from "./mockRuntime";

// R6 hermetic transport. R6 removed the localhost FastAPI/WebSocket backend; the frontend now
// talks to Rust ONLY through Tauri `invoke`/`listen`. The e2e harness runs in a plain browser
// (Vite dev), so `installMockRuntimeInitScript` injects a mock `__TAURI_INTERNALS__` before the
// SPA loads: `invoke("cmd")` is answered from the per-test in-memory state below, and the fake
// agent (RuntimeHost stand-in) pushes `delta-runtime-event` envelopes straight into the page's
// `listen("delta-runtime-event")`. No HTTP, no WebSocket — production code is untouched.

const appEventSequences = new WeakMap<Page, number>();

/** Push an app-wide event (sessionId:null) to the GUI, exactly as the window-wide event path
 *  delivers it. `__DELTA_MOCK__.emit` forwards to the page's registered listeners. */
export async function sendAppEvent(
  page: Page,
  event: { type: string; payload: Record<string, unknown> },
): Promise<void> {
  const sequence = (appEventSequences.get(page) ?? 0) + 1;
  appEventSequences.set(page, sequence);
  await page.evaluate(
    ([eventName, payload, eventSequence]) => {
      const mock = (window as any).__DELTA_MOCK__;
      if (!mock || typeof mock.emit !== "function") {
        throw new Error("mock transport not installed");
      }
      mock.emit("delta-runtime-event", {
        type: eventName,
        version: 1,
        sessionId: null,
        sequence: eventSequence,
        payload,
      });
    },
    [event.type, event.payload, sequence] as const,
  );
}

const HEALTH = {
  status: "ok",
  default_workspace: null,
  model: "anthropic:claude-opus-4-8",
  protocolVersion: 1,
  capabilities: [
    "events.app-wide",
    "provider.custom",
    "session.message-revert",
    "session.reasoning-effort",
  ],
};

const SETTINGS = {
  provider: "openai",
  model: "anthropic:claude-opus-4-8",
  models: ["anthropic:claude-opus-4-8", "gpt-5.5", "gpt-4o", "gpt-4o-mini", "o3-mini"],
  has_key: true,
  model_ready: true,
  source: "store",
  onboarded: true,
  experimental_connectors: false,
  surfaces: { delta: true },
  nav_layout: "grouped",
  scratch_base: "~/Delta",
  secrets_path: "/Users/test/.config/delta/secrets.json",
  sessions_peek: 6,
  pdf_fallback: "text",
  pdf_max_pages: 2,
  pdf_max_mb: 10,
  context_bar: false,
  model_labels: {
    "anthropic:claude-opus-4-8": "Claude Opus 4.8 · Anthropic",
    "zai:glm-5.2": "GLM-5.2 · Z AI",
  },
  model_context_windows: {
    "anthropic:claude-opus-4-8": 200_000,
  },
};

const PERSONAS = {
  personas: [
    { id: "delta", name: "Delta", icon: "delta", tagline: "Produce a deliverable — office, research, content, scripts", needs_workspace: true, builtin: true, family: "knowledge", workspace: "deliverable", tools: ["files", "search", "shell", "todo"], enabled: true, surfaced: true, default: true },
  ],
};

const PINNED_SESSION = {
  session_id: "pinned-delta-1",
  title: "Draft the launch note",
  workspace: "/Users/test/Delta/launch-note",
  agent: "delta",
  model: "anthropic:claude-opus-4-8",
  mode: "interactive",
  updated_at: "2026-07-01 09:00:00",
  messages: 2,
  pinned: true,
  archived: false,
  attention: 0,
};

const EXTRA_SESSIONS = Array.from({ length: 7 }, (_, i) => ({
  session_id: `wp-${i + 1}`,
  title: `Weekly plan ${i + 1}`,
  workspace: "",
  agent: "delta",
  model: "anthropic:claude-opus-4-8",
  mode: "interactive",
  updated_at: `2026-06-2${8 - Math.min(i, 7)} 10:00:00`,
  messages: 3,
  pinned: false,
  archived: false,
  attention: i + 1 === 3 ? 1 : 0,
}));

const EXTRA_SESSION_2 = {
  session_id: "infra-1",
  title: "infra triage",
  workspace: "/Users/test/Delta/infra-triage",
  agent: "delta",
  model: "anthropic:claude-opus-4-8",
  mode: "interactive",
  updated_at: "2026-06-15 10:00:00",
  messages: 4,
  pinned: false,
  archived: false,
  attention: 0,
};

const SLACK_SESSION = {
  session_id: "slack-thread-1",
  title: "#general — check the deploy?",
  workspace: "",
  agent: "delta",
  model: "anthropic:claude-opus-4-8",
  mode: "interactive",
  updated_at: "2026-06-10 10:00:00",
  messages: 2,
  pinned: false,
  archived: false,
  attention: 0,
  liveness: "idle",
  subscriptions: [],
  origin: "slack",
  origin_label: "#general · T0AB",
};

const INBOX_ITEMS = [
  {
    id: "inb-approval-1",
    session_id: "wp-3",
    kind: "approval",
    title: "Approve: run_shell",
    body: "rm -rf build/",
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "2026-07-01 08:00:00",
    resolved_at: null,
    session_title: "Weekly plan 3",
    session_agent: "delta",
    session_workspace: "",
    session_exists: true,
  },
  {
    id: "inb-question-1",
    session_id: "infra-1",
    kind: "question",
    title: "Which environment should I restart?",
    body: "",
    options: ["staging", "production"],
    allow_text: true,
    multi: false,
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "2026-07-01 08:05:00",
    resolved_at: null,
    session_title: "Investigate alerts",
    session_agent: "delta",
    session_workspace: "",
    session_exists: true,
  },
];

const PRIMARY_ROOT = { path: "/Users/test/Delta/launch-note", writable: true, label: "scratch", primary: true, exists: true };

const PROVIDERS = [
  { name: "openai", title: "OpenAI", needs_key: true, fields: [{ key: "api_key", label: "OpenAI API key", secret: true, required: true, help: "", placeholder: "sk-…" }], configured: true, values: {}, suggested_models: ["gpt-5.5"], key_set_at: "2026-06-12", last_used_at: Math.floor(Date.now() / 1000) - 7200 },
  { name: "anthropic", title: "Claude (Anthropic)", needs_key: true, fields: [{ key: "api_key", label: "API key", secret: true, required: true, help: "", placeholder: "sk-…" }], configured: true, values: {}, suggested_models: ["claude-opus-4-8"], key_set_at: null, last_used_at: null },
  { name: "zai", title: "Z AI (GLM)", needs_key: true, blurb: "Uses Z AI's OpenAI-compatible API.", fields: [{ key: "api_key", label: "Z AI API key", secret: true, required: true, help: "", placeholder: "" }, { key: "base_url", label: "Endpoint", secret: false, required: false, help: "Prefilled.", placeholder: "https://api.z.ai/api/paas/v4", default: "https://api.z.ai/api/paas/v4" }], configured: false, values: {}, suggested_models: ["glm-5.2"], key_set_at: null, last_used_at: null },
];

const CONNECTORS = [
  { name: "browser", title: "Browser", icon: "B", blurb: "Headless browser.", auth: "none", two_way: false, channels: false, available: true, brand_color: "#6b7280", logo: "", fields: [], instructions: [], connected: true, account: null, enabled: true, allowed_users: [], tools: [], managed: false, managed_profile: false },
  { name: "slack", title: "Slack", icon: "#", blurb: "Two-way Slack messaging.", auth: "bot_token", two_way: true, channels: true, available: true, brand_color: "#611f69", logo: "slack", fields: [], instructions: [], connected: true, account: "deeplearning.ai", enabled: true, allowed_users: [], approval_owner_ids: [], tools: [], managed: true, managed_profile: false, mode: "", workspaces: [{ team_id: "T1DL", account: "deeplearning.ai", domain: "dlaiteam", allowed_users: ["U_ME"], allow_all: false, allowed_user_names: {}, approval_owner_ids: ["U_ME"], approval_owner_names: { U_ME: "Rohit Prasad" }, installer_user_id: "U_ME", installer_name: "Rohit Prasad" }], unauthorized: [{ id: "pk1", platform: "slack", chat_id: "C0AAA111", chat_name: "#delta-test", user_id: "U0NEW", user_name: "Maya", chat_type: "channel", text: "hey delta, can you summarize this thread?", ts: 1_780_000_000, team_id: "T1DL" }] },
  { name: "github", title: "GitHub", icon: "⌘", blurb: "Work with issues, pull requests, repository files, and CI status.", auth: "token", two_way: true, channels: false, available: true, brand_color: "#1f2328", logo: "github", fields: [{ key: "token", label: "Personal access token", secret: true, required: true, help: "", placeholder: "" }], instructions: [], connected: true, account: "acme", enabled: true, allowed_users: [], tools: [], managed: true, managed_profile: false, mode: "", installations: [{ installation_id: "101", account_login: "acme", account_type: "Organization", repo_selection: "selected", github_login: "rohit-dev", allowed_users: ["rohit-dev"], allow_all: false }], unauthorized: [] },
  { name: "telegram", title: "Telegram", icon: "T", blurb: "Two-way Telegram messaging.", auth: "bot_token", two_way: true, channels: true, available: true, brand_color: "#229ed9", logo: "telegram", fields: [{ key: "bot_token", label: "Bot token", secret: true, required: true, help: "", placeholder: "123456:ABC…" }], instructions: [], connected: false, account: null, enabled: false, allowed_users: [], tools: [], managed: false, managed_profile: false },
  { name: "gmail", title: "Gmail", icon: "✉", blurb: "Search, summarize, draft, and send email.", about: "Search, summarize, and send over your Gmail.", access: ["Reads and searches your mail.", "Sends email as you.", "Never deletes mail or changes account settings."], auth: "oauth", two_way: false, channels: false, available: true, brand_color: "#ea4335", logo: "gmail", fields: [{ key: "access_token", label: "OAuth access token", secret: true, required: true, help: "", placeholder: "" }], instructions: [], connected: false, account: null, enabled: false, allowed_users: [], tools: [{ name: "gmail_search", label: "Search mail", kind: "read", description: "Search messages.", enabled: true, requires_approval: false }, { name: "gmail_send", label: "Send email", kind: "write", description: "Send a message.", enabled: true, requires_approval: true }], managed: true, managed_profile: false },
  { name: "google_calendar", title: "Google Calendar", icon: "◷", blurb: "Read availability, summarize schedules, and create events.", auth: "oauth", two_way: false, channels: false, available: true, brand_color: "#4285f4", logo: "google_calendar", fields: [{ key: "access_token", label: "OAuth access token", secret: true, required: true, help: "", placeholder: "" }], instructions: [], connected: false, account: null, enabled: false, allowed_users: [], tools: [], managed: true, managed_profile: false },
  { name: "hubspot", title: "HubSpot", icon: "⊚", blurb: "Search CRM records; log notes and tasks, update records. No deletes.", auth: "token", two_way: false, channels: false, available: true, brand_color: "#ff7a59", logo: "hubspot", fields: [{ key: "token", label: "Private app token", secret: true, required: true, help: "", placeholder: "pat-…" }], instructions: [], connected: false, account: null, enabled: false, allowed_users: [], tools: [], managed: true, managed_profile: false },
  { name: "notion", title: "Notion", icon: "◰", blurb: "Search pages, read content, query databases, create pages.", auth: "oauth", two_way: false, channels: false, available: true, brand_color: "#1f2328", logo: "", fields: [{ key: "access_token", label: "Integration secret", secret: true, required: true, help: "", placeholder: "ntn_…" }], instructions: [], connected: false, account: null, enabled: false, allowed_users: [], tools: [], managed: true, managed_profile: false },
  { name: "outlook", title: "Outlook", icon: "◎", blurb: "Microsoft 365 mail and calendar.", aliases: ["calendar", "email", "mail", "microsoft", "office"], auth: "oauth", two_way: false, channels: false, available: true, brand_color: "#0078d4", logo: "outlook", fields: [{ key: "access_token", label: "OAuth access token", secret: true, required: true, help: "", placeholder: "" }], instructions: [], connected: false, account: null, enabled: false, allowed_users: [], tools: [], managed: true, managed_profile: false },
  { name: "attio", title: "Attio", icon: "▣", blurb: "Search and read Attio CRM records; log notes.", auth: "oauth", two_way: false, channels: false, available: true, brand_color: "#2d6ae0", logo: "attio", fields: [{ key: "access_token", label: "OAuth access token", secret: true, required: true, help: "", placeholder: "" }], instructions: [], connected: false, account: null, enabled: false, allowed_users: [], tools: [], managed: true, managed_profile: false },
  { name: "monday", title: "monday.com", icon: "▦", blurb: "Read boards and items, track work, create items and post updates.", aliases: ["project management", "tasks", "boards"], auth: "oauth", two_way: false, channels: false, available: true, brand_color: "#6161ff", logo: "monday", mcp: true, fields: [], instructions: [], connected: false, account: null, enabled: false, allowed_users: [], tools: [{ name: "mcp__monday__get_board_info", label: "Read board", kind: "read", description: "Read a board's columns and groups.", enabled: true, requires_approval: false }, { name: "mcp__monday__create_item", label: "Create item", kind: "write", description: "Create an item on a board.", enabled: true, requires_approval: true }], managed: false, managed_profile: false },
  { name: "jira", title: "Jira", icon: "◆", blurb: "Search, summarize, create, and update issues.", aliases: ["issues", "tickets", "atlassian"], auth: "api_token", two_way: false, channels: false, available: true, brand_color: "#0052cc", logo: "jira", mcp: true, fields: [{ key: "base_url", label: "Atlassian site URL", secret: false, required: true, help: "", placeholder: "" }, { key: "email", label: "Account email", secret: false, required: true, help: "", placeholder: "" }, { key: "api_token", label: "API token", secret: true, required: true, help: "", placeholder: "" }], instructions: [], connected: false, account: null, enabled: false, allowed_users: [], tools: [], managed: false, managed_profile: false },
];

const AUTOMATION = {
  id: "task-1",
  title: "Daily AI News",
  instructions: "Fetch the latest AI news and produce an HTML+Tailwind presentation.",
  schedule: "Every day at ~5:40 PM",
  schedule_raw: { kind: "cron", cron: "40 17 * * *", fire_at: null, timezone: "local" },
  workspace: "",
  agent: "delta",
  enabled: true,
  next_run: Math.floor(Date.now() / 1000) + 3600,
  last_run: Math.floor(Date.now() / 1000) - 60,
  last_status: "running",
  run_count: 1,
  notify_on_completion: false,
  always_allowed: [
    { entry: "send_message slack:T1/C1", tool: "send_message", target: "slack:T1/C1" },
  ],
  unseen_runs: 2,
  unseen_failed: true,
  seen_runs_at: 0,
};
const AUTOMATION_CLEAN = { ...AUTOMATION, id: "task-2", title: "Weekly CRM digest", schedule: "Every Monday at ~9:00 AM", last_status: "ok", unseen_runs: 0, unseen_failed: false, always_allowed: [] };
const AUTOMATION_RUNS = [
  { run_id: "r1", task_id: "task-1", session_id: "__run__r1", started_at: Math.floor(Date.now() / 1000) - 60, finished_at: null, status: "running", result_text: null, artifacts: [], error: null, trigger: "schedule" },
];

/** Build the full per-test seed passed into the page's mock transport. */
function buildSeed(): Record<string, any> {
  return {
    settings: { ...SETTINGS },
    health: { ...HEALTH },
    sessions: [
      { ...PINNED_SESSION },
      ...EXTRA_SESSIONS.map((s) => ({ ...s })),
      { ...EXTRA_SESSION_2 },
      { ...SLACK_SESSION },
    ],
    inbox: INBOX_ITEMS.map((i) => ({ ...i })),
    roots: [{ ...PRIMARY_ROOT }],
    providers: PROVIDERS.map((p) => ({ ...p })),
    automations: [{ ...AUTOMATION }, { ...AUTOMATION_CLEAN }],
    automationRuns: AUTOMATION_RUNS.map((r) => ({ ...r })),
    sessionMessages: {} as Record<string, any[]>,
    runtimeSequences: {} as Record<string, number>,
    pendingTools: {} as Record<string, string>,
    epicPartials: {} as Record<string, string>,
    epicTimers: {} as Record<string, number>,
    inboxRouting: [{ name: "default", channel: null, target: "" }],
    unattended: {} as Record<string, boolean>,
    skills: [
      { name: "weekly-report", description: "Monday status report", instructions: "1. Collect updates\n2. Write it up", scope: "global", source: "local", enabled: true, path: "/state/skills/weekly-report", files: 0 },
      { name: "html-to-markdown", description: "Convert an HTML document or fragment to clean markdown.", instructions: "Convert the given HTML to markdown, preserving structure.", scope: "global", source: "uploaded", enabled: true, path: "/state/skills/html-to-markdown", files: 2 },
    ],
    stagedSkill: null as any,
    connections: {
      connected: [
        { connector: "browser", enabled: true, detail: "Browser" },
        { connector: "slack", enabled: true, detail: "Slack" },
        { connector: "github", enabled: true, detail: "GitHub" },
      ],
      recommended: [{ connector: "gmail", reason: "email context for morning summaries", tier: "core", connected: false }],
      attention: 1,
    },
    connectors: CONNECTORS.map((c) => structuredClone(c)),
    subscriptions: [
      { session_id: "wp-1", session_title: "Weekly plan 1", agent: "delta", channel: "slack:T1DL/C0AAA111", channel_name: "delta-test", routing_target: null, collision: false },
    ],
    mcpServers: [] as any[],
  };
}

/**
 * Command handlers run IN-PAGE. Each is a source string; the transport creates it as a function
 * `(args) => …` with `$state` (the seed above, mutable) and `emit(eventName, payload)` in scope.
 * Shapes mirror the removed /v1 endpoints so the frontend reads them unchanged.
 */
function commandSources(): Record<string, string> {
  return {
    health: `(args) => ($state.health)`,
    settings_get: `(args) => { const s = $state.settings; return { ...s }; }`,
    settings_set_context_bar: `(args) => { $state.settings.context_bar = !!args.shown; return { ok: true, context_bar: $state.settings.context_bar }; }`,
    settings_set_pdf: `(args) => { const p = args.patch || {}; Object.assign($state.settings, p); return { ok: true }; }`,
    settings_set_compaction: `(args) => { const p = args.patch || {}; Object.assign($state.settings, p); return { ok: true }; }`,
    settings_set_language: `(args) => { $state.settings.language = args.language; return { ok: true, language: args.language }; }`,
    settings_set_onboarded: `(args) => { $state.settings.onboarded = !!args.value; return { ok: true, onboarded: $state.settings.onboarded }; }`,
    settings_set_sessions_peek: `(args) => { $state.settings.sessions_peek = args.count; return { ok: true }; }`,
    settings_set_scratch_base: `(args) => { $state.settings.scratch_base = args.path; return { ok: true }; }`,
    settings_set_nav_layout: `(args) => { $state.settings.nav_layout = args.layout; return { ok: true }; }`,
    settings_set_model_key: `(args) => { $state.settings.has_key = true; $state.settings.model_ready = true; return { ok: true }; }`,
    settings_set_default_model: `(args) => { $state.settings.model = args.modelId; return { ok: true, model: args.modelId }; }`,
    settings_add_model: `(args) => { if (!$state.settings.models.includes(args.modelId)) $state.settings.models.push(args.modelId); return { ok: true, models: $state.settings.models }; }`,
    settings_remove_model: `(args) => { $state.settings.models = $state.settings.models.filter((m) => m !== args.modelId); return { ok: true, models: $state.settings.models }; }`,
    settings_set_surfaces: `(args) => ({ ok: true, surfaces: { delta: true } })`,
    providers_list: `(args) => $state.providers`,
    provider_protocols: `(args) => ([
      { id: "openai", title: "OpenAI", needs_key: false, fields: [{ key: "api_key", label: "API key", secret: true, required: false, help: "", placeholder: "sk-…", default: null }, { key: "base_url", label: "Endpoint", secret: false, required: true, help: "", placeholder: "https://…/v1", default: null }], recommended_model: "gpt-5.6-sol" },
      { id: "anthropic", title: "Anthropic", needs_key: true, fields: [{ key: "api_key", label: "Anthropic API key", secret: true, required: true, help: "", placeholder: "sk-ant-…", default: null }, { key: "base_url", label: "Endpoint", secret: false, required: true, help: "", placeholder: "https://…", default: null }], recommended_model: "claude-fable-5" },
    ])`,
    provider_set: `(args) => { let p = $state.providers.find((x) => x.name === args.name); if (!p) { p = { name: args.name, title: args.name, needs_key: true, fields: [], configured: false, values: {}, suggested_models: [], key_set_at: null, last_used_at: null, custom: true, protocol: args.protocol, alias: args.name }; $state.providers.push(p); } if (args.fields && args.fields.api_key) { p.configured = true; p.key_set_at = "2026-07-05"; } if (p.needs_key === false) p.configured = true; for (const [k, v] of Object.entries(args.fields || {})) { if (k === "api_key") continue; if (v) p.values = { ...p.values, [k]: v }; else if (p.values) delete p.values[k]; } return { ok: true, provider: args.name, recommended_model: null }; }`,
    provider_remove: `(args) => { const i = $state.providers.findIndex((x) => x.name === args.name); if (i === -1) return { ok: false, error: "unknown provider" }; const p = $state.providers[i]; if (!p.custom) { p.configured = !p.needs_key; p.key_set_at = null; } else { $state.providers.splice(i, 1); } return { ok: true, provider: args.name }; }`,
    provider_verify: `(args) => { const key = String((args.fields && args.fields.api_key) || ""); return /bad/i.test(key) ? { ok: false, error: "Invalid API key." } : { ok: true }; }`,
    provider_fetch_models: `(args) => { const key = String((args.fields && args.fields.api_key) || ""); if (/bad/i.test(key)) return { ok: false, error: "Invalid API key." }; const ids = ["custom-7b", "custom-coder"]; const added = ids.filter((id) => !$state.settings.models.includes(args.name + ":" + id)); added.forEach((id) => $state.settings.models.push(args.name + ":" + id)); return { ok: true, alias: args.name, models: ids, added }; }`,

    sessions_list: `(args) => ({ sessions: $state.sessions })`,
    session_messages: `(args) => ({ messages: $state.sessionMessages[args.sessionId] || [] })`,
    session_rename: `(args) => { const s = $state.sessions.find((x) => x.session_id === args.sessionId); if (s) s.title = args.title; return { ok: true }; }`,
    session_set_flags: `(args) => { const s = $state.sessions.find((x) => x.session_id === args.sessionId); if (s) { if (typeof args.pinned === "boolean") s.pinned = args.pinned; if (typeof args.archived === "boolean") s.archived = args.archived; } return { ok: true }; }`,
    session_delete: `(args) => { $state.sessions = $state.sessions.filter((x) => x.session_id !== args.sessionId); delete $state.sessionMessages[args.sessionId]; return { ok: true }; }`,
    session_set_reasoning: `(args) => { const s = $state.sessions.find((x) => x.session_id === args.sessionId); if (s) s.reasoning_effort = args.effort; return { ok: true, reasoning_effort: args.effort }; }`,
    session_revert: `(args) => ({ ok: true })`,
    workspaces_recent: `(args) => ({ workspaces: [] })`,
    workspaces_trusted: `(args) => ({ workspaces: [] })`,
    workspace_set_trusted: `(args) => ({ ok: true, path: args.path, trusted: !!args.trusted })`,
    workspace_open: `(args) => ({ ok: true, path: args.path, git_branch: "main" })`,
    session_roots: `(args) => ({ roots: $state.roots })`,
    session_add_root: `(args) => { const existing = $state.roots.find((r) => r.path === args.path); if (existing) existing.writable = !!args.writable; else $state.roots.push({ path: args.path, writable: !!args.writable, label: (args.path.split("/").filter(Boolean).pop() || args.path), primary: false, exists: true }); return { ok: true, roots: $state.roots }; }`,
    session_remove_root: `(args) => { $state.roots = $state.roots.filter((r) => r.path !== args.path || r.primary); return { ok: true, roots: $state.roots }; }`,
    session_get_unattended: `(args) => ({ ok: true, unattended: !!$state.unattended[args.sessionId] })`,
    session_set_unattended: `(args) => { $state.unattended[args.sessionId] = !!args.unattended; return { ok: true, unattended: !!args.unattended }; }`,
    session_connections: `(args) => $state.connections`,
    session_set_connection: `(args) => { const row = $state.connections.connected.find((c) => c.connector === args.connector); if (row) row.enabled = !!args.enabled; return { ok: true }; }`,

    inbox_list: `(args) => ({ items: $state.inbox.filter((i) => (!args.sessionId || i.session_id === args.sessionId) && (!args.itemState || i.state === args.itemState)) })`,
    inbox_resolve: `(args) => { const it = $state.inbox.find((x) => x.id === args.id); if (it) { it.state = "resolved"; it.resolution = args.resolution; } return { ok: true }; }`,
    inbox_routing_list: `(args) => ({ bindings: $state.inboxRouting })`,
    inbox_routing_set: `(args) => { const row = $state.inboxRouting.find((x) => x.name === args.name); if (row) { row.channel = args.channel; row.target = args.target; } return { ok: true }; }`,

    artifacts_list: `(args) => ({ artifacts: [] })`,
    artifact_read: `(args) => ({ ok: false, error: "no artifacts in mock" })`,
    artifact_resolve_path: `(args) => ({ ok: false, error: "not found" })`,

    memory_list: `(args) => ({ memory: [] })`,
    memory_update: `(args) => ({ ok: true })`,
    memory_delete: `(args) => ({ ok: true })`,
    memory_delete_all: `(args) => ({ ok: true })`,
    memory_settings: `(args) => ({ enabled: true, user_rules: "" })`,
    memory_set_settings: `(args) => ({ ok: true })`,

    automations_list: `(args) => ({ tasks: $state.automations })`,
    automation_get: `(args) => { const t = $state.automations.find((x) => x.id === args.id) || $state.automations[0]; return { task: t, runs: $state.automationRuns.filter((r) => r.task_id === t.id) }; }`,
    automation_create: `(args) => { const b = args.payload || {}; if (!b.title || !b.instructions || !(b.cron || b.fire_at)) return { ok: false, error: "missing fields" }; const grants = (b.permissions || []).filter((g) => g && g.access === "write" && g.tool && g.target).map((g) => ({ entry: g.tool + " " + g.target, tool: g.tool, target: g.target })); const t = { ...$state.automations[0], id: "task-ob-" + $state.automations.length, title: b.title, instructions: b.instructions, schedule: b.cron || b.fire_at, always_allowed: grants, run_count: 0 }; $state.automations.push(t); return { ok: true, task: t }; }`,
    automation_update: `(args) => { const t = $state.automations.find((x) => x.id === args.id); if (t) { if (args.changes && args.changes.revoke) { t.always_allowed = (t.always_allowed || []).filter((r) => r.entry !== args.changes.revoke); return { ok: true, task: t }; } Object.assign(t, args.changes || {}); } return { ok: true, task: t }; }`,
    automation_delete: `(args) => { $state.automations = $state.automations.filter((x) => x.id !== args.id); return { ok: true }; }`,
    automation_mark_seen: `(args) => { const t = $state.automations.find((x) => x.id === args.id); if (t) { t.unseen_runs = 0; t.unseen_failed = false; } return { ok: true }; }`,
    automation_prepare_run: `(args) => { const id = args.id; const runId = "r" + ($state.automationRuns.length + 1); const task = $state.automations.find((t) => t.id === id); $state.automationRuns.unshift({ run_id: runId, task_id: id, session_id: "__run__" + runId, started_at: Math.floor(Date.now() / 1000), finished_at: null, status: "running", result_text: null, artifacts: [], error: null, trigger: "manual" }); return { ok: true, run_id: runId, session_id: "__run__" + runId, workspace: task ? task.workspace : "", agent: task ? task.agent : "delta", prompt: task ? task.instructions : "" }; }`,
    automation_finalize_run: `(args) => ({ ok: true })`,
    scheduler_due: `(args) => ({ tasks: [] })`,

    mcp_list: `(args) => { $state.mcpServers.forEach((s) => { if (s.status === "authorizing") { s._mockPolls = (s._mockPolls || 0) + 1; if (s._mockPolls > 1) { s.status = "connected"; s.tool_count = 6; } } }); return { servers: structuredClone($state.mcpServers) }; }`,
    mcp_put: `(args) => { const exists = $state.mcpServers.find((x) => x.name === args.name); const s = { name: args.name, enabled: true, transport: (args.config && args.config.url) ? "http" : "stdio", requires_approval: true, auth: (args.config && args.config.auth === "oauth") ? "oauth" : null, status: (args.config && args.config.auth === "oauth") ? "needs_auth" : "configured", last_error: null, tool_count: null, config: args.config || {} }; if (exists) Object.assign(exists, s); else $state.mcpServers.push(s); return { ok: true, name: args.name }; }`,
    mcp_patch: `(args) => ({ ok: true })`,
    mcp_delete: `(args) => { $state.mcpServers = $state.mcpServers.filter((x) => x.name !== args.name); return { ok: true }; }`,
    mcp_tools: `(args) => ({ ok: true, tools: [] })`,
    mcp_reload: `(args) => ({ ok: true })`,
    mcp_connect: `(args) => { const c = $state.connectors.find((x) => x.name === args.name && x.mcp); if (c) { c.connected = true; c.enabled = true; return { ok: true, started: true }; } const s = $state.mcpServers.find((x) => x.name === args.name); if (s) { s.status = "authorizing"; s._mockPolls = 0; } return { ok: true, started: true }; }`,
    mcp_signout: `(args) => { const s = $state.mcpServers.find((x) => x.name === args.name); if (s) { s.status = "needs_auth"; s.tool_count = null; } return { ok: true }; }`,

    audit_list: `(args) => ({ events: [] })`,
    sources_list: `(args) => ({ sources: [] })`,
    validations_list: `(args) => ({ validations: [] })`,

    skills_list: `(args) => ({ skills: $state.skills })`,
    skill_create: `(args) => { const b = args.body || {}; if (!b.name || !(b.instructions || "").trim()) return { ok: false, error: "Skill name and instructions are required." }; if ($state.skills.some((s) => s.name === b.name)) return { ok: false, error: "duplicate" }; $state.skills.push({ name: b.name, description: b.description || "", instructions: b.instructions, scope: "global", source: "local", enabled: true, path: "/state/skills/" + b.name, files: 0 }); return { ok: true }; }`,
    skill_update: `(args) => { const s = $state.skills.find((x) => x.name === args.name); if (!s) return { ok: false, error: "unknown" }; const b = args.patch || {}; if (typeof b.enabled === "boolean") s.enabled = b.enabled; if (typeof b.description === "string") s.description = b.description; if (typeof b.instructions === "string") s.instructions = b.instructions; return { ok: true }; }`,
    skill_delete: `(args) => { $state.skills = $state.skills.filter((x) => x.name !== args.name); return { ok: true }; }`,
    skill_move: `(args) => ({ ok: true })`,
    skill_resolve_folder: `(args) => ({ ok: false, error: "unknown" })`,
    skill_stage_upload: `(args) => { $state.stagedSkill = { token: "stage-1", name: "greet", description: "says hello", instructions: "Say hello warmly.", files: ["notes.txt"] }; return { ok: true, ...$state.stagedSkill }; }`,
    skill_confirm_upload: `(args) => { const st = $state.stagedSkill; if (!st || args.token !== st.token) return { ok: false, error: "expired" }; $state.skills.push({ name: st.name, description: st.description, instructions: st.instructions, scope: "global", source: "uploaded", enabled: true, path: "/state/skills/" + st.name, files: st.files.length }); $state.stagedSkill = null; return { ok: true }; }`,
    session_skills: `(args) => ({ skills: $state.skills.filter((s) => s.enabled).map((s) => ({ name: s.name, description: s.description, scope: s.scope, enabled: true })) })`,
    session_set_skill: `(args) => ({ ok: true })`,
  };
}

/**
 * The fake agent — the scripted RuntimeHost replacing the removed WebSocket session. It lives
 * IN-PAGE: the `runtime_run` handler runs it. Envelopes are emitted on "delta-runtime-event"
 * with the strict v1 protocol so the full send→stream→render loop and the approval round-trip
 * run through production code paths.
 */
function fakeAgentSources(): Record<string, string> {
  return {
    runtime_run: `(args) => {
      const sessionId = args.sessionId;
      const text = String(args.userInput || "");
      const messages = ($state.sessionMessages[sessionId] = $state.sessionMessages[sessionId] || []);
      const send = (type, payload) => {
        const body = payload || {};
        const ts = Date.now() / 1000;
        if (type === "assistant_message") messages.push({ role: "assistant", content: String(body.text || ""), ...(body.reasoning ? { reasoning: body.reasoning } : {}), ...(body.usage ? { usage: body.usage } : {}), ts });
        else if (type === "error") messages.push({ role: "notice", kind: "error", text: String(body.error || "unknown"), ts });
        else if (type === "interrupted") messages.push({ role: "notice", kind: "interrupted", ts });
        else if (type === "compacted") messages.push({ role: "notice", kind: "compacted", text: String(body.text || ""), ts });
        else if (type === "model_changed") messages.push({ role: "notice", kind: "model_switch", text: String(body.text || ""), ts });
        const sequence = ($state.runtimeSequences[sessionId] = ($state.runtimeSequences[sessionId] || 1) + 1);
        emit("delta-runtime-event", { type, version: 1, sessionId, sequence, payload: body });
      };
      const run = () => {
        messages.push({ role: "user", content: text, ...(args.skill ? { _display: "/" + args.skill + (text ? " " + text : "") } : {}), ts: Date.now() / 1000 });
        send("turn_start", { input: text, ...(args.skill ? { display: "/" + args.skill + (text ? " " + text : "") } : {}) });
        if (/run a tool/i.test(text)) {
          $state.pendingTools[sessionId] = "run_shell";
          send("tool_proposed", { name: "run_shell", arguments: { command: "ls" } });
          send("permission_required", { name: "run_shell", arguments: { command: "ls" }, reason: "The delta wants to run a command." });
          return;
        }
        if (/write a file/i.test(text)) {
          $state.pendingTools[sessionId] = "write_file";
          const toolArgs = { path: "src/fetch_data.py", content: 'import json\\nimport urllib.request\\n\\ncompanies = ["NVDA", "AMD"]\\nprint(len(companies))\\ndone = True' };
          send("tool_proposed", { name: "write_file", arguments: toolArgs });
          send("permission_required", { name: "write_file", arguments: toolArgs, reason: "" });
          return;
        }
        if (/post the long digest/i.test(text)) {
          $state.pendingTools[sessionId] = "send_message";
          const toolArgs = { target: "slack:T1/C1", text: "aisuite — last 24 hours of work (through Jul 15): 5 PRs merged covering chat-completion streaming with unified chunks across providers, multimodal input conversion, Slack collaboration improvements, human attribution for outbound posts, and repo-wide formatting. ".repeat(6) };
          send("tool_proposed", { name: "send_message", arguments: toolArgs });
          send("permission_required", { name: "send_message", arguments: toolArgs, reason: "", category: "messaging" });
          return;
        }
        if (/post the digest/i.test(text)) {
          $state.pendingTools[sessionId] = "send_message";
          const toolArgs = { target: "slack:T1/C1", text: "Weekly digest ready" };
          send("tool_proposed", { name: "send_message", arguments: toolArgs });
          send("permission_required", { name: "send_message", arguments: toolArgs, reason: "", category: "messaging", standing_target: "slack:T1/C1" });
          return;
        }
        if (/create an automation/i.test(text)) {
          $state.pendingTools[sessionId] = "create_scheduled_task";
          const toolArgs = { title: "Weekly digest", instructions: "Summarize the week and post it.", cron: "0 9 * * 1", permissions: [{ tool: "send_message", target: "slack:T1/C1", access: "write" }, { tool: "github_list_commits", target: "rohit/agent-platform", access: "read" }] };
          send("tool_proposed", { name: "create_scheduled_task", arguments: {} });
          send("permission_required", { name: "create_scheduled_task", arguments: toolArgs, reason: "", category: "automation" });
          return;
        }
        if (/think hard/i.test(text)) {
          const thoughts = ["Weighing options. ", "Comparing tradeoffs. ", "Settling it. "];
          let tick = 0;
          const timer = setInterval(() => {
            if (tick < thoughts.length) { send("reasoning_delta", { text: thoughts[tick++] }); return; }
            clearInterval(timer);
            send("assistant_delta", { text: "Decision made." });
            send("assistant_message", { text: "Decision made.", reasoning: thoughts.join("") });
            send("turn_done");
          }, 120);
          return;
        }
        if (/compact the context/i.test(text)) {
          send("compacting", {});
          setTimeout(() => { send("compacted", { text: "Context compacted — earlier turns were summarized" }); send("assistant_message", { text: "Still on it — continuing where I left off." }); send("turn_done"); }, 400);
          return;
        }
        if (/fail the turn/i.test(text)) { send("error", { error: "model unreachable" }); send("turn_done"); return; }
        if (/stream the epic/i.test(text)) {
          let ticks = 0;
          const line = "The epic scrolls ever onward, line upon line upon line. ";
          $state.epicPartials[sessionId] = "";
          $state.epicTimers[sessionId] = setInterval(() => {
            ticks += 1;
            const delta = line.repeat(3) + "\\n\\n";
            $state.epicPartials[sessionId] += delta;
            send("assistant_delta", { text: delta });
            if (ticks >= 40) {
              clearInterval($state.epicTimers[sessionId]);
              delete $state.epicTimers[sessionId];
              delete $state.epicPartials[sessionId];
              send("assistant_message", { text: ("The epic concludes. " + line).repeat(20) });
              send("turn_done");
            }
          }, 120);
          return;
        }
        send("assistant_delta", { text: "Echo: " });
        send("assistant_delta", { text: text });
        send("assistant_message", { text: "Echo: " + text + (args.skill ? " [skill=" + args.skill + "]" : "") + " [model=" + (args.modelId || "none") + "]", usage: { model: args.modelId || "anthropic:claude-opus-4-8", input: 1000, output: 200, cache_read: 8000, cache_write: 800 } });
        send("turn_done");
      };
      // Defer so the turn_start lands after the run is accepted.
      setTimeout(run, 0);
      const runId = "run-" + Date.now();
      $state.sessionMessages[sessionId] = messages;
      return { ok: true, accepted: true, runId, state: "Running" };
    }`,
    runtime_approval: `(args) => {
      const sessionId = args.sessionId;
      const messages = ($state.sessionMessages[sessionId] = $state.sessionMessages[sessionId] || []);
      const send = (type, payload) => { const body = payload || {}; if (type === "assistant_message") messages.push({ role: "assistant", content: String(body.text || ""), ts: Date.now() / 1000 }); const sequence = ($state.runtimeSequences[sessionId] = ($state.runtimeSequences[sessionId] || 1) + 1); emit("delta-runtime-event", { type, version: 1, sessionId, sequence, payload: body }); };
      const pendingTool = $state.pendingTools[sessionId] || "run_shell";
      const denied = args.decision === "deny";
      if (pendingTool === "run_shell") {
        if (denied) { send("tool_finished", { name: pendingTool, status: "denied" }); send("assistant_message", { text: "Understood — skipped the command." }); }
        else { send("tool_finished", { name: pendingTool, status: "done", result_preview: "README.md" }); send("assistant_message", { text: "The command ran; 1 file found." }); }
      } else if (denied) { send("tool_finished", { name: pendingTool, status: "denied" }); send("assistant_message", { text: "Understood — skipped it." }); }
      else { send("tool_finished", { name: pendingTool, status: "done", result_preview: "ok" }); send("assistant_message", { text: "Done via " + pendingTool + " [decision=" + args.decision + "]" }); }
      delete $state.pendingTools[sessionId];
      send("turn_done");
      return { ok: true, toolCallId: args.toolCallId || null, decision: args.decision };
    }`,
    runtime_cancel: `(args) => {
      const sessionId = args.sessionId;
      const messages = ($state.sessionMessages[sessionId] = $state.sessionMessages[sessionId] || []);
      const send = (type, payload) => { const body = payload || {}; if (type === "interrupted") messages.push({ role: "notice", kind: "interrupted", ts: Date.now() / 1000 }); const sequence = ($state.runtimeSequences[sessionId] = ($state.runtimeSequences[sessionId] || 1) + 1); emit("delta-runtime-event", { type, version: 1, sessionId, sequence, payload: body }); };
      if ($state.epicTimers[sessionId]) { clearInterval($state.epicTimers[sessionId]); delete $state.epicTimers[sessionId]; }
      if ($state.epicPartials[sessionId]) { messages.push({ role: "assistant", content: $state.epicPartials[sessionId], ts: Date.now() / 1000 }); delete $state.epicPartials[sessionId]; }
      send("interrupted", {}); send("turn_done");
      return { ok: true, cancelled: true };
    }`,
    runtime_messages: `(args) => ({ messages: $state.sessionMessages[args.sessionId] || [] })`,
    runtime_steer: `(args) => ({ ok: true, accepted: true })`,
    runtime_retry: `(args) => { const sessionId = args.sessionId; const messages = ($state.sessionMessages[sessionId] = $state.sessionMessages[sessionId] || []); const send = (type, payload) => { const body = payload || {}; if (type === "assistant_message") messages.push({ role: "assistant", content: String(body.text || ""), ts: Date.now() / 1000 }); const sequence = ($state.runtimeSequences[sessionId] = ($state.runtimeSequences[sessionId] || 1) + 1); emit("delta-runtime-event", { type, version: 1, sessionId, sequence, payload: body }); }; send("turn_start", { input: "" }); send("assistant_message", { text: "Recovered after retry." }); send("turn_done"); return { ok: true, accepted: true, runId: "run-retry" }; }`,
    runtime_resume: `(args) => ({ ok: true, accepted: true, runId: "run-resume" })`,
    runtime_follow_up: `(args) => ({ ok: true, accepted: true, runId: "run-followup" })`,
    runtime_switch_model: `(args) => { const sessionId = args.sessionId; const messages = ($state.sessionMessages[sessionId] = $state.sessionMessages[sessionId] || []); const text = "Model switched to " + args.modelId; messages.push({ role: "notice", kind: "model_switch", text, ts: Date.now() / 1000 }); const sequence = ($state.runtimeSequences[sessionId] = ($state.runtimeSequences[sessionId] || 1) + 1); emit("delta-runtime-event", { type: "model_changed", version: 1, sessionId, sequence, payload: { model: args.modelId, text } }); return { ok: true, notice: text }; }`,
    runtime_truncate: `(args) => ({ ok: true, len: 0 })`,

    // No-op shells so foreground callers never throw when a feature isn't part of a given test.
    pick_folder: `(args) => "/tmp/picked-folder"`,
    connector_connect: `(args) => { const c = $state.connectors.find((x) => x.name === args.name); if (c) { c.connected = true; c.enabled = true; } return { ok: true }; }`,
    connector_disconnect: `(args) => { const c = $state.connectors.find((x) => x.name === args.name); if (c) { c.connected = false; c.enabled = false; } return { ok: true }; }`,
    connector_update_tools: `(args) => ({ ok: true })`,
    connectors_list: `(args) => ({ connectors: $state.connectors })`,
    connector_action: `(args) => { const c = $state.connectors.find((x) => x.name === args.name); const p = args.payload || {}; if (!c) return { ok: false, error: "unknown connector" }; if (args.action === "mcp_connect") { c.connected = true; c.enabled = true; return { ok: true, started: true }; } if (args.action === "resolve_unauthorized") { const i = (c.unauthorized || []).findIndex((x) => x.id === p.item_id); if (i < 0) return { ok: false, error: "unknown item" }; const item = c.unauthorized.splice(i, 1)[0]; if (p.action === "allow" || p.action === "allow_deliver") { const ws = (c.workspaces || []).find((x) => x.team_id === item.team_id); const pool = ws ? ws.allowed_users : c.allowed_users; if (pool && !pool.includes(item.user_id)) pool.push(item.user_id); } return { ok: true }; } if (args.action === "allow_user") { const ws = (c.workspaces || []).find((x) => x.team_id === p.team_id); const pool = ws ? ws.allowed_users : c.allowed_users; if (pool && !pool.includes(p.user_id)) pool.push(p.user_id); if (ws && p.name) ws.allowed_user_names[p.user_id] = p.name; return { ok: true }; } if (args.action === "directory") { const q = String(p.q || "").toLowerCase(); const ws = (c.workspaces || []).find((x) => x.team_id === p.team_id); const allowed = ws ? ws.allowed_users : c.allowed_users; const members = [{ id: "U9MAYA", name: "Maya Chen", handle: "maya", guest: false }, { id: "U8ROHIT", name: "Rohit Prasad", handle: "rohit", guest: false }, { id: "U7CAL", name: "Contractor Cal", handle: "cal", guest: true }].filter((m) => !allowed.includes(m.id) && (!q || m.name.toLowerCase().includes(q) || m.handle.includes(q))); return { ok: true, members }; } if (args.action === "channels") { const q = String(p.q || "").toLowerCase(); const channels = [{ id: "C9LAUNCH", name: "launch-team", is_private: false, is_member: true }, { id: "C8LEADS", name: "leads", is_private: true, is_member: true }, { id: "C7LOBBY", name: "lobby", is_private: false, is_member: false }].filter((x) => !q || x.name.includes(q)); return { ok: true, channels }; } return { ok: true }; }`,
    subscription_add: `(args) => { const raw = String(args.channel || "").trim(); if (raw.startsWith("#")) return { ok: false, error: "Channel names can't be looked up — paste the channel ID or the channel's Copy-link URL." }; const link = raw.match(/slack\\.com\\/archives\\/([A-Za-z0-9]+)/); const channel = link ? "slack:" + link[1].toUpperCase() : (raw.includes(":") ? raw : "slack:" + raw); $state.subscriptions.push({ session_id: args.sessionId, session_title: "", agent: "delta", channel, channel_name: null, routing_target: null, collision: false }); return { ok: true, channel }; }`,
    subscription_remove: `(args) => { $state.subscriptions = $state.subscriptions.filter((s) => !(s.session_id === args.sessionId && s.channel === args.channel)); return { ok: true }; }`,
    subscriptions_list: `(args) => ({ subscriptions: $state.subscriptions })`,
    unrouted_list: `(args) => ([])`,
    recent_channels: `(args) => ({ channels: [{ channel: "slack:C0AAA111", name: "delta-test", last_from: "alice", last_text: "ship it" }, { channel: "slack:C0BBB222", name: null, last_from: "bob", last_text: "deploy failed" }] })`,
  };
}

/** Install the mock transport on a page (must run before navigation). */
export async function mockApi(page: import("@playwright/test").Page): Promise<void> {
  const seed = buildSeed();
  const sources = { ...commandSources(), ...fakeAgentSources() };
  const initScript = installMockRuntimeInitScript(seed, sources);
  await page.addInitScript(initScript);
}

/** Patch mock authority state before or after navigation. Pre-navigation patches are consumed
 * regardless of init-script ordering; post-navigation patches update the live authority. */
export async function patchMockState(page: Page, patch: Record<string, unknown>): Promise<void> {
  await page.addInitScript((value) => {
    const g = window as any;
    const merge = (target: Record<string, any>, source: Record<string, any>) => {
      for (const [key, next] of Object.entries(source)) {
        if (next && typeof next === "object" && !Array.isArray(next) && target[key] && typeof target[key] === "object" && !Array.isArray(target[key])) Object.assign(target[key], next);
        else target[key] = next;
      }
    };
    g.__DELTA_MOCK_STATE_PATCH__ ||= {};
    merge(g.__DELTA_MOCK_STATE_PATCH__, value);
    if (g.__DELTA_MOCK__) merge(g.__DELTA_MOCK__.state, value);
  }, patch);
}

/** Replace one native command for a test (for example, to hold boot or model loading). */
export async function overrideMockCommand(page: Page, name: string, source: string): Promise<void> {
  await page.addInitScript(({ commandName, commandSource }) => {
    const g = window as any;
    g.__DELTA_MOCK_COMMAND_OVERRIDES__ = {
      ...(g.__DELTA_MOCK_COMMAND_OVERRIDES__ || {}),
      [commandName]: commandSource,
    };
    g.__DELTA_MOCK__?.setCommand(commandName, commandSource);
  }, { commandName: name, commandSource: source });
}

export async function readMockState<T = Record<string, unknown>>(page: Page): Promise<T> {
  return await page.evaluate(() => (window as any).__DELTA_MOCK__.state as T);
}

// A `test` whose page has the mock transport installed before navigation.
export const test = base.extend({
  page: async ({ page }, use) => {
    await mockApi(page);
    await use(page);
  },
});

export { expect };
