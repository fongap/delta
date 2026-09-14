import type { GroupedQuestion, QuestionOption, SessionInfo, WsEvent } from "./types";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  RuntimeContractError,
  type ArtifactDto,
  type MessageDto,
  type MessageSourceDto,
  type RuntimeEventEnvelopeV1,
} from "./runtime-contract";
import {
  directAddModel,
  directCancel,
  directApproval,
  directCreateAutomation,
  directDeleteAutomation,
  directFetchProviderModels,
  directFollowUp,
  directGetProtocols,
  directGetProviders,
  directGetSettings,
  directGetUnattended,
  directGetMemorySettings,
  directGetAutomation,
  directHealth,
  directListenSession,
  directListenApp,
  directListSessions,
  directListInbox,
  directListArtifacts,
  directListMemory,
  directListAutomations,
  directOpenWorkspace,
  directPickFolder,
  directRecentWorkspaces,
  directRemoveModel,
  directRemoveProvider,
  directDeleteMemory,
  directDeleteAllMemory,
  directResolveInbox,
  directReadArtifact,
  directResolveArtifactPath,
  directPrepareAutomationRun,
  directRetry,
  directRun,
  directSessionDelete,
  directSessionMessages,
  directSessionAddRoot,
  directSessionRename,
  directSessionRemoveRoot,
  directSessionRevert,
  directSessionRoots,
  directSessionSetFlags,
  directSessionSetReasoning,
  directSetCompactionSettings,
  directSetContextBar,
  directSetDefaultModel,
  directSetLanguage,
  directSetModelKey,
  directSetOnboarded,
  directSetPdfSettings,
  directSetProvider,
  directSetScratchBase,
  directSetSessionsPeek,
  directSetUnattended,
  directSetMemorySettings,
  directMarkAutomationSeen,
  directSteer,
  directSwitchModel,
  directSetWorkspaceTrusted,
  directTrustedWorkspaces,
  directUpdateMemory,
  directUpdateAutomation,
  directFinalizeAutomationRun,
  directListAudit,
  directListMcp,
  directPutMcp,
  directPatchMcp,
  directDeleteMcp,
  directMcpTools,
  directReloadMcp,
  directConnectMcp,
  directSignoutMcp,
  directListSkills,
  directCreateSkill,
  directUpdateSkill,
  directDeleteSkill,
  directMoveSkill,
  directResolveSkillFolder,
  directStageSkillUpload,
  directConfirmSkillUpload,
  directSessionSkills,
  directSetSessionSkill,
  directSetMode,
  directDirectoryResponse,
  directPlanResponse,
  directQuestionResponse,
  directListConnectors,
  directConnectConnector,
  directDisconnectConnector,
  directUpdateConnectorTools,
  directConnectorAction,
  directSessionConnections,
  directSetSessionConnection,
  directListSubscriptions,
  directAddSubscription,
  directRemoveSubscription,
  directListInboxRouting,
  directSetInboxRouting,
  directListUnrouted,
  directRecentChannels,
  directGetDmRoute,
  directSetDmRoute,
  directBrowserState,
  directBrowserScreenshot,
  directBrowserClose,
  directVerifyProvider,
} from "./runtimeTransport";

export interface Health {
  status: string;
  default_workspace: string | null;
  model: string;
  protocolVersion: number;
  capabilities: RuntimeCapability[];
}

export const UI_PROTOCOL_VERSION = 1;
export const RUNTIME_CAPABILITIES = [
  "events.app-wide",
  "provider.custom",
  "session.message-revert",
  "session.reasoning-effort",
] as const;
export type RuntimeCapability = (typeof RUNTIME_CAPABILITIES)[number];

const runtimeCapabilities = new Set<string>(RUNTIME_CAPABILITIES);
const reportedContractDiagnostics = new Set<string>();

function reportContractDiagnostic(key: string, message: string): void {
  if (reportedContractDiagnostics.has(key)) return;
  reportedContractDiagnostics.add(key);
  console.warn(`[runtime-contract] ${message}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

type ParsedRuntimeEvent = RuntimeEventEnvelopeV1<Record<string, unknown>>;

const EVENT_SEQUENCE_WINDOW = 256;

class RuntimeEventSequenceGate {
  private readonly states = new Map<string, { highest: number; seen: Set<number> }>();

  constructor(private readonly stream: string) {}

  accept(event: ParsedRuntimeEvent, resetOnReady = false): boolean {
    const key = event.sessionId ?? "<none>";
    let state = this.states.get(key);
    if (!state || (resetOnReady && event.type === "ready" && event.sequence <= state.highest)) {
      state = { highest: 0, seen: new Set<number>() };
      this.states.set(key, state);
    }
    if (state.seen.has(event.sequence)) {
      reportContractDiagnostic(
        `${this.stream}:duplicate:${key}:${event.sequence}`,
        `${this.stream} ignored duplicate sequence ${event.sequence} for ${key}`,
      );
      return false;
    }
    if (event.sequence < state.highest) {
      reportContractDiagnostic(
        `${this.stream}:out-of-order`,
        `${this.stream} received out-of-order sequence ${event.sequence} after ${state.highest}`,
      );
    }
    state.seen.add(event.sequence);
    state.highest = Math.max(state.highest, event.sequence);
    if (state.seen.size > EVENT_SEQUENCE_WINDOW * 2) {
      const cutoff = state.highest - EVENT_SEQUENCE_WINDOW;
      for (const sequence of state.seen) {
        if (sequence < cutoff) state.seen.delete(sequence);
      }
    }
    return true;
  }
}

const SESSION_EVENT_TYPES = new Set<string>([
  "ready",
  "inbound",
  "turn_start",
  "assistant_delta",
  "reasoning_delta",
  "assistant_message",
  "tool_proposed",
  "permission_required",
  "directory_requested",
  "question_requested",
  "plan_proposed",
  "tool_started",
  "tool_finished",
  "iteration_end",
  "turn_end",
  "error",
  "input_rejected",
  "interrupted",
  "model_changed",
  "memory_saved",
  "compacting",
  "compacted",
  "turn_done",
]);
const APP_EVENT_TYPES = new Set<string>(["automation_run_started"]);

export interface RecentWorkspace {
  path: string;
  name: string;
  exists: boolean;
}

export interface WorkspaceCommandTrust {
  workspace: string;
  requested_commands: string[];
  trusted: boolean;
  required: boolean;
  exists?: boolean;
}

export async function getHealth(): Promise<Health> {
  const raw: unknown = await directHealth();
  if (!isRecord(raw)) throw new RuntimeContractError("health response must be an object");
  const body = raw;
  if (
    typeof body.status !== "string" ||
    (body.default_workspace !== null && typeof body.default_workspace !== "string") ||
    typeof body.model !== "string" ||
    typeof body.protocolVersion !== "number" ||
    !Number.isInteger(body.protocolVersion) ||
    !Array.isArray(body.capabilities)
  ) {
    throw new RuntimeContractError("health response does not match the current runtime contract");
  }
  const protocolVersion = body.protocolVersion;
  if (protocolVersion !== UI_PROTOCOL_VERSION) {
    throw new RuntimeContractError(
      `unsupported runtime protocolVersion ${protocolVersion}; expected ${UI_PROTOCOL_VERSION}`,
    );
  }
  const advertised = body.capabilities.filter(
    (value): value is string => typeof value === "string",
  );
  const capabilities: RuntimeCapability[] = [];
  for (const capability of advertised) {
    if (runtimeCapabilities.has(capability)) {
      capabilities.push(capability as RuntimeCapability);
    } else {
      reportContractDiagnostic(
        `capability:unknown:${capability}`,
        `ignored unknown runtime capability "${capability}"`,
      );
    }
  }
  return {
    status: body.status,
    default_workspace: body.default_workspace,
    model: body.model,
    protocolVersion,
    capabilities,
  };
}

export async function getRecentWorkspaces(): Promise<RecentWorkspace[]> {
  const out = (await directRecentWorkspaces()) as { workspaces?: RecentWorkspace[] };
  return out.workspaces ?? [];
}

/** Open the native OS folder picker; null on cancel/unavailable. */
export async function pickFolderViaServer(): Promise<string | null> {
  const path = await directPickFolder();
  return typeof path === "string" && path ? path : null;
}

export async function openWorkspace(
  path: string,
  create = false,
): Promise<{
  path: string;
  ok: boolean;
  error?: string;
  git_branch?: string | null;
  command_trust?: WorkspaceCommandTrust;
}> {
  return await directOpenWorkspace(path, create);
}

export async function getTrustedWorkspaces(): Promise<WorkspaceCommandTrust[]> {
  const out = await directTrustedWorkspaces();
  return out.workspaces ?? [];
}

export async function setWorkspaceTrusted(
  path: string,
  trusted: boolean,
): Promise<{ ok: boolean; error?: string } & WorkspaceCommandTrust> {
  return await directSetWorkspaceTrusted(path, trusted);
}

export async function revertSession(
  sessionId: string,
  index: number,
): Promise<{ ok: boolean; error?: string; text?: string }> {
  return await directSessionRevert(sessionId, index);
}

export async function setReasoningEffort(
  sessionId: string,
  effort: string,
): Promise<{ ok: boolean; error?: string; reasoning_effort?: string }> {
  return await directSessionSetReasoning(sessionId, effort);
}

export async function getSessions(workspace?: string): Promise<SessionInfo[]> {
  const out = (await directListSessions(workspace)) as { sessions?: SessionInfo[] };
  return out.sessions ?? [];
}

// A structured connector-delivered inbound message (§3.1). Attached to the user message it framed,
// for display only — the model still sees the framed `content`; this drives the ConnectorMessageCard.
export type MessageSource = MessageSourceDto;

// A transcript message returned by Rust session authority. Kept permissive (open shape) because
// itemsFromMessages reads several role-specific fields; `source` is the optional connector sidecar.
export type ConversationMessage = MessageDto;

export async function getSessionMessages(sessionId: string): Promise<ConversationMessage[]> {
  const out = (await directSessionMessages(sessionId)) as {
    messages?: ConversationMessage[];
  };
  return out.messages ?? [];
}

export async function renameSession(sessionId: string, title: string): Promise<{ ok: boolean; error?: string }> {
  return (await directSessionRename(sessionId, title)) as { ok: boolean; error?: string };
}

export async function setSessionFlags(
  sessionId: string,
  flags: { pinned?: boolean; archived?: boolean },
): Promise<{ ok: boolean; error?: string }> {
  return (await directSessionSetFlags(sessionId, flags)) as { ok: boolean; error?: string };
}

export async function deleteSession(sessionId: string): Promise<{ ok: boolean; error?: string }> {
  return (await directSessionDelete(sessionId)) as { ok: boolean; error?: string };
}

export type ArtifactInfo = ArtifactDto;

export interface ArtifactContent {
  ok: boolean;
  error?: string;
  path: string;
  kind: string;
  content?: string;
  data_url?: string;
  truncated?: boolean;
  // kind === "folder": a directory listing (models sometimes link a whole package dir).
  entries?: { name: string; dir: boolean; size: number }[];
  // kind === "sheet": server-parsed workbook preview (the GUI never parses xlsx itself —
  // npm xlsx is vulnerable; P1 security fix 2026-08-25). Rows are row-limited server-side.
  sheets?: { name: string; rows: unknown[][]; total_rows: number; truncated: boolean }[];
}

export async function getArtifacts(sessionId: string): Promise<ArtifactInfo[]> {
  const out = await directListArtifacts(sessionId);
  return out.artifacts ?? [];
}

export async function readArtifact(sessionId: string, path: string): Promise<ArtifactContent> {
  return await directReadArtifact(sessionId, path);
}

/** Show the artifact in the OS file manager ("reveal") or open it with its default app ("open"). */
export async function revealArtifact(
  sessionId: string,
  path: string,
  mode: "reveal" | "open" = "reveal",
): Promise<{ ok: boolean; error?: string }> {
  const resolved = await directResolveArtifactPath(sessionId, path);
  if (!resolved.ok || typeof resolved.path !== "string") {
    return { ok: false, error: resolved.error || "Artifact is unavailable" };
  }
  try {
    if (mode === "open") await openPath(resolved.path);
    else await revealItemInDir(resolved.path);
    return { ok: true };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

// -- session roots (orphan Delta: scratch + added folders) -------------------
export interface RootInfo {
  path: string;
  writable: boolean;
  label: string;
  primary: boolean;
  exists: boolean;
}

export async function getRoots(sessionId: string): Promise<RootInfo[]> {
  const out = await directSessionRoots(sessionId);
  return out.roots ?? [];
}

export async function addRoot(
  sessionId: string,
  path: string,
  writable: boolean,
): Promise<{ ok: boolean; error?: string; roots?: RootInfo[] }> {
  return await directSessionAddRoot(sessionId, path, writable);
}

export async function removeRoot(
  sessionId: string,
  path: string,
): Promise<{ ok: boolean; error?: string; roots?: RootInfo[] }> {
  return await directSessionRemoveRoot(sessionId, path);
}

// -- MCP servers --------------------------------------------------------------
export interface McpServer {
  name: string;
  enabled: boolean;
  transport: string;
  requires_approval: boolean;
  // "connected" | "configured" | "disabled" | and for auth:"oauth" servers:
  // "needs_auth" (no tokens yet) | "authorizing" (browser sign-in in flight)
  status: string;
  auth?: "oauth" | null;
  last_error?: string | null;
  tool_count: number | null;
  config: Record<string, any>;
}

export async function getMcpServers(): Promise<McpServer[]> {
  return (await directListMcp()).servers ?? [];
}

export async function addMcpServer(name: string, config: Record<string, any>) {
  return await directPutMcp(name, config);
}

export async function patchMcpServer(name: string, changes: Record<string, any>) {
  return await directPatchMcp(name, changes);
}

export async function deleteMcpServer(name: string) {
  return await directDeleteMcp(name);
}

export async function getMcpTools(
  name: string,
): Promise<{ ok: boolean; error?: string; tools: { name: string; description: string }[] }> {
  return await directMcpTools(name);
}

export async function reloadMcp() {
  return await directReloadMcp();
}

/** Connect one MCP server now. For OAuth servers this opens the system browser;
 * poll getMcpServers() for the status flip (authorizing → connected / needs_auth). */
export async function connectMcp(name: string): Promise<{ ok: boolean; started?: boolean }> {
  return await directConnectMcp(name);
}

/** Drop the connection and forget the stored OAuth tokens. */
export async function signoutMcp(name: string): Promise<{ ok: boolean }> {
  return await directSignoutMcp(name);
}

// -- connectors ---------------------------------------------------------------
export interface ConnectorField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  placeholder: string;
}

// A message from a sender not (yet) on the allow-list — parked instead of dropped (§19).
export interface ParkedMessage {
  id: string;
  platform: string;
  chat_id: string;
  chat_name: string | null;
  user_id: string;
  user_name: string | null;
  chat_type: string;
  text: string;
  ts: number;
  team_id?: string | null; // workspace (multi-workspace legacy); null on manual Socket Mode
}

// One connected Slack workspace (legacy multi-workspace; ids are workspace-scoped,
// so each workspace carries its OWN allow-list).
export interface SlackWorkspace {
  team_id: string;
  account: string;
  domain?: string; // slack.com subdomain — unique even when display names collide
  allowed_users: string[];
  allow_all: boolean;
  allowed_user_names?: Record<string, string | null>;
  approval_owner_ids?: string[];
  approval_owner_names?: Record<string, string | null>;
  // Who installed this workspace (authed_user) — pre-added to the allow-list on
  // connect (UX-027); the GUI marks their chip "you" and keys the setup card copy.
  installer_user_id?: string;
  installer_name?: string;
}

// One connected GitHub App installation (managed relay is multi-installation;
// sender logins are global but each installation keeps its OWN allow-list).
export interface GithubInstallation {
  installation_id: string;
  account_login: string; // the org/user the App is installed on
  account_type: string; // "Organization" | "User"
  repo_selection: string; // "all" | "selected"
  github_login: string; // the connecting user's own login
  allowed_users: string[]; // sender logins allowed to trigger work
  allow_all: boolean;
}

// One connected HubSpot portal (multi-portal: `hubspot:portal:<hub_id>` profiles).
export interface HubSpotPortal {
  hub_id: string;
  name: string;
  sandbox: boolean;
  default: boolean;
  managed: boolean;
  access: "read" | "write" | ""; // consent tier granted ("" = manual token, unknown)
}

// One connected Google account (multi-account: `gmail:account:<email>` /
// `google_calendar:account:<email>` profiles — same shape for both).
export interface GmailAccount {
  email: string;
  default: boolean;
  managed: boolean;
  scopes: string;
  needs_reauth: boolean;
}

// "Never show agents" — enforced locally in the tool layer; agents see silent
// omissions, the user sees counts on tool cards + Activity rows.
export interface GmailFilters {
  senders: string[];
  labels: string[];
}

// One account of a generic multi-account connector (`<name>:account:<id>`
// profiles — Notion workspaces, PostHog projects, …). Gmail/Calendar predate
// the generic layer and keep their email-keyed shape above.
export interface AccountRow {
  account_id: string;
  name: string; // display identity captured at connect (workspace name, email, …)
  default: boolean;
  managed: boolean;
}

export interface Connector {
  name: string;
  title: string;
  icon: string;
  blurb: string;
  // Pre-connect detail page copy (UX-DECISIONS §38): optional About paragraph
  // (empty → group omitted) + honest Access bullets.
  about?: string;
  access?: string[];
  auth: string;
  two_way: boolean;
  // Chat-platform capability, narrower than two_way: sessions can subscribe to channels.
  channels: boolean;
  available: boolean;
  fields: ConnectorField[];
  instructions: string[];
  connected: boolean;
  account: string | null;
  enabled: boolean;
  brand_color: string; // hex brand color, e.g. "#611f69" (fallback gray "#6b7280")
  logo: string; // stable logo id keyed into the frontend registry (empty → fallback glyph)
  aliases?: string[]; // extra typeahead terms ("calendar" surfaces Outlook)
  mcp?: boolean; // MCP-backed one-click (vendor-hosted MCP + local OAuth)
  allowed_users: string[]; // the allow-list (managed inline in the Connectors tab)
  allowed_user_names?: Record<string, string | null>; // id → display name (people directory)
  approval_owner_ids?: string[]; // Manual Slack: humans allowed to resolve approvals
  approval_owner_names?: Record<string, string | null>;
  recent?: RecentSender[]; // recently-seen senders on a connected two-way connector
  unauthorized?: ParkedMessage[]; // parked messages from unallowed senders (§19)
  tools: ConnectorTool[];
  // Whether a future Federation adapter could offer a no-token install for
  // this connector. Always false today; the field stays so descriptors can
  // declare capability without a runtime change.
  managed: boolean;
  // One-click temporarily off (e.g. Google CASA pending) — badge "Coming soon"
  managed_paused?: boolean;
  // Current profile came from managed OAuth (vs manual paste) — only true
  // for legacy profiles installed before the broker was removed; new profiles
  // are always manual.
  managed_profile: boolean;
  // "mcp" for MCP-backed profiles; "" (default) for manual connect.
  // The "relay" value was used by the now-removed managed relay and is no
  // longer set.
  mode?: string;
  // Slack only: connected workspaces (legacy managed-relay multi-workspace)
  workspaces?: SlackWorkspace[];
  // Gmail/Calendar: email-keyed rows; generic account connectors (notion,
  // attio, posthog, …): AccountRow. The detail pages narrow by connector.
  accounts?: GmailAccount[] | AccountRow[];
  filters?: GmailFilters; // Gmail only: "Never show agents" senders/labels
  portals?: HubSpotPortal[]; // HubSpot only: connected portals (multi-portal)
  hidden_fields?: string[]; // HubSpot only: properties stripped from agent reads
  // GitHub only: App installations (legacy managed-relay multi-install)
  installations?: GithubInstallation[];
}

// --- Connector connect helpers ---

/** One-click connect for an MCP-backed connector (monday, asana, jira): the sidecar
 * opens the vendor's sign-in in the browser (local OAuth, no cloud account needed);
 * poll getConnectors until the card flips to connected. */
export async function connectMcpBacked(name: string): Promise<{ ok: boolean; error?: string }> {
  return await directConnectMcp(name);
}

export interface ConnectorTool {
  name: string;
  label: string;
  kind: "read" | "write" | string;
  description: string;
  enabled: boolean;
  requires_approval: boolean;
}

export async function getConnectors(): Promise<Connector[]> {
  return (await directListConnectors()).connectors ?? [];
}

export async function connectConnector(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; account?: string; error?: string }> {
  return await directConnectConnector(name, fields);
}

export async function disconnectConnector(name: string): Promise<{ ok: boolean }> {
  return await directDisconnectConnector(name);
}

export async function updateConnectorTools(
  name: string,
  enabled: Record<string, boolean>,
): Promise<{ ok: boolean; error?: string; tools?: Record<string, boolean> }> {
  return await directUpdateConnectorTools(name, enabled);
}

export interface AuditEvent {
  id: number;
  timestamp: string;
  session_id: string;
  agent: string;
  workspace: string;
  connector: string;
  tool: string;
  stage: string;
  status: string;
  approval: string;
  args: Record<string, any>;
  result_preview: string;
  reason: string;
  resource: string;
}

export async function getAudit(params: {
  limit?: number;
  session_id?: string;
  connector?: string;
  tool?: string;
} = {}): Promise<AuditEvent[]> {
  const out = await directListAudit({
    limit: params.limit,
    sessionId: params.session_id,
    connector: params.connector,
    tool: params.tool,
  });
  return out.events ?? [];
}

export interface BrowserState {
  open: boolean;
  url: string;
  title: string;
  status: string;
  last_action: string;
  last_result: string;
  last_error: string;
  screenshot_data_url: string;
  updated_at: string | null;
  controls: any[];
}

export async function getBrowserState(): Promise<BrowserState> {
  return await directBrowserState();
}

export async function takeBrowserScreenshot(): Promise<BrowserState & { ok?: boolean; error?: string }> {
  return await directBrowserScreenshot();
}

export async function closeBrowser(): Promise<{ ok?: boolean; error?: string }> {
  return await directBrowserClose();
}

// -- settings (model API key, default model, onboarding) ----------------------
export interface ModelSettings {
  provider: string;
  model: string;
  models: string[];
  has_key: boolean;
  model_ready: boolean; // can the default model's provider actually run (any provider)?
  source: "env" | "store" | null;
  onboarded: boolean;
  // UI/agent language (a Locale like "zh-CN" / "en-US"). Absent → null: the GUI falls back
  // to its own default rather than the server guessing.
  language?: string | null;
  scratch_base: string;
  secrets_path: string;  // OS-native on-disk location the server reports (not hardcoded)
  // Sidebar: sessions shown per group before "Show more" (default 5, 1–50).
  sessions_peek?: number;
  // Composer: show the context-window fill bar (default FALSE; absent → the chip shows
  // the session total). The usage popover keeps both numbers regardless.
  context_bar?: boolean;
  // Curated-matrix display names ({full id → "GLM-5.2 · via Together"}); custom models absent.
  model_labels?: Record<string, string>;
  // {full id → context window in tokens}, verified matrix entries only — drives the
  // composer's context-fill meter (absent id → the meter hides). Optional for older backends.
  model_context_windows?: Record<string, number>;
  // Token savings (PDF attachments): fallback for models without native PDF support,
  // and attach-time thresholds. Optional so the GUI is robust to an older backend.
  pdf_fallback?: "text" | "images";
  pdf_max_pages?: number; // default 20, 1–100
  pdf_max_mb?: number; // default 10, 1–10
  // Auto-compaction of long histories (OPE-27): trigger = min(threshold% × context
  // window, cap tokens); model pins the summarizer ("" → the session's own model).
  // Optional so the GUI is robust to an older backend.
  compaction_threshold_pct?: number; // default 0.8, 0.10–0.95
  compaction_cap_tokens?: number; // default 250000
  compaction_model?: string;
}

export interface PdfSettings {
  pdf_fallback: "text" | "images";
  pdf_max_pages: number;
  pdf_max_mb: number;
}

/** Persist the Token-savings PDF settings (fallback mode + attach thresholds). */
export async function setPdfSettings(
  patch: Partial<PdfSettings>,
): Promise<{ ok: boolean; error?: string } & Partial<PdfSettings>> {
  return directSetPdfSettings(patch) as Promise<
    { ok: boolean; error?: string } & Partial<PdfSettings>
  >;
}

export interface CompactionSettings {
  compaction_threshold_pct: number;
  compaction_cap_tokens: number;
  compaction_model: string;
}

/** Persist the auto-compaction overrides (threshold %, token cap, summarizer model). */
export async function setCompactionSettings(
  patch: Partial<CompactionSettings>,
): Promise<{ ok: boolean; error?: string }> {
  return directSetCompactionSettings(patch) as Promise<{ ok: boolean; error?: string }>;
}

/** Local page/size probe for a PDF data URL — the composer's attach-time threshold check. */
export async function inspectPdf(
  dataUrl: string,
): Promise<{ ok: boolean; pages?: number; bytes?: number; error?: string }> {
  try {
    const encoded = dataUrl.split(",", 2)[1] || "";
    const binary = atob(encoded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    const pdfjs = await import("pdfjs-dist");
    pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
    const loading = pdfjs.getDocument({ data: bytes });
    const document = await loading.promise;
    const pages = document.numPages;
    await loading.destroy();
    return { ok: true, pages, bytes: bytes.byteLength };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

/** Persist whether the composer shows the context-window fill bar. */
export async function setContextBar(
  shown: boolean,
): Promise<{ ok: boolean; context_bar?: boolean; error?: string }> {
  return directSetContextBar(shown);
}

/** Persist how many sessions a sidebar group shows before "Show more". */
export async function setSessionsPeek(
  n: number,
): Promise<{ ok: boolean; sessions_peek?: number; error?: string }> {
  return directSetSessionsPeek(n);
}

export async function setScratchBase(
  path: string,
): Promise<{ ok: boolean; error?: string; scratch_base?: string }> {
  return directSetScratchBase(path);
}

export const INBOX_UNLOCK = "delta:inbox-unlock";
export function announceInboxUnlock() {
  window.dispatchEvent(new CustomEvent(INBOX_UNLOCK));
}

// -- Per-session connections (Sources bar + drawer, §6) -----------------------
// An effective-enabled connector for a session, with a short human detail (e.g. "#delta-test · DMs").
// `enabled` reflects the session override so the drawer toggle shows the authoritative state.
export interface SessionConnectedConnector {
  connector: string;
  enabled: boolean;
  detail: string;
}

// A recommended connector not yet connected (drives the `⚠ N` attention count).
export interface SessionRecommendedConnector {
  connector: string;
  reason: string;
  tier: string;
  connected: boolean;
}

export interface SessionConnections {
  connected: SessionConnectedConnector[];
  recommended: SessionRecommendedConnector[];
  attention: number; // ⚠ count = recommended connectors not yet connected
}

export async function getSessionConnections(sessionId: string): Promise<SessionConnections> {
  return await directSessionConnections(sessionId);
}

/**
 * Set a per-session connection override (mute/unmute a connector for THIS session). Pass
 * `clear: true` to drop the session override.
 */
export async function setSessionConnection(
  sessionId: string,
  connector: string,
  enabled: boolean,
  clear = false,
): Promise<{ ok: boolean; error?: string }> {
  return await directSetSessionConnection(sessionId, connector, enabled, clear);
}

// -- Skills (SKILLS-SPEC §4) ----------------------------------------------------
// Scope = folder location: "global" (every session) or "project" (one workspace).
// The session endpoints resolve the effective menu (Settings disables + session mutes).

export interface SkillRow {
  name: string;
  description: string;
  instructions: string;
  scope: "global" | "project";
  source: string; // "local" | "uploaded"
  enabled: boolean;
  path: string;
  files?: number; // bundled resources beyond SKILL.md (§6 — rich skills are visible)
}

export interface SessionSkillRow {
  name: string;
  description: string;
  scope: "global" | "project";
  enabled: boolean; // false = muted for this session only
}

export interface SkillUploadPreview {
  ok: boolean;
  error?: string;
  token?: string;
  name?: string;
  description?: string;
  instructions?: string;
  files?: string[];
}

export async function listSkills(workspace?: string): Promise<SkillRow[]> {
  return (await directListSkills(workspace)).skills ?? [];
}

export async function createSkill(body: {
  name: string;
  description: string;
  instructions: string;
  scope?: "global" | "project";
  workspace?: string;
}): Promise<{ ok: boolean; error?: string }> {
  return await directCreateSkill(body);
}

export async function updateSkill(
  name: string,
  patch: { description?: string; instructions?: string; enabled?: boolean; workspace?: string },
): Promise<{ ok: boolean; error?: string }> {
  return await directUpdateSkill(name, patch);
}

export async function revealSkill(name: string): Promise<{ ok: boolean; error?: string }> {
  const resolved = await directResolveSkillFolder(name);
  if (!resolved.ok || typeof resolved.path !== "string") {
    return { ok: false, error: resolved.error || "Skill is unavailable" };
  }
  try {
    await revealItemInDir(resolved.path);
    return { ok: true };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

export async function deleteSkill(
  name: string,
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  return await directDeleteSkill(name, workspace);
}

export async function moveSkill(
  name: string,
  scope: "global" | "project",
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  return await directMoveSkill(name, scope, workspace);
}

export async function stageSkillUpload(
  dataB64: string,
  filename = "",
): Promise<SkillUploadPreview> {
  return await directStageSkillUpload(dataB64, filename);
}

export async function confirmSkillUpload(
  token: string,
  scope: "global" | "project" = "global",
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  return await directConfirmSkillUpload(token, scope, workspace);
}


export async function sessionSkills(
  sessionId: string,
  workspace?: string,
): Promise<SessionSkillRow[]> {
  return (await directSessionSkills(sessionId, workspace)).skills ?? [];
}

export async function setSessionSkill(
  sessionId: string,
  skill: string,
  enabled: boolean,
  opts: { clear?: boolean; workspace?: string } = {},
): Promise<{ skills?: SessionSkillRow[]; ok?: boolean; error?: string }> {
  return await directSetSessionSkill(sessionId, skill, enabled, !!opts.clear, opts.workspace);
}

// -- Inbox + Unattended -------------------------------------------------------
export interface InboxItem {
  id: string;
  session_id: string;
  kind: "approval" | "question" | "notification" | "directory" | "plan";
  title: string;
  body: string;
  state: "pending" | "resolved";
  resolution: string | null;
  inbox: string;
  created_at: string;
  resolved_at: string | null;
  visibility?: "inline" | "inbox";
  // Question metadata (ask_user): quick-reply choices + a free-text escape. Options may be rich
  // {label, description, recommended, preview} objects (OPE-51); `questions` is the grouped form
  // (stepper), whose resolution is a JSON object string keyed by header-or-question.
  options?: QuestionOption[];
  allow_text?: boolean;
  multi?: boolean;
  header?: string;
  questions?: GroupedQuestion[];
  // Kind-specific payload (directory: {path, writable}; …).
  data?: Record<string, any>;
  // Originating-session context (server-joined) so the Inbox is self-contained.
  session_title?: string;
  session_agent?: string | null;
  session_workspace?: string | null;
  session_exists?: boolean;
}

export async function getInbox(sessionId?: string, state?: string): Promise<InboxItem[]> {
  const out = await directListInbox(sessionId, state);
  return out.items ?? [];
}

export async function resolveInboxItem(
  id: string,
  resolution: string,
): Promise<{ ok: boolean }> {
  return await directResolveInbox(id, resolution);
}

// -- channel subscriptions (view-only) ----------------------------------------
export interface Subscription {
  session_id: string;
  session_title: string;
  agent: string;
  channel: string;
  channel_name?: string | null; // resolved display name ("delta-test"); address stays the id
  routing_target: string | null;
  collision: boolean; // inbound subscription == outbound Inbox routing on the same channel
}

export interface RecentChannel {
  channel: string;
  name?: string | null; // resolved display name, e.g. "delta-test" (falls back to the address)
  last_from: string | null;
  last_text: string | null;
}

export async function getSubscriptions(): Promise<Subscription[]> {
  return (await directListSubscriptions()).subscriptions ?? [];
}

// -- inbox routing (where Unattended approvals/questions get mirrored) ---------
export interface InboxBinding {
  name: string;
  channel: string | null; // platform, e.g. "slack" (null = in-app Inbox only)
  target: string; // chat_id, e.g. "C0BEJNCQQ8Y"
}

export async function getInboxRouting(): Promise<InboxBinding[]> {
  return (await directListInboxRouting()).bindings ?? [];
}

export async function setInboxBinding(
  name: string,
  channel: string | null,
  target: string,
): Promise<{ ok: boolean; bindings?: InboxBinding[]; error?: string }> {
  return await directSetInboxRouting(name, channel, target);
}

export interface UnroutedItem {
  source: string;
  sender: string;
  text: string;
  reason: string;
  ts: number;
}

export async function getUnrouted(): Promise<UnroutedItem[]> {
  return (await directListUnrouted()).items ?? [];
}

export async function getRecentChannels(): Promise<RecentChannel[]> {
  return (await directRecentChannels()).channels ?? [];
}

export async function subscribeChannel(
  sessionId: string,
  channel: string,
): Promise<{ ok: boolean; channel?: string; error?: string }> {
  return await directAddSubscription(sessionId, channel);
}

export async function unsubscribeChannel(
  sessionId: string,
  channel: string,
): Promise<{ ok: boolean; removed?: boolean }> {
  return await directRemoveSubscription(sessionId, channel);
}

export async function getUnattended(sessionId: string): Promise<boolean> {
  return !!(await directGetUnattended(sessionId)).unattended;
}

export async function setUnattended(
  sessionId: string,
  unattended: boolean,
): Promise<{ ok: boolean; unattended: boolean }> {
  return await directSetUnattended(sessionId, unattended);
}

export async function getSettings(): Promise<ModelSettings> {
  return directGetSettings() as Promise<ModelSettings>;
}

export async function setModelKey(
  apiKey: string,
): Promise<{ ok: boolean; error?: string; has_key?: boolean; source?: string }> {
  return directSetModelKey(apiKey);
}

export async function setDefaultModel(
  model: string,
): Promise<{ ok: boolean; error?: string; model?: string }> {
  return directSetDefaultModel(model);
}

export async function addModel(model: string): Promise<ModelSettings & { ok: boolean; error?: string }> {
  return directAddModel(model) as Promise<ModelSettings & { ok: boolean; error?: string }>;
}

export async function removeModel(model: string): Promise<ModelSettings & { ok: boolean }> {
  return directRemoveModel(model) as Promise<ModelSettings & { ok: boolean }>;
}

export async function setOnboarded(value: boolean): Promise<{ ok: boolean; onboarded: boolean }> {
  return directSetOnboarded(value);
}

/** Persist the UI/agent language (a Locale like `zh-CN` / `en-US`). */
export async function setLanguage(
  language: string,
): Promise<{ ok: boolean; language?: string | null } & Partial<ModelSettings>> {
  return directSetLanguage(language);
}

// -- Memory (MEMORY-SPEC §5.3/§6: the memory screen, user rules, toast Undo) ----

export interface MemoryEntry {
  id: number;
  scope: string;
  content: string;
  summary: string;
  created_at: string;
}

export interface MemorySettings {
  enabled: boolean;
  user_rules: string;
}

// Fired whenever memory changes from OUTSIDE the memory screen — today the agent
// saving or editing one mid-conversation. The screen only loads its list on mount, so
// without this it sits there stale and the user reads "Nothing yet" seconds after a
// save actually landed (owner-hit 2026-07-28).
export const MEMORY_CHANGED = "delta:memory-changed";
export function announceMemoryChanged() {
  window.dispatchEvent(new CustomEvent(MEMORY_CHANGED));
}

export async function getMemory(): Promise<MemoryEntry[]> {
  return (await directListMemory()).memory ?? [];
}

export async function updateMemory(
  id: number,
  content: string,
): Promise<{ ok: boolean; error?: string }> {
  return await directUpdateMemory(id, content);
}

export async function deleteMemory(id: number): Promise<{ ok: boolean; error?: string }> {
  return await directDeleteMemory(id);
}

export async function deleteAllMemory(): Promise<{ ok: boolean; deleted: number }> {
  return await directDeleteAllMemory();
}

export async function getMemorySettings(): Promise<MemorySettings> {
  return await directGetMemorySettings();
}

export async function setMemorySettings(
  patch: Partial<MemorySettings>,
): Promise<MemorySettings> {
  return await directSetMemorySettings(patch);
}

// -- model providers -----------------------------------------------------------
export interface ProviderField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  placeholder: string;
  default?: string; // pre-filled editable value (e.g. an OpenAI-compatible vendor's endpoint)
  // Non-empty → segmented choice, not a text input. tag = tiny badge ("Easiest");
  // desc = one-liner atop the method panel; command = copyable terminal command.
  choices?: { value: string; label: string; tag?: string; desc?: string; command?: string }[];
  show_when?: Record<string, string> | null; // render only while these fields hold these values
}

export interface ProviderInfo {
  name: string;
  title: string;
  needs_key: boolean;
  fields: ProviderField[];
  configured: boolean;
  values: Record<string, string>; // non-secret stored values (e.g. base_url), for prefilling
  suggested_models: string[]; // bare model-name suggestions for the "add model" datalist
  recommended_model: string | null; // pre-filled default for this provider (e.g. qwen3-coder:30b)
  blurb?: string; // one-line note under the title ("Uses X's OpenAI-compatible API…")
  key_set_at?: string | null; // ISO date the key was last (re)saved — absent for env-only config
  last_used_at?: number | null; // epoch secs the provider last served a completion
  // custom-config-first markers (backend-emitted); null for built-in providers
  custom?: boolean | null; // true for a user-defined alias
  protocol?: string | null; // protocol_id of a custom provider ("openai" or "anthropic")
  alias?: string | null; // the alias name for a custom provider (=== name when custom)
}

export interface ProviderProtocol {
  id: string; // "openai" or "anthropic"
  title: string; // dropdown label
  needs_key: boolean;
  fields: ProviderField[]; // the fields this protocol's form renders
  recommended_model: string | null;
  env_key?: string | null; // env var that can supply the key
  blurb?: string;
}

export async function getProviders(): Promise<ProviderInfo[]> {
  return directGetProviders() as Promise<ProviderInfo[]>;
}

/** The two protocol definitions for the custom-provider form's protocol dropdown. */
export async function getProtocols(): Promise<ProviderProtocol[]> {
  return directGetProtocols() as Promise<ProviderProtocol[]>;
}

export async function setProvider(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; error?: string; provider?: string; recommended_model?: string | null }> {
  return directSetProvider(name, fields);
}

/** Forget a provider's stored config (Settings ▸ Models "Remove key…"). */
export async function removeProvider(name: string): Promise<{ ok: boolean; error?: string }> {
  return directRemoveProvider(name);
}

/**
 * Create or update a user-defined provider alias. Pass `protocol` to create (or
 * update) a custom provider; omit it to hit the built-in provider path.
 */
export async function createCustomProvider(
  alias: string,
  protocol: string,
  fields: Record<string, string>,
): Promise<{
  ok: boolean;
  error?: string;
  provider?: string;
  protocol?: string;
  recommended_model?: string | null;
}> {
  return directSetProvider(alias, fields, protocol);
}

/**
 * Remove a custom provider alias entirely (unregister + drop its alias: models).
 * Returning `{ok:false}` for a built-in name here is expected; use removeProvider
 * for built-ins.
 */
export async function removeCustomProvider(
  alias: string,
): Promise<{ ok: boolean; error?: string }> {
  return directRemoveProvider(alias);
}

/**
 * Fetch a configured (or just-entered) custom provider's model list and auto-add
 * each id as `alias:{id}` per "按前缀自动加入". Returns the bare ids plus what was
 * newly added.
 */
export async function fetchModels(
  name: string,
  fields: Record<string, string>,
): Promise<{
  ok: boolean;
  error?: string;
  alias?: string;
  models?: string[];
  added?: string[];
}> {
  return directFetchProviderModels(name, fields);
}

/** Live read-only credential check (does NOT save the key). Triggered by the user's "Test" click. */
export async function verifyProvider(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; error?: string }> {
  return directVerifyProvider(name, fields);
}

/** Client-side provider guess from an API key's shape (mirrors the server's detect_provider). */
export function detectProvider(apiKey: string): string | null {
  const key = (apiKey || "").trim();
  if (!key) return null;
  if (key.startsWith("sk-ant-")) return "anthropic";
  if (key.startsWith("sk-or-")) return "openrouter";
  if (key.startsWith("sk-") || key.startsWith("sk_")) return "openai";
  return null;
}

// -- super-agent --------------------------------------------------------------
export interface RecentSender {
  user_id: string;
  user_name: string | null;
  chat_id: string;
  chat_type: string;
  target: string;
  authorized: boolean;
  team_id?: string | null; // workspace (managed relay); null on manual Socket Mode
}

// -- direct-message routing ---------------------------------------------------
export async function getDmRoute(): Promise<string | null> {
  return (await directGetDmRoute()).dm_session ?? null;
}

export async function setDmRoute(sessionId: string): Promise<{ ok: boolean; dm_session: string | null }> {
  return await directSetDmRoute(sessionId);
}

// -- automations (scheduled tasks) --------------------------------------------
export interface Automation {
  id: string;
  title: string;
  instructions: string;
  schedule: string;
  schedule_raw?: { kind: string; cron?: string | null; fire_at?: string | null; timezone?: string };
  workspace: string;
  enabled: boolean;
  next_run: number | null;
  last_run: number | null;
  last_status: string | null;
  run_count: number;
  notify_on_completion: boolean;
  // UX-023 sidebar badges: runs started since the user last opened this automation's
  // detail; `unseen_failed` = the newest unseen run errored (danger tint).
  unseen_runs?: number;
  unseen_failed?: boolean;
  seen_runs_at?: number;
  // Standing scoped approvals (§25): target-bound rules this automation may exercise
  // without asking. `entry` is the raw record entry — the revoke handle; `target` is
  // null for legacy name-only entries.
  always_allowed: { entry: string; tool: string; target: string | null }[];
}

export interface AutomationRun {
  run_id: string;
  task_id: string;
  session_id: string;
  started_at: number;
  finished_at: number | null;
  status: string;
  result_text: string | null;
  artifacts: string[];
  error: string | null;
  trigger: string;
}

export async function getAutomations(): Promise<Automation[]> {
  return (await directListAutomations()).tasks ?? [];
}

// Fired after any automation mutation the sidebar should reflect immediately
// (mark-seen, create, delete) — its poll covers the rest.
export const AUTOMATIONS_CHANGED = "delta:automations-changed";
export function announceAutomationsChanged() {
  window.dispatchEvent(new CustomEvent(AUTOMATIONS_CHANGED));
}

/** App-wide native event stream for session-independent runtime events. */
export function connectEvents(
  onEvent: (msg: {
    type: string;
    version: 1;
    sessionId: string | null;
    sequence: number;
    payload: Record<string, unknown>;
  }) => void
): () => void {
  const sequenceGate = new RuntimeEventSequenceGate("app events");
  return directListenApp((event) => {
    if (APP_EVENT_TYPES.has(event.type) && sequenceGate.accept(event)) onEvent(event);
  });
}

/** Advance the automation's seen mark — clears its unseen-runs badge (UX-023). */
export async function markAutomationSeen(id: string): Promise<{ ok: boolean }> {
  return await directMarkAutomationSeen(id);
}

export async function createAutomation(payload: {
  title: string;
  instructions: string;
  cron?: string;
  fire_at?: string;
  timezone?: string;
  // §25 standing grants (the creating surface rendered them; submit IS the consent).
  // Only target-bound write entries survive server-side validation.
  permissions?: { tool: string; target: string; access: "read" | "write" }[];
}): Promise<{ ok: boolean; error?: string; task?: Automation }> {
  return await directCreateAutomation(payload);
}

export async function getAutomation(id: string): Promise<{ task: Automation; runs: AutomationRun[] }> {
  return await directGetAutomation(id);
}

export async function updateAutomation(id: string, changes: Record<string, any>) {
  return await directUpdateAutomation(id, changes);
}

export async function deleteAutomation(id: string) {
  return await directDeleteAutomation(id);
}

export interface PreparedRun {
  ok: boolean;
  error?: string;
  run_id: string;
  session_id: string;
  workspace: string;
  prompt: string;
}

/** Prepare a live manual run: returns the session to open + the opening prompt to send. */
export async function runAutomation(id: string): Promise<PreparedRun> {
  return await directPrepareAutomationRun(id);
}

/** Mark a manual run complete after its first turn finished. */
export async function finalizeAutomationRun(id: string, runId: string) {
  return await directFinalizeAutomationRun(id, runId);
}

export async function allowUser(
  name: string,
  userId: string,
  teamId?: string | null,
  displayName?: string,
) {
  return await directConnectorAction(name, "allow_user", {
    user_id: userId,
    ...(teamId ? { team_id: teamId } : {}),
    ...(displayName ? { name: displayName } : {}),
  });
}

// One workspace member from the roster (people picker; users:read, cached locally).
export interface SlackMember {
  id: string;
  name: string;
  handle: string;
  guest: boolean;
}

// One channel from the workspace roster. Private channels appear only where the
// bot is a member (Slack API constraint); is_member=false → "invite @Delta" hint.
export interface SlackChannelEntry {
  id: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
}

/** Workspace member roster for the people picker (teamId "default" = manual Socket Mode). */
export async function getSlackDirectory(
  teamId: string,
  q = "",
): Promise<{ ok: boolean; error?: string; members?: SlackMember[] }> {
  return await directConnectorAction("slack", "directory", { team_id: teamId, q });
}

/** Channel roster for the channel typeahead (name → id resolution). */
export async function getSlackChannels(
  teamId: string,
  q = "",
): Promise<{ ok: boolean; error?: string; channels?: SlackChannelEntry[] }> {
  return await directConnectorAction("slack", "channels", { team_id: teamId, q });
}

/** Resolve a parked unauthorized message (§19): dismiss / allow / allow_deliver. */
export async function resolveUnauthorized(
  name: string,
  itemId: string,
  action: "dismiss" | "allow" | "allow_deliver",
): Promise<{ ok: boolean; error?: string }> {
  return await directConnectorAction(name, "resolve_unauthorized", {
    item_id: itemId,
    action,
  });
}

export async function disallowUser(name: string, userId: string, teamId?: string | null) {
  return await directConnectorAction(name, "disallow_user", {
    user_id: userId,
    ...(teamId ? { team_id: teamId } : {}),
  });
}

export async function addSlackApprovalOwner(
  userId: string,
  displayName?: string,
): Promise<{ ok: boolean; error?: string }> {
  return await directConnectorAction("slack", "add_approval_owner", {
    user_id: userId,
    ...(displayName ? { name: displayName } : {}),
  });
}

export async function removeSlackApprovalOwner(
  userId: string,
): Promise<{ ok: boolean; error?: string }> {
  return await directConnectorAction("slack", "remove_approval_owner", { user_id: userId });
}

/** Disconnect one legacy managed Slack workspace (the app stays installed in Slack). */
export async function disconnectSlackWorkspace(teamId: string): Promise<{ ok: boolean; error?: string; remaining_workspaces?: number }> {
  return await directConnectorAction("slack", "disconnect_workspace", { team_id: teamId });
}

/** Drop ONE Gmail mailbox; the default pointer moves to the next account. */
export async function disconnectGmailAccount(email: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  return await directConnectorAction("gmail", "disconnect_account", { account_id: email });
}

export async function setGmailDefaultAccount(email: string): Promise<{ ok: boolean; error?: string }> {
  return await directConnectorAction("gmail", "set_default_account", { account_id: email });
}

/** Drop ONE Google Calendar account; the default pointer moves to the next one. */
export async function disconnectGcalAccount(email: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  return await directConnectorAction("google_calendar", "disconnect_account", { account_id: email });
}

export async function setGcalDefaultAccount(email: string): Promise<{ ok: boolean; error?: string }> {
  return await directConnectorAction("google_calendar", "set_default_account", { account_id: email });
}

/** Drop ONE account of a generic multi-account connector (notion, attio,
 * posthog, …); the default pointer moves to the next account. */
export async function disconnectAccount(connector: string, accountId: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  return await directConnectorAction(connector, "disconnect_account", { account_id: accountId });
}

export async function setDefaultAccount(connector: string, accountId: string): Promise<{ ok: boolean; error?: string }> {
  return await directConnectorAction(connector, "set_default_account", { account_id: accountId });
}

/** Replace the "Never show agents" lists (senders and/or labels; omit to keep). */
export async function setGmailFilters(filters: { senders?: string[]; labels?: string[] }): Promise<{ ok: boolean; filters?: GmailFilters; error?: string }> {
  return await directConnectorAction("gmail", "set_filters", filters);
}

// GitHub health: socket (legacy) and per-installation token health (+ missed-event counts).
export interface GithubStatus {
  ok: boolean;
  mode: string;
  relay: { state: string; reconnects: number; last_event_at: number | null; last_error: string };
  installs: Record<string, { token_ok: boolean }>;
  missed: Record<string, number>;
}

export async function getGithubStatus(): Promise<GithubStatus> {
  return await directConnectorAction("github", "github_status");
}

/** Stop relaying ONE GitHub App installation to this computer. */
export async function disconnectGithubInstallation(installationId: string): Promise<{ ok: boolean; error?: string; remaining_installs?: number }> {
  return await directConnectorAction("github", "disconnect_installation", {
    installation_id: installationId,
  });
}

/** Drop ONE HubSpot portal; the default pointer moves to the next portal. */
export async function disconnectHubSpotPortal(hubId: string): Promise<{ ok: boolean; error?: string; remaining_portals?: number }> {
  return await directConnectorAction("hubspot", "disconnect_portal", { hub_id: hubId });
}

export async function setHubSpotDefaultPortal(hubId: string): Promise<{ ok: boolean; error?: string }> {
  return await directConnectorAction("hubspot", "set_default_portal", { hub_id: hubId });
}

/** Replace the hidden-fields denylist (properties stripped from agent reads). */
export async function setHubSpotHiddenFields(fields: string[]): Promise<{ ok: boolean; hidden_fields?: string[]; error?: string }> {
  return await directConnectorAction("hubspot", "set_hidden_fields", { fields });
}

/** Slack health: relay socket (legacy) and per-team token health. */
export interface SlackStatus {
  mode: string; // "relay" | "" (manual/off)
  relay: {
    state: "live" | "reconnecting" | "offline";
    reconnects: number;
    last_event_at: number | null;
    last_error: string;
  };
  teams: Record<string, { token_ok: boolean }>;
}

export async function getSlackStatus(): Promise<SlackStatus> {
  return await directConnectorAction("slack", "slack_status");
}

export type Handlers = {
  onEvent: (event: WsEvent) => void;
  onOpen?: () => void;
  onClose?: () => void;
};

export class Session {
  private unlisten: (() => void) | null = null;
  private stopped = false;
  private readonly sequenceGate = new RuntimeEventSequenceGate("session events");
  /** The current model selected by the composer, carried with every native run. */
  private model: string;
  private mode = "interactive";

  constructor(
    private readonly sessionId: string,
    private readonly workspace: string,
    private readonly handlers: Handlers,
  ) {
    this.model = "";
    this.connect();
  }

  private connect() {
    if (this.stopped) return;
    this.unlisten = directListenSession(this.sessionId, (raw) => {
      if (this.stopped) return;
      const event = raw as unknown as WsEvent;
      if (event?.sessionId === null) {
        reportContractDiagnostic(
          "session events:null-session",
          "session events rejected an envelope with a null sessionId",
        );
      } else if (event && event.sessionId !== this.sessionId) {
        reportContractDiagnostic(
          `session events:mismatched-session:${event?.sessionId}`,
          `session events rejected an envelope for session ${event?.sessionId}`,
        );
      } else if (event && SESSION_EVENT_TYPES.has(event.type) && this.sequenceGate.accept(event)) {
        this.handlers.onEvent(event);
      }
    });
    this.handlers.onOpen?.();
  }

  /** `model` = the composer's CURRENT selection, carried on every message so the turn uses
   * exactly what the user sees. */
  userMessage(text: string, attachments?: unknown[], model?: string, skill?: string) {
    void directRun({
      sessionId: this.sessionId,
      modelId: model || this.model,
      userInput: text,
      workspace: this.workspace,
      attachments,
      skill,
      mode: this.mode,
      onEvent: (ev) => {
        if (this.stopped) return;
        const event = ev as unknown as WsEvent;
        if (event && this.sequenceGate.accept(event)) this.handlers.onEvent(event);
      },
    });
  }

  approve(decision: string) {
    void directApproval(this.sessionId, decision);
  }

  // Reply to a `request_directory` prompt: grant a folder (with access level) or decline.
  respondDirectory(granted: boolean, path?: string, writable?: boolean) {
    void directDirectoryResponse(this.sessionId, granted, path, !!writable);
  }

  // Reply to a `propose_plan` prompt: approve (choosing the execution mode) or reject with feedback.
  respondPlan(approved: boolean, mode?: string, feedback?: string) {
    void directPlanResponse(this.sessionId, approved, mode, feedback);
  }

  // Answer a live `ask_user` prompt (attended sessions; unattended ones answer via the Inbox).
  respondQuestion(answer: string) {
    void directQuestionResponse(this.sessionId, answer);
  }

  interrupt() {
    void directCancel(this.sessionId);
  }

  // R6 Active-Run Steering: modify the CURRENT turn's direction mid-execution.
  // Differs from follow-up — steering applies to the live run, not after it ends.
  steer(text: string, source?: unknown) {
    void directSteer(this.sessionId, text, source);
  }

  // R6 Follow-up: queue a turn that runs AFTER the current one completes.
  followUp(text: string, source?: unknown) {
    void directFollowUp(this.sessionId, text, source);
  }

  // Re-run a turn that ended in a provider error — no new user message; the server
  // guards on the history tail so a stray frame is a no-op.
  retry() {
    void directRetry(this.sessionId);
  }

  setMode(mode: string) {
    this.mode = mode;
    void directSetMode(this.sessionId, mode);
  }

  setModel(model: string) {
    this.model = model;
    void directSwitchModel(this.sessionId, model);
  }

  close() {
    this.stopped = true;
    if (this.unlisten) {
      this.unlisten();
      this.unlisten = null;
    }
    this.handlers.onClose?.();
  }
}
