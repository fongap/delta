// R6 native bridge: React -> Tauri invoke/listen -> Rust Runtime.
// Commands use `invoke`; runtime events use `listen("delta-runtime-event")`.

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { RuntimeEventEnvelopeV1 } from "./runtime-contract";

/** The Tauri event name carrying runtime event frames. */
const RUNTIME_EVENT_CHANNEL = "delta-runtime-event";

export interface DirectRuntimeAcceptance {
  ok: boolean;
  accepted?: boolean;
  runId?: string;
  state?: string;
  error?: string;
}

// -----------------------------------------------------------------------------
// Runtime controls (agent loop verbs)
// -----------------------------------------------------------------------------

export async function directRun(args: {
  sessionId: string;
  modelId: string;
  userInput: string;
  workspace?: string;
  attachments?: unknown[];
  skill?: string;
  mode?: string;
  maxIterations?: number;
  maxRetries?: number;
  source?: unknown;
  onEvent: (event: WsEventEnvelope) => void;
  onError?: (error: unknown) => void;
}): Promise<DirectRuntimeAcceptance> {
  const {
    sessionId, modelId, userInput,
    workspace, attachments, skill, mode, maxIterations, maxRetries, source,
  } = args;
  try {
    const out = await invoke("runtime_run", {
      sessionId, modelId, userInput,
      workspace, attachments, skill, mode, maxIterations, maxRetries, source,
    });
    if (out && (out as any).error) {
      args.onError?.((out as any).error);
      return { ok: false, error: (out as any).error };
    }
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
      state: typeof (out as any).state === "string" ? (out as any).state : undefined,
    };
  } catch (e) {
    args.onError?.(e);
    return { ok: false, error: String(e) };
  }
}

export async function directResume(
  sessionId: string,
): Promise<DirectRuntimeAcceptance> {
  try {
    const out = await invoke("runtime_resume", { sessionId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directRetry(
  sessionId: string,
): Promise<DirectRuntimeAcceptance> {
  try {
    const out = await invoke("runtime_retry", { sessionId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directSteer(
  sessionId: string,
  text: string,
  source?: unknown,
): Promise<DirectRuntimeAcceptance> {
  try {
    const out = await invoke("runtime_steer", { sessionId, text, source });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directFollowUp(
  sessionId: string,
  text: string,
  source?: unknown,
): Promise<DirectRuntimeAcceptance> {
  try {
    const out = await invoke("runtime_follow_up", { sessionId, text, source });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directCancel(
  sessionId: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const out = await invoke("runtime_cancel", { sessionId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directMessages(
  sessionId: string,
): Promise<unknown[]> {
  try {
    const out = await invoke("runtime_messages", { sessionId });
    return (out && (out as any).messages) || [];
  } catch {
    return [];
  }
}

export async function directSwitchModel(
  sessionId: string,
  modelId: string,
): Promise<{ ok: boolean; error?: string; notice?: unknown }> {
  try {
    const out = await invoke("runtime_switch_model", { sessionId, modelId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true, notice: (out as any).notice };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directTruncate(
  sessionId: string,
  index: number,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const out = await invoke("runtime_truncate", { sessionId, index });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directHealth(): Promise<unknown> {
  try {
    return await invoke("health");
  } catch (e) {
    return { status: "error", error: String(e) };
  }
}

export async function directApproval(
  sessionId: string,
  decision: string,
  toolCallId?: string,
): Promise<{ ok: boolean; error?: string; toolCallId?: string }> {
  try {
    const out = await invoke("runtime_approval", { sessionId, decision, toolCallId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      toolCallId: typeof (out as any).toolCallId === "string" ? (out as any).toolCallId : undefined,
    };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

export const directSetMode = (sessionId: string, mode: string) =>
  invokeAuthority("runtime_set_mode", { sessionId, mode });
export const directDirectoryResponse = (
  sessionId: string,
  granted: boolean,
  path?: string,
  writable = false,
) => invokeAuthority("runtime_directory_response", { sessionId, granted, path, writable });
export const directPlanResponse = (
  sessionId: string,
  approved: boolean,
  mode?: string,
  feedback?: string,
) => invokeAuthority("runtime_plan_response", { sessionId, approved, mode, feedback });
export const directQuestionResponse = (sessionId: string, answer: string) =>
  invokeAuthority("runtime_question_response", { sessionId, answer });

async function invokeAuthority(command: string, args: Record<string, unknown> = {}): Promise<any> {
  try {
    return await invoke(command, args);
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

export const directGetSettings = () => invokeAuthority("settings_get");
export const directSetModelKey = (apiKey: string) =>
  invokeAuthority("settings_set_model_key", { apiKey });
export const directSetDefaultModel = (modelId: string) =>
  invokeAuthority("settings_set_default_model", { modelId });
export const directAddModel = (modelId: string) =>
  invokeAuthority("settings_add_model", { modelId });
export const directRemoveModel = (modelId: string) =>
  invokeAuthority("settings_remove_model", { modelId });
export const directSetOnboarded = (value: boolean) =>
  invokeAuthority("settings_set_onboarded", { value });
export const directSetLanguage = (language: string) =>
  invokeAuthority("settings_set_language", { language });
export const directSetContextBar = (shown: boolean) =>
  invokeAuthority("settings_set_context_bar", { shown });
export const directSetSessionsPeek = (count: number) =>
  invokeAuthority("settings_set_sessions_peek", { count });
export const directSetScratchBase = (path: string) =>
  invokeAuthority("settings_set_scratch_base", { path });
export const directSetPdfSettings = (patch: Record<string, unknown>) =>
  invokeAuthority("settings_set_pdf", { patch });
export const directSetCompactionSettings = (patch: Record<string, unknown>) =>
  invokeAuthority("settings_set_compaction", { patch });
export const directGetProviders = () => invokeAuthority("providers_list");
export const directGetProtocols = () => invokeAuthority("provider_protocols");
export const directSetProvider = (
  name: string,
  fields: Record<string, string>,
  protocol?: string,
) => invokeAuthority("provider_set", { name, fields, protocol });
export const directRemoveProvider = (name: string) =>
  invokeAuthority("provider_remove", { name });
export const directFetchProviderModels = (name: string, fields: Record<string, string>) =>
  invokeAuthority("provider_fetch_models", { name, fields });
export const directVerifyProvider = (name: string, fields: Record<string, string>) =>
  invokeAuthority("provider_verify", { name, fields });

// -----------------------------------------------------------------------------
// Runtime event subscription
// -----------------------------------------------------------------------------

/** A raw runtime event envelope as delivered by Tauri events. */
export interface WsEventEnvelope extends RuntimeEventEnvelopeV1<Record<string, unknown>> {}

/**
 * Subscribe to a session's runtime events via Tauri events.
 * Returns an unsubscribe function.
 */
export function directListenSession(
  sessionId: string,
  onEvent: (event: WsEventEnvelope) => void,
): () => void {
  let disposed = false;
  let unlisten: (() => void) | undefined;
  listen<WsEventEnvelope>(RUNTIME_EVENT_CHANNEL, (event) => {
    if (disposed) return;
    const payload = event.payload as WsEventEnvelope;
    // Filter to this session only.
    if (payload && payload.sessionId === sessionId) {
      onEvent(payload);
    }
  }).then((fn) => {
    if (disposed && fn) fn();
    else if (fn) unlisten = fn;
  });
  return () => {
    disposed = true;
    unlisten?.();
  };
}

const RUNTIME_SESSION_EVENT_TYPES = new Set<string>([
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

export { RUNTIME_SESSION_EVENT_TYPES };

// -----------------------------------------------------------------------------
// R6 Application Control Plane — Session/Workspace via direct IPC.
// Mirrors the removed FastAPI /v1/sessions + /v1/workspaces read/write shapes so
// api.ts callers can switch to IPC unchanged.
// -----------------------------------------------------------------------------

export async function directListSessions(workspace?: string): Promise<unknown> {
  try {
    return await invoke("sessions_list", workspace ? { workspace } : {});
  } catch (e) {
    return { sessions: [], error: String(e) };
  }
}

export async function directSessionMessages(sessionId: string): Promise<unknown> {
  try {
    return await invoke("session_messages", { sessionId });
  } catch (e) {
    return { messages: [], error: String(e) };
  }
}

export async function directSessionRename(
  sessionId: string,
  title: string,
): Promise<unknown> {
  try {
    return await invoke("session_rename", { sessionId, title });
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directSessionSetFlags(
  sessionId: string,
  flags: { pinned?: boolean; archived?: boolean },
): Promise<unknown> {
  try {
    return await invoke("session_set_flags", { sessionId, ...flags });
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directSessionDelete(sessionId: string): Promise<unknown> {
  try {
    return await invoke("session_delete", { sessionId });
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directRecentWorkspaces(): Promise<unknown> {
  try {
    return await invoke("workspaces_recent");
  } catch (e) {
    return { workspaces: [], error: String(e) };
  }
}

export function directListenApp(
  onEvent: (event: WsEventEnvelope) => void,
): () => void {
  let disposed = false;
  let unlisten: (() => void) | undefined;
  listen<WsEventEnvelope>(RUNTIME_EVENT_CHANNEL, (event) => {
    if (disposed) return;
    const payload = event.payload as WsEventEnvelope;
    if (payload && payload.sessionId === null) onEvent(payload);
  }).then((fn) => {
    if (disposed && fn) fn();
    else if (fn) unlisten = fn;
  });
  return () => {
    disposed = true;
    unlisten?.();
  };
}

export const directPickFolder = () => invokeAuthority("pick_folder");
export const directOpenWorkspace = (path: string, create: boolean) =>
  invokeAuthority("workspace_open", { path, create });
export const directTrustedWorkspaces = () => invokeAuthority("workspaces_trusted");
export const directSetWorkspaceTrusted = (path: string, trusted: boolean) =>
  invokeAuthority("workspace_set_trusted", { path, trusted });
export const directSessionRevert = (sessionId: string, index: number) =>
  invokeAuthority("session_revert", { sessionId, index });
export const directSessionSetReasoning = (sessionId: string, effort: string) =>
  invokeAuthority("session_set_reasoning", { sessionId, effort });
export const directSessionRoots = (sessionId: string) =>
  invokeAuthority("session_roots", { sessionId });
export const directSessionAddRoot = (sessionId: string, path: string, writable: boolean) =>
  invokeAuthority("session_add_root", { sessionId, path, writable });
export const directSessionRemoveRoot = (sessionId: string, path: string) =>
  invokeAuthority("session_remove_root", { sessionId, path });
export const directGetUnattended = (sessionId: string) =>
  invokeAuthority("session_get_unattended", { sessionId });
export const directSetUnattended = (sessionId: string, unattended: boolean) =>
  invokeAuthority("session_set_unattended", { sessionId, unattended });
export const directListInbox = (sessionId?: string, itemState?: string) =>
  invokeAuthority("inbox_list", { sessionId, itemState });
export const directResolveInbox = (id: string, resolution: string) =>
  invokeAuthority("inbox_resolve", { id, resolution });
export const directListArtifacts = (sessionId: string) =>
  invokeAuthority("artifacts_list", { sessionId });
export const directReadArtifact = (sessionId: string, path: string) =>
  invokeAuthority("artifact_read", { sessionId, path });
export const directResolveArtifactPath = (sessionId: string, path: string) =>
  invokeAuthority("artifact_resolve_path", { sessionId, path });
export const directListMemory = () => invokeAuthority("memory_list");
export const directUpdateMemory = (id: number, content: string) =>
  invokeAuthority("memory_update", { id, content });
export const directDeleteMemory = (id: number) =>
  invokeAuthority("memory_delete", { id });
export const directDeleteAllMemory = () => invokeAuthority("memory_delete_all");
export const directGetMemorySettings = () => invokeAuthority("memory_settings");
export const directSetMemorySettings = (patch: Record<string, unknown>) =>
  invokeAuthority("memory_set_settings", { patch });
export const directListAutomations = () => invokeAuthority("automations_list");
export const directCreateAutomation = (payload: Record<string, unknown>) =>
  invokeAuthority("automation_create", { payload });
export const directGetAutomation = (id: string) =>
  invokeAuthority("automation_get", { id });
export const directUpdateAutomation = (id: string, changes: Record<string, unknown>) =>
  invokeAuthority("automation_update", { id, changes });
export const directDeleteAutomation = (id: string) =>
  invokeAuthority("automation_delete", { id });
export const directMarkAutomationSeen = (id: string) =>
  invokeAuthority("automation_mark_seen", { id });
export const directPrepareAutomationRun = (id: string) =>
  invokeAuthority("automation_prepare_run", { id });
export const directFinalizeAutomationRun = (id: string, runId: string) =>
  invokeAuthority("automation_finalize_run", { id, runId });
export const directListMcp = () => invokeAuthority("mcp_list");
export const directPutMcp = (name: string, config: Record<string, unknown>) =>
  invokeAuthority("mcp_put", { name, config });
export const directPatchMcp = (name: string, changes: Record<string, unknown>) =>
  invokeAuthority("mcp_patch", { name, changes });
export const directDeleteMcp = (name: string) => invokeAuthority("mcp_delete", { name });
export const directMcpTools = (name: string) => invokeAuthority("mcp_tools", { name });
export const directReloadMcp = () => invokeAuthority("mcp_reload");
export const directConnectMcp = (name: string) => invokeAuthority("mcp_connect", { name });
export const directSignoutMcp = (name: string) => invokeAuthority("mcp_signout", { name });
export const directListAudit = (params: Record<string, unknown>) =>
  invokeAuthority("audit_list", params);
export const directListSkills = (workspace?: string) =>
  invokeAuthority("skills_list", { workspace });
export const directCreateSkill = (body: Record<string, unknown>) =>
  invokeAuthority("skill_create", { body });
export const directUpdateSkill = (name: string, patch: Record<string, unknown>) =>
  invokeAuthority("skill_update", { name, patch });
export const directDeleteSkill = (name: string, workspace?: string) =>
  invokeAuthority("skill_delete", { name, workspace });
export const directMoveSkill = (name: string, scope: string, workspace?: string) =>
  invokeAuthority("skill_move", { name, scope, workspace });
export const directResolveSkillFolder = (name: string, workspace?: string) =>
  invokeAuthority("skill_resolve_folder", { name, workspace });
export const directStageSkillUpload = (dataB64: string, filename: string) =>
  invokeAuthority("skill_stage_upload", { dataB64, filename });
export const directConfirmSkillUpload = (token: string, scope: string, workspace?: string) =>
  invokeAuthority("skill_confirm_upload", { token, scope, workspace });
export const directSessionSkills = (sessionId: string, workspace?: string) =>
  invokeAuthority("session_skills", { sessionId, workspace });
export const directSetSessionSkill = (
  sessionId: string,
  skill: string,
  enabled: boolean,
  clear: boolean,
  workspace?: string,
) => invokeAuthority("session_set_skill", { sessionId, skill, enabled, clear, workspace });
export const directListConnectors = () => invokeAuthority("connectors_list");
export const directConnectConnector = (name: string, fields: Record<string, string>) =>
  invokeAuthority("connector_connect", { name, fields });
export const directDisconnectConnector = (name: string) =>
  invokeAuthority("connector_disconnect", { name });
export const directUpdateConnectorTools = (name: string, enabled: Record<string, boolean>) =>
  invokeAuthority("connector_update_tools", { name, enabled });
export const directConnectorAction = (name: string, action: string, payload: unknown = {}) =>
  invokeAuthority("connector_action", { name, action, payload });
export const directSessionConnections = (sessionId: string) =>
  invokeAuthority("session_connections", { sessionId });
export const directSetSessionConnection = (
  sessionId: string,
  connector: string,
  enabled: boolean,
  clear: boolean,
) => invokeAuthority("session_set_connection", { sessionId, connector, enabled, clear });
export const directListSubscriptions = () => invokeAuthority("subscriptions_list");
export const directAddSubscription = (sessionId: string, channel: string) =>
  invokeAuthority("subscription_add", { sessionId, channel });
export const directRemoveSubscription = (sessionId: string, channel: string) =>
  invokeAuthority("subscription_remove", { sessionId, channel });
export const directListInboxRouting = () => invokeAuthority("inbox_routing_list");
export const directSetInboxRouting = (name: string, channel: string | null, target: string) =>
  invokeAuthority("inbox_routing_set", { name, channel, target });
export const directListUnrouted = () => invokeAuthority("unrouted_list");
export const directRecentChannels = () => invokeAuthority("recent_channels");
export const directGetDmRoute = () => invokeAuthority("dm_route_get");
export const directSetDmRoute = (sessionId: string) =>
  invokeAuthority("dm_route_set", { sessionId });
export const directBrowserState = () => invokeAuthority("browser_state");
export const directBrowserScreenshot = () => invokeAuthority("browser_screenshot");
export const directBrowserClose = () => invokeAuthority("browser_close");
