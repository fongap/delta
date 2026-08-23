/** Stable UI-facing DTOs. Provider and agent implementation fields do not belong here. */

export interface RuntimeErrorEnvelope {
  code: string;
  message: string;
  details: Record<string, unknown>;
  retriable: boolean;
}

export interface RuntimeEventEnvelopeV1<TPayload extends Record<string, unknown> = Record<string, unknown>> {
  type: string;
  version: 1;
  /** Session streams always provide an id; session-independent app events use null. */
  sessionId: string | null;
  sequence: number;
  payload: TPayload;
}

export interface SessionDto {
  session_id: string;
  title?: string;
  workspace: string;
  agent: string;
  model: string;
  mode: string;
  updated_at: string | null;
  messages: number;
  pinned?: boolean;
  archived?: boolean;
  reasoning_effort?: string;
  attention?: number;
  liveness?: "working" | "sleeping" | "idle";
  subscriptions?: string[];
  origin?: string;
  origin_label?: string;
}

export interface MessageSourceDto {
  connector: string;
  kind: "channel" | "dm";
  channel_id: string;
  channel_name: string;
  sender_id: string;
  sender_name: string;
  ts: number;
  text: string;
}

export interface MessageDto {
  role: string;
  // Message payloads are role-specific and remain open during the first contract slice.
  content?: any;
  tool_calls?: any[];
  tool_call_id?: string;
  source?: MessageSourceDto;
  usage?: {
    model?: string | null;
    input: number;
    output: number;
    cache_read: number;
    cache_write: number;
  };
  [key: string]: any;
}

export interface ApprovalDto {
  name: string;
  arguments: Record<string, unknown>;
  reason: string;
  category: string;
  standing_target: string;
}

export interface ArtifactDto {
  path: string;
  abs_path?: string;
  name: string;
  kind: string;
  size: number;
  modified_at: number;
}

export interface ModelDto {
  id: string;
  provider: string;
  label?: string;
  available: boolean;
  custom_provider: boolean;
}

export class RuntimeContractError extends Error {}

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new RuntimeContractError("expected an object");
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string") throw new RuntimeContractError(`missing required field: ${field}`);
  return value;
}

function requiredNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new RuntimeContractError(`missing required field: ${field}`);
  }
  return value;
}

function requiredNonNegativeNumber(value: unknown, field: string): number {
  const number = requiredNumber(value, field);
  if (number < 0) throw new RuntimeContractError(`invalid field: ${field}`);
  return number;
}

const optionalString = (value: unknown): string | undefined =>
  typeof value === "string" ? value : undefined;
const optionalBoolean = (value: unknown, defaultValue: boolean): boolean =>
  typeof value === "boolean" ? value : defaultValue;
const optionalRecord = (value: unknown): Record<string, unknown> | undefined =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;

export function parseSessionDto(value: unknown): SessionDto {
  const input = record(value);
  const liveness = input.liveness;
  return {
    session_id: requiredString(input.session_id, "session_id"),
    workspace: requiredString(input.workspace, "workspace"),
    agent: requiredString(input.agent, "agent"),
    model: requiredString(input.model, "model"),
    mode: requiredString(input.mode, "mode"),
    updated_at: typeof input.updated_at === "string" ? input.updated_at : null,
    messages: typeof input.messages === "number" && input.messages >= 0 ? input.messages : 0,
    ...(optionalString(input.title) ? { title: input.title as string } : {}),
    pinned: optionalBoolean(input.pinned, false),
    archived: optionalBoolean(input.archived, false),
    reasoning_effort: optionalString(input.reasoning_effort) ?? "auto",
    attention: typeof input.attention === "number" && input.attention >= 0 ? input.attention : 0,
    liveness:
      liveness === "working" || liveness === "sleeping" || liveness === "idle"
        ? liveness
        : "idle",
    subscriptions: Array.isArray(input.subscriptions)
      ? input.subscriptions.filter((item): item is string => typeof item === "string")
      : [],
    ...(optionalString(input.origin) ? { origin: input.origin as string } : {}),
    ...(optionalString(input.origin_label) ? { origin_label: input.origin_label as string } : {}),
  };
}

export function parseMessageDto(value: unknown): MessageDto {
  const input = record(value);
  const source = optionalRecord(input.source);
  const usage = optionalRecord(input.usage);
  return {
    role: requiredString(input.role, "role"),
    ...(input.content !== undefined ? { content: input.content } : {}),
    ...(Array.isArray(input.tool_calls) ? { tool_calls: input.tool_calls } : {}),
    ...(optionalString(input.tool_call_id) ? { tool_call_id: input.tool_call_id as string } : {}),
    ...(source
      ? {
          source: {
            connector: requiredString(source.connector, "source.connector"),
            kind: source.kind === "dm" ? "dm" : "channel",
            channel_id: requiredString(source.channel_id, "source.channel_id"),
            channel_name: requiredString(source.channel_name, "source.channel_name"),
            sender_id: requiredString(source.sender_id, "source.sender_id"),
            sender_name: requiredString(source.sender_name, "source.sender_name"),
            ts: requiredNumber(source.ts, "source.ts"),
            text: requiredString(source.text, "source.text"),
          },
        }
      : {}),
    ...(usage
      ? {
          usage: {
            ...(optionalString(usage.model) ? { model: usage.model as string } : {}),
            input: requiredNonNegativeNumber(usage.input, "usage.input"),
            output: requiredNonNegativeNumber(usage.output, "usage.output"),
            cache_read: requiredNonNegativeNumber(usage.cache_read, "usage.cache_read"),
            cache_write: requiredNonNegativeNumber(usage.cache_write, "usage.cache_write"),
          },
        }
      : {}),
  };
}

export function parseApprovalDto(value: unknown): ApprovalDto {
  const input = record(value);
  return {
    name: requiredString(input.name, "name"),
    arguments:
      input.arguments !== null && typeof input.arguments === "object" && !Array.isArray(input.arguments)
        ? (input.arguments as Record<string, unknown>)
        : {},
    reason: optionalString(input.reason) ?? "",
    category: optionalString(input.category) ?? "",
    standing_target: optionalString(input.standing_target) ?? "",
  };
}

export function parseArtifactDto(value: unknown): ArtifactDto {
  const input = record(value);
  return {
    path: requiredString(input.path, "path"),
    name: requiredString(input.name, "name"),
    kind: requiredString(input.kind, "kind"),
    size: requiredNonNegativeNumber(input.size, "size"),
    modified_at: requiredNumber(input.modified_at, "modified_at"),
    ...(optionalString(input.abs_path) ? { abs_path: input.abs_path as string } : {}),
  };
}

export function parseModelDto(value: unknown): ModelDto {
  const input = record(value);
  return {
    id: requiredString(input.id, "id"),
    provider: requiredString(input.provider, "provider"),
    ...(optionalString(input.label) ? { label: input.label as string } : {}),
    available: optionalBoolean(input.available, true),
    custom_provider: optionalBoolean(input.custom_provider, false),
  };
}
