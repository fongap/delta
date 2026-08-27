import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  createCustomProvider,
  fetchModels,
  getProtocols,
  getProviders,
  removeCustomProvider as deleteCustomProvider,
  removeProvider,
  setDefaultModel,
  setProvider,
  verifyProvider,
  type ProviderField as ProviderFieldT,
  type ProviderInfo,
  type ProviderProtocol,
} from "../api";
import { openExternal } from "../tauri";
import { useI18n } from "../i18n/I18nContext";
import { PROVIDER_LOGOS, providerRank } from "./logos";

// The provider gallery ⇄ key form, shared by Onboarding step 1 (§39) and
// Settings ▸ Models (UX-021) so the two can never drift apart visually. The hook
// owns the interaction state machine; ProviderCards/ProviderForm own the shared
// markup. Each surface keeps its own frame (fixed-height modal vs scrolling page)
// and passes a testid prefix so both stay independently addressable in e2e.

// Where a non-developer gets an API key — deep link + one line of instructions.
export const KEY_HELP: Record<string, { url: string; label: string }> = {
  anthropic: { url: "https://console.anthropic.com/settings/keys", label: "console.anthropic.com" },
  openai: { url: "https://platform.openai.com/api-keys", label: "platform.openai.com" },
  gemini: { url: "https://aistudio.google.com/apikey", label: "aistudio.google.com" },
  openrouter: { url: "https://openrouter.ai/keys", label: "openrouter.ai" },
  bedrock: { url: "https://console.aws.amazon.com/bedrock/home#/api-keys", label: "the AWS Bedrock console" },
  fireworks: { url: "https://fireworks.ai/account/api-keys", label: "fireworks.ai" },
  together: { url: "https://api.together.xyz/settings/api-keys", label: "together.xyz" },
  zai: { url: "https://z.ai/manage-apikey/apikey-list", label: "z.ai" },
  kimi: { url: "https://platform.moonshot.ai/console/api-keys", label: "platform.moonshot.ai" },
  deepseek: { url: "https://platform.deepseek.com/api_keys", label: "platform.deepseek.com" },
  mistral: { url: "https://console.mistral.ai/api-keys", label: "console.mistral.ai" },
  qwen: { url: "https://modelstudio.console.alibabacloud.com", label: "alibabacloud.com" },
  minimax: { url: "https://platform.minimax.io", label: "platform.minimax.io" },
  xai: { url: "https://console.x.ai", label: "console.x.ai" },
};

export type Verify = { state: "idle" | "testing" | "ok" | "error"; msg?: string };

// §7 H2: the server's test-failure strings are machine-readable diagnostics
// (reason/code never localized); map the known shapes to localized UI text
// here so the user-facing message is Chinese/English while the wire value
// stays untouched. Unknown messages pass through verbatim.
export function localizeVerifyMsg(msg: string | undefined, t: (k: string, v?: Record<string, string>) => string) {
  if (!msg) return "";
  let m: RegExpMatchArray | null;
  if ((m = msg.match(/^Couldn't reach (.+) \(([^)]+)\)\.$/))) return t("providers.cantReach", { name: m[1], err: m[2] });
  if (msg === "Invalid API key.") return t("providers.invalidKey");
  if (msg === "Server rejected the request.") return t("providers.serverRejected");
  if (msg === "Enter an API key to test.") return t("providers.enterKeyToTest");
  if (msg === "Reached the server, but no OpenAI-compatible /v1 API there.") return t("providers.noOpenAiApi");
  if ((m = msg.match(/^(.+) returned HTTP (\d+)\.$/))) return t("providers.httpError", { name: m[1], code: m[2] });
  if ((m = msg.match(/^unknown provider: (.+)$/))) return t("providers.unknownProvider", { name: m[1] });
  if ((m = msg.match(/^provider already exists: (.+)$/))) return t("providers.providerExists", { name: m[1] });
  if (msg === "Invalid provider alias.") return t("providers.invalidAlias");
  if (msg === "unreachable" || msg === "couldn't verify") return t("providers.unreachable");
  return msg;
}

type Translate = (key: string, vars?: Record<string, string | number>, fallback?: string) => string;
// Section header style — mirrors ManageTabs' SEC_H (kept local to avoid a circular import).
const SEC_H = "text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";
const FIELD_KEYS: Record<string, string> = {
  base_url: "serverAddress",
  region: "awsRegion",
  auth_method: "authMethod",
  bedrock_api_key: "bedrockApiKey",
  aws_profile: "awsProfile",
  aws_access_key_id: "accessKeyId",
  aws_secret_access_key: "secretAccessKey",
  project: "gcpProject",
  location: "location",
  service_account_json: "serviceAccountJson",
  vertex_api_key: "vertexApiKey",
};
const fieldLabel = (field: ProviderFieldT, t: Translate) => {
  if (field.key === "api_key") {
    return t(
      field.required ? "providers.fields.apiKey" : "providers.fields.apiKeyOptional",
      undefined,
      field.label,
    );
  }
  const key = FIELD_KEYS[field.key];
  return key ? t(`providers.fields.${key}`, undefined, field.label) : field.label;
};
const fieldHelp = (field: ProviderFieldT, t: Translate) => {
  if (field.key === "api_key" && field.help) {
    return t("providers.fields.apiKeyHelp", undefined, field.help);
  }
  const key = FIELD_KEYS[field.key];
  return field.help && key ? t(`providers.fields.${key}Help`, undefined, field.help) : field.help;
};
const choiceLabel = (value: string, fallback: string, t: Translate) =>
  t(`providers.auth.${value}`, undefined, fallback);
const choiceDesc = (value: string, fallback: string, t: Translate) =>
  t(`providers.auth.${value}Desc`, undefined, fallback);

/** Brand chip: always a light plate so multicolor marks read on any theme. */
export function ProviderMark({ name, title, size = 32 }: { name: string; title: string; size?: number }) {
  const url = PROVIDER_LOGOS[name];
  return (
    <span
      className="rounded-lg border border-line grid place-items-center shrink-0"
      style={{ width: size, height: size, background: "#f6f7f8" }}
    >
      {url ? (
        <img src={url} alt="" style={{ width: size * 0.6, height: size * 0.6 }} />
      ) : (
        <span className="text-[13px] font-semibold text-muted">{title[0]}</span>
      )}
    </span>
  );
}

/** "2h ago"-style label for a provider's last completion (null when never used). */
export function relTime(epoch?: number | null, t?: (key: string, vars?: Record<string, string | number>, fallback?: string) => string): string | null {
  if (!epoch) return null;
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (secs < 90) return t ? t("providers.justNow", undefined, "just now") : "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return t ? t("providers.minutesAgo", { n: mins }, `${mins}m ago`) : `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return t ? t("providers.hoursAgo", { n: hrs }, `${hrs}h ago`) : `${hrs}h ago`;
  return t ? t("providers.daysAgo", { n: Math.floor(hrs / 24) }, `${Math.floor(hrs / 24)}d ago`) : `${Math.floor(hrs / 24)}d ago`;
}

export interface ProviderSetupState {
  providers: ProviderInfo[];
  ordered: ProviderInfo[];
  customProviders: ProviderInfo[];
  orderedCustom: ProviderInfo[];
  refreshProviders: () => Promise<void>;
  sel: string | null;
  info: ProviderInfo | undefined;
  fields: Record<string, string>;
  setFieldValue: (key: string, value: string) => void;
  dirty: boolean;
  verify: Verify;
  showEndpoint: boolean;
  setShowEndpoint: (v: boolean) => void;
  keylessOk: Set<string>;
  credentialed: boolean;
  savedState: boolean;
  secretFilled: boolean;
  openProvider: (name: string) => void;
  backToGallery: () => void;
  runTestAndSave: () => Promise<boolean>;
  removeKey: () => Promise<void>;
  removeCustom: () => Promise<void>;
  cancelBackTimer: () => void;
  statusFor: (p: ProviderInfo, opts?: { lastUsed?: boolean }) => ReactNode;
  // Blur-save for non-secret fields on an already-configured provider (the Test button is
  // the KEY's save path; extras like anthropic's thinking_budget must not need a re-test —
  // owner-hit 2026-07-23: the budget silently never saved).
  saveField: (key: string) => Promise<void>;
  fieldSaved: string | null; // field key flashing "✓ Saved"

  // -- custom-config-first (F1+F2) -------------------------------------------------
  // The 7 protocol definitions (openai-compatible, openai, anthropic, gemini, ollama,
  // bedrock, vertex) loaded from /v1/protocols; the create form's protocol dropdown reads
  // these to render its dynamic field table.
  protocols: ProviderProtocol[];
  // True while the "Add custom provider" form is open (no alias saved yet). The gallery's
  // surfaces render the create form in place of ProviderCards when this is set.
  creating: boolean;
  // The alias the user is typing for a not-yet-created custom provider.
  alias: string;
  setAlias: (v: string) => void;
  // The selected protocol_id for the create form; defaults to "openai-compatible".
  protoId: string;
  setProtoId: (id: string) => void;
  // The resolved protocol definition for `protoId` (undefined until /v1/protocols loads).
  protoDef: ProviderProtocol | undefined;
  // Async state of the /v1/protocols fetch: loading / done / failed. The create form uses
  // this so the API-key field + dropdown don't silently render empty when the fetch hasn't
  // resolved (the "no API Key input box" + "no default protocol" symptom).
  protocolsLoading: boolean;
  protocolsErr: string | null;
  // Localized version of protocolsErr, for rendering (null when protocolsErr is null).
  protocolErrorMessage: string | null;
  // Open the create form (resets alias/protocol/fields). Closing returns to the gallery.
  openNewCustom: () => void;
  // Create & save: register the alias, persist its fields, then run a live verify. Stays
  // on the form when verify fails — a custom provider is first-class the moment it's named,
  // so a failing test is not a reason to discard it. Returns true on verify success.
  runCustomCreate: () => Promise<boolean>;
  // Fetch the provider's model list and auto-add each id as `alias:{id}` (按前缀自动加入).
  fetchCustomModels: () => Promise<void>;
  fetching: boolean;
  fetchMsg: { state: "ok" | "error"; text: string } | null;
  fetchedModels: string[];
  pickFetchedDefault: (bareId: string) => Promise<void>;
}

export function useProviderSetup(opts?: { onSaved?: () => void }): ProviderSetupState {
  const { t } = useI18n();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  // null = the gallery; a provider name = that provider's key form.
  const [sel, setSel] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [showEndpoint, setShowEndpoint] = useState(false);
  const [verify, setVerify] = useState<Verify>({ state: "idle" });
  // Keyless providers (Ollama) report configured without proving anything runs —
  // a passing Detect this session is what marks them live.
  const [keylessOk, setKeylessOk] = useState<Set<string>>(new Set());
  // Unsaved per-provider input survives switching cards (owner complaint 2026-07-16).
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({});
  const backTimer = useRef<number | null>(null);
  // Which non-secret field just blur-saved (flashes "✓ Saved" in the input).
  const [fieldSaved, setFieldSaved] = useState<string | null>(null);
  const fieldSavedTimer = useRef<number | null>(null);

  // -- custom-config-first (F1+F2) ------------------------------------------------
  const [protocols, setProtocols] = useState<ProviderProtocol[]>([]);
  // Protocol list arrives async from the backend; the create form must not render its
  // empty pre-fetch state (no API-key field, no dropdown options, "no default protocol").
  // Track load explicitly so a slow/failed fetch shows a real status instead of a silent
  // empty form (the .catch would otherwise swallow the error forever).
  const [protocolsLoading, setProtocolsLoading] = useState(true);
  const [protocolsErr, setProtocolsErr] = useState<string | null>(null);
  const loadProtocols = () => {
    setProtocolsLoading(true);
    setProtocolsErr(null);
    getProtocols()
      .then((p) => {
        setProtocols(p || []);
        setProtocolsLoading(false);
      })
      .catch(() => {
        setProtocols([]);
        setProtocolsLoading(false);
        setProtocolsErr("providers.protocolsLoadFailed");
      });
  };
  const [creating, setCreating] = useState(false);
  const [alias, setAlias] = useState("");
  const [protoId, setProtoIdState] = useState("openai-compatible");
  const [fetching, setFetching] = useState(false);
  const [fetchedModels, setFetchedModels] = useState<string[]>([]);
  const [fetchMsg, setFetchMsg] = useState<{ state: "ok" | "error"; text: string } | null>(null);

  const refreshProviders = () =>
    getProviders()
      .then(setProviders)
      .catch(() => {});
  useEffect(() => {
    refreshProviders();
    loadProtocols();
    return () => {
      if (backTimer.current) window.clearTimeout(backTimer.current);
    };
  }, []);

  const info = providers.find((p) => p.name === sel);
  const credentialed = !!info?.configured && !!info?.needs_key;

  // The protocol currently being authored in the create form (fields follow its def).
  const protoDef = protocols.find((p) => p.id === protoId);

  // Reset the create form to a fresh protocol's field defaults whenever the dropdown
  // changes (so switching OpenAI-compatible → Anthropic doesn't carry stale fields over).
  const setProtoId = (id: string) => {
    setProtoIdState(id);
    const def = protocols.find((p) => p.id === id);
    const next: Record<string, string> = {};
    for (const f of def?.fields || []) next[f.key] = f.default || "";
    setFields(next);
    setDirty(false);
    setVerify({ state: "idle" });
    setFetchedModels([]);
    setFetchMsg(null);
  };

  const resetCreateForm = () => {
    setAlias("");
    const def = protocols.find((p) => p.id === protoId);
    const next: Record<string, string> = {};
    for (const f of def?.fields || []) next[f.key] = f.default || "";
    setFields(next);
    setDirty(false);
    setVerify({ state: "idle" });
    setFetchedModels([]);
    setFetchMsg(null);
  };

  // Open the "add custom provider" form (the gallery's create path). Closes any open
  // provider edit first so the two never share a live `sel`.
  const openNewCustom = () => {
    if (sel) setDrafts((d) => ({ ...d, [sel]: dirty ? fields : {} }));
    setSel(null);
    setCreating(true);
    setAlias("");
    setProtoIdState("openai-compatible");
    const def = protocols.find((p) => p.id === "openai-compatible");
    const next: Record<string, string> = {};
    for (const f of def?.fields || []) next[f.key] = f.default || "";
    setFields(next);
    setDirty(false);
    setVerify({ state: "idle" });
    setFetchedModels([]);
    setFetchMsg(null);
  };

  const openProvider = (name: string) => {
    const p = providers.find((x) => x.name === name);
    if (sel) setDrafts((d) => ({ ...d, [sel]: fields }));
    setCreating(false);
    const draft = drafts[name];
    const next: Record<string, string> = {};
    for (const f of p?.fields || []) next[f.key] = draft?.[f.key] || p?.values?.[f.key] || f.default || "";
    setSel(name);
    setFields(next);
    setDirty(!!draft && Object.values(draft).some(Boolean));
    setVerify({ state: "idle" });
    setFetchedModels([]);
    setFetchMsg(null);
    setShowEndpoint(false);
  };

  const backToGallery = () => {
    // Stash only UNSAVED input. The unconditional stash used to capture the just-saved
    // key on the post-Test auto-return, so revisiting a connected provider restored the
    // plaintext key into the field instead of the masked placeholder + saved pill
    // (state-restore bug, owner catch 2026-07-19). A clean form clears any stale draft.
    if (sel) setDrafts((d) => ({ ...d, [sel]: dirty ? fields : {} }));
    setSel(null);
    setCreating(false);
    resetCreateForm();
  };

  // Test = verify AND save AND return (§39: a passing Test auto-saves and takes
  // you back to the gallery, where the card now wears its ✓ — no extra clicks).
  const runTestAndSave = async (): Promise<boolean> => {
    if (!sel) return false;
    setVerify({ state: "testing" });
    const res = await verifyProvider(sel, fields).catch(() => ({ ok: false, error: "unreachable" }));
    if (!res.ok) {
      setVerify({ state: "error", msg: res.error || "couldn't verify" });
      return false;
    }
    if (dirty || !info?.configured) await setProvider(sel, fields).catch(() => {});
    if (!info?.needs_key) setKeylessOk((s) => new Set(s).add(sel));
    setVerify({ state: "ok" });
    setDirty(false);
    setDrafts((d) => ({ ...d, [sel]: {} }));
    await refreshProviders();
    opts?.onSaved?.();
    // Let the in-field "✓ Tested & saved" register, then slide home. NOT backToGallery:
    // the timeout would fire its stale closure (dirty/fields from before the save) and
    // re-stash the just-saved key as a draft — the state-restore bug (owner catch
    // 2026-07-19). This return path clears the draft unconditionally.
    backTimer.current = window.setTimeout(() => {
      setDrafts((d) => ({ ...d, [sel]: {} }));
      setSel(null);
      resetCreateForm();
    }, 900);
    return true;
  };

  // Create & save for a brand-new custom alias. Ordering matters: verifyProvider needs
  // get_descriptor(alias) to exist, so the alias MUST be registered (and its fields
  // persisted) first, then verified live. A failing verify doesn't discard the alias —
  // custom providers are first-class the moment they're named, so we land on its edit
  // form with the entered values still in place and the error message shown.
  const runCustomCreate = async (): Promise<boolean> => {
    const aliasTrim = alias.trim();
    if (!aliasTrim || !protoId || !protoDef) return false;
    setVerify({ state: "testing" });
    const create = await createCustomProvider(aliasTrim, protoId, fields)
      .catch(() => ({ ok: false, error: "unreachable" }));
    // Idempotent: a prior Fetch in create mode already registered the alias (and stored its
    // key), so a second create returns "provider already exists". Treat that as success and
    // fall through to verify — the alias is real, so verify resolves it. Without this, the
    // post-Fetch Create & save dead-ends on an error for work the Fetch already completed.
    const alreadyExists = !create.ok && /^provider already exists:/.test(create.error || "");
    if (!create.ok && !alreadyExists) {
      setVerify({ state: "error", msg: create.error || "couldn't verify" });
      return false;
    }
    const res = await verifyProvider(aliasTrim, fields)
      .catch(() => ({ ok: false, error: "unreachable" }));
    await refreshProviders();
    if (!res.ok) {
      setVerify({ state: "error", msg: res.error || "couldn't verify" });
      setCreating(false);
      setSel(aliasTrim);
      return false;
    }
    if (!protoDef.needs_key) setKeylessOk((s) => new Set(s).add(aliasTrim));
    setVerify({ state: "ok" });
    setDirty(false);
    setCreating(false);
    setSel(null);
    resetCreateForm();
    setDrafts((d) => ({ ...d, [aliasTrim]: {} }));
    opts?.onSaved?.();
    return true;
  };

  // Fetch a provider's model list and auto-add each id as `alias:{id}` (按前缀自动加入).
  // Works from the edit form (sel set) or the create form (alias still being authored) —
  // in create mode the alias is registered first since fetchModels resolves its descriptor.
  const fetchCustomModels = async (): Promise<void> => {
    const name = (sel ?? alias.trim()).trim();
    if (!name) return;
    setFetching(true);
    setFetchMsg(null);
    if (!sel && !providers.some((p) => p.name === name && p.custom)) {
      const c = await createCustomProvider(name, protoId, fields)
        .catch(() => ({ ok: false, error: "unreachable" }));
      if (!c.ok) {
        setFetching(false);
        setFetchMsg({ state: "error", text: localizeVerifyMsg(c.error || "couldn't verify", t) });
        return;
      }
    }
    const res = await fetchModels(name, fields).catch(
      (): { ok: false; error: string; added?: string[]; models?: string[] } => ({
        ok: false,
        error: "unreachable",
      }),
    );
    setFetching(false);
    if (!res.ok) {
      setFetchedModels([]);
      setFetchMsg({ state: "error", text: localizeVerifyMsg(res.error || "couldn't verify", t) });
      return;
    }
    setFetchedModels(res.models ?? []);
    const n = res.added?.length ?? 0;
    // Create mode: Fetch already registered the alias AND stored its key (the registration
    // POST carries the fields), so creation is complete. Keep the form OPEN with the success
    // message + fetched-model chips so the user can pick a default model, then close it
    // explicitly when done. Resetting here used to wipe fetchedModels/fetchMsg the instant
    // they were set (the model list was lost before it could render); runCustomCreate is
    // idempotent for "already exists" so the post-Fetch Create & save never dead-ends.
    setFetchMsg({
      state: "ok",
      text: n > 0
        ? t("providers.fetchOk", { n }, `Fetched ${n} model${n === 1 ? "" : "s"}`)
        : t("providers.fetchOkNone", undefined, "Models already up to date"),
    });
    await refreshProviders();
    opts?.onSaved?.();
  };

  // One-click default from the fetched list: the chip id is bare; the pool entry carries
  // the alias prefix ("alias:model").
  const pickFetchedDefault = async (bareId: string): Promise<void> => {
    const name = (sel ?? alias.trim()).trim();
    if (!name) return;
    const r = await setDefaultModel(`${name}:${bareId}`).catch(() => ({
      ok: false,
      error: "unreachable",
    }));
    setFetchMsg(
      r.ok
        ? {
            state: "ok",
            text: t(
              "providers.defaultSet",
              { m: `${name}:${bareId}` },
              `Default model set to ${name}:${bareId}`,
            ),
          }
        : { state: "error", text: localizeVerifyMsg(r.error || "couldn't save", t) },
    );
  };

  // Blur-save for non-secret fields when the provider is already configured: extras like
  // anthropic's thinking_budget must persist without a key re-test (owner-hit 2026-07-23 —
  // typed, left Settings, silently never saved). Secrets keep the explicit Test-to-save
  // contract; unconfigured providers save everything on their first Test.
  const saveField = async (key: string) => {
    if (!sel || !info?.configured) return;
    const spec = info.fields.find((f) => f.key === key);
    if (!spec || spec.secret) return;
    const current = (fields[key] || "").trim();
    const stored = (info.values?.[key] || "").trim();
    if (current === stored) return;
    const res = await setProvider(sel, { [key]: current }).catch(() => ({ ok: false }));
    if (!res.ok) return;
    await refreshProviders();
    opts?.onSaved?.();
    setFieldSaved(key);
    if (fieldSavedTimer.current) window.clearTimeout(fieldSavedTimer.current);
    fieldSavedTimer.current = window.setTimeout(() => setFieldSaved(null), 1400);
  };

  // Settings-only: forget the stored key; the card reverts to "Not set up".
  const removeKey = async () => {
    if (!sel) return;
    await removeProvider(sel).catch(() => {});
    setDrafts((d) => ({ ...d, [sel]: {} }));
    setKeylessOk((s) => {
      const next = new Set(s);
      next.delete(sel);
      return next;
    });
    await refreshProviders();
    opts?.onSaved?.();
    setSel(null);
    resetCreateForm();
  };

  const removeCustom = async () => {
    if (!sel || !info?.custom) return;
    const removed = await deleteCustomProvider(sel).catch(() => ({ ok: false }));
    if (!removed.ok) return;
    setDrafts((current) => ({ ...current, [sel]: {} }));
    setSel(null);
    resetCreateForm();
    await refreshProviders();
    opts?.onSaved?.();
  };

  const statusFor = (p: ProviderInfo, o?: { lastUsed?: boolean }) => {
    if (p.custom) {
      const verifiedDraft = p.name === alias.trim() && fetchedModels.length > 0;
      return (
        <span className={"block text-[11.5px] font-medium truncate " + (verifiedDraft ? "text-accent" : "text-ok")}>
          {verifiedDraft
            ? t("providers.verifiedPendingSave", undefined, "Verified · save to finish")
            : `✓ ${t("providers.saved", undefined, "Saved")}`}
        </span>
      );
    }
    if (p.configured && p.needs_key) {
      const used = o?.lastUsed ? relTime(p.last_used_at, t) : null;
      return (
        <span className="block text-[11.5px] text-ok font-medium truncate">
          ✓ {t("common.connected", undefined, "Connected")}
          {used ? <span className="text-muted font-normal"> · {t("providers.usedAgo", { n: used }, `used ${used}`)}</span> : ""}
        </span>
      );
    }
    if (!p.needs_key)
      return (
        <span className="block text-[11.5px] text-faint truncate">
          {keylessOk.has(p.name) ? (
            <span className="text-ok font-medium">✓ {t("providers.running", undefined, "Running")}</span>
          ) : (
            t("providers.noKeyNeeded", undefined, "No key needed")
          )}
        </span>
      );
    return <span className="block text-[11.5px] text-faint truncate">{t("providers.notSetUp", undefined, "Not set up")}</span>;
  };

  const customProviders = providers.filter((p) => p.custom);

  const protocolErrorMessage = protocolsErr ? t(protocolsErr, undefined, "Couldn't load protocols") : null;
  return {
    providers,
    ordered: [...providers].sort((a, b) => providerRank(a.name) - providerRank(b.name)),
    customProviders,
    orderedCustom: [...customProviders].sort((a, b) => providerRank(a.name) - providerRank(b.name)),
    refreshProviders,
    sel,
    info,
    fields,
    setFieldValue: (key, value) => {
      setFields((cur) => ({ ...cur, [key]: value }));
      setDirty(true);
      setVerify({ state: "idle" });
    },
    dirty,
    verify,
    showEndpoint,
    setShowEndpoint,
    keylessOk,
    credentialed,
    // The in-field saved state (§39): green border + pill INSIDE the key box — shown
    // for stored credentials and fresh test-passes alike; typing clears it.
    savedState: (credentialed && !dirty) || verify.state === "ok",
    // Only REQUIRED secrets gate the Test button — cloud providers (Bedrock, Vertex)
    // have optional key fields whose credentials may live in ~/.aws or ADC instead.
    secretFilled: (info?.fields || []).every(
      (f) => !f.secret || !f.required || (fields[f.key] || "").trim(),
    ),
    openProvider,
    backToGallery,
    runTestAndSave,
    removeKey,
    removeCustom,
    saveField,
    fieldSaved,
    cancelBackTimer: () => {
      if (backTimer.current) window.clearTimeout(backTimer.current);
    },
    statusFor,
    // -- custom-config-first -------------------------------------------------------
    protocols,
    creating,
    alias,
    setAlias,
    protoId,
    setProtoId,
    protoDef,
    openNewCustom,
    runCustomCreate,
    fetchCustomModels,
    fetching,
    fetchMsg,
    fetchedModels,
    pickFetchedDefault,
    protocolsLoading,
    protocolsErr,
    protocolErrorMessage,
  };
}

/** The gallery: a card per configured/built-in provider, plus the entry point into the
 * custom-config-first create form (alias + protocol). */
export function ProviderCards({
  ps,
  tp,
  gridClass = "grid grid-cols-2 gap-2.5",
  lastUsed = false,
  hideAdd = false,
  customOnly = false,
}: {
  ps: ProviderSetupState;
  tp: string; // testid prefix ("ob" onboarding, "set" settings)
  gridClass?: string;
  lastUsed?: boolean;
  hideAdd?: boolean;
  customOnly?: boolean;
}) {
  const { t } = useI18n();
  const card =
    "flex items-center gap-2.5 rounded-xl border border-line bg-panel px-3 py-2.5 text-left hover:border-lineStrong transition-colors";
  const list = customOnly ? ps.orderedCustom : ps.ordered;
  return (
    <div className={gridClass}>
      {!hideAdd && (
        <button
          className={card + " border-dashed text-muted hover:text-ink"}
          onClick={ps.openNewCustom}
          data-testid={`${tp}-provider-add`}
        >
          <span
            className="rounded-lg border border-line grid place-items-center shrink-0"
            style={{ width: 32, height: 32, background: "#f6f7f8" }}
          >
            <span className="text-[16px] font-semibold text-muted">＋</span>
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[13px] font-semibold leading-tight truncate">
              {t("providers.addCustomProvider", undefined, "Add custom provider")}
            </span>
            <span className="block text-[11.5px] text-faint truncate">
              {t("providers.customProviderHint", undefined, "Any OpenAI-compatible · or native protocol")}
            </span>
          </span>
          <span className="text-faint text-[14px]">＋</span>
        </button>
      )}
      {list.map((p) => (
        <button
          key={p.name}
          className={card}
          data-testid={`${tp}-provider-${p.name}`}
          onClick={() => ps.openProvider(p.name)}
        >
          <ProviderMark name={p.name} title={p.title} />
          <span className="min-w-0 flex-1">
            <span className="block text-[13px] font-semibold leading-tight truncate">
              {p.custom ? p.alias || p.name : p.title}
            </span>
            {p.custom ? (
              <span className="block text-[11.5px] text-faint truncate">
                {t(`providers.protocols.${p.protocol}`, undefined, p.blurb || p.protocol || "")}
                {" · "}
                {p.configured
                  ? t("providers.saved", undefined, "Saved")
                  : p.name === ps.alias.trim() && ps.fetchedModels.length
                    ? t("providers.verifiedPendingSave", undefined, "Verified · save to finish")
                    : t("providers.pendingSave", undefined, "Not saved")}
              </span>
            ) : (
              ps.statusFor(p, { lastUsed })
            )}
          </span>
          <span className="text-faint text-[14px]">›</span>
        </button>
      ))}
    </div>
  );
}

/** One provider's key form: crumb, brand head, fields (endpoint behind a quiet
 * disclosure), in-field saved pill, Test/Detect, key help, fixed error line.
 * `footer` renders after the error line (Settings adds "Remove key…" there). */
export function ProviderForm({
  ps,
  tp,
  footer,
}: {
  ps: ProviderSetupState;
  tp: string;
  footer?: ReactNode;
}) {
  const { t } = useI18n();
  const { info, sel } = ps;
  const [showSecret, setShowSecret] = useState(false);
  const label = "block text-[12.5px] font-medium text-muted mt-3 mb-1";
  const input =
    "w-full px-3 py-2 rounded-lg border bg-panel text-[13.5px] outline-none focus:border-accent";
  const fieldsAll = info?.fields || [];
  // Cloud providers declare a segmented auth-method choice; the selected method's
  // credential fields render inside a panel with its own Test & save footer.
  const choice = fieldsAll.find((f) => f.choices && f.choices.length);
  const method = choice ? ps.fields[choice.key] || choice.default || "" : "";
  const selected = choice?.choices?.find((c) => c.value === method);
  const methodFields = choice
    ? fieldsAll.filter(
        (f) =>
          f.show_when &&
          Object.entries(f.show_when).every(([k, v]) => (ps.fields[k] || "") === v),
      )
    : [];
  if (ps.creating) return <CustomCreateForm ps={ps} tp={tp} />;
  if (!sel) return null;

  const fieldRow = (f: ProviderFieldT) => (
    <div key={f.key} className="mb-3 last:mb-0">
      <label className={label}>{fieldLabel(f, t)}</label>
      <div className="relative">
        <input
          className={input + " border-line" + (f.secret ? " pr-10" : "")}
          type={f.secret && !showSecret ? "password" : "text"}
          placeholder={f.secret && ps.credentialed && !ps.dirty ? "••••••••" : f.placeholder}
          value={ps.fields[f.key] || ""}
          data-testid={`${tp}-field-${f.key}`}
          onChange={(e) => ps.setFieldValue(f.key, e.target.value)}
          onBlur={f.secret ? undefined : () => void ps.saveField(f.key)}
        />
        {f.secret && (ps.fields[f.key] || ps.credentialed) && (
          <button
            type="button"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-[13px] text-muted hover:text-ink"
            onClick={() => setShowSecret((s) => !s)}
            aria-label={showSecret ? t("providers.hideKey", undefined, "Hide key") : t("providers.showKey", undefined, "Show key")}
            data-testid={`${tp}-toggle-secret`}
          >
            {showSecret ? "🙈" : "👁"}
          </button>
        )}
      </div>
      {ps.fieldSaved === f.key && (
        <span
          className="text-[11.5px] text-ok mt-0.5 block"
          data-testid={`${tp}-field-saved-${f.key}`}
        >
          {t("common.saved", undefined, "Saved")}
        </span>
      )}
      {f.help && <p className="text-[11.5px] text-faint mt-1">{fieldHelp(f, t)}</p>}
    </div>
  );

  return (
    <div className="max-w-[620px]">
      <button className="text-[12.5px] text-muted hover:text-ink" onClick={ps.backToGallery} data-testid={`${tp}-back`}>
        ‹ {t("providers.allProviders", undefined, "All providers")}
      </button>
      <div className="flex items-center gap-3 mt-3 mb-1">
        <ProviderMark name={info?.name || ""} title={info?.title || ""} size={36} />
        <span className="min-w-0">
          <span className="block text-[15px] font-semibold leading-tight">
            {info?.custom ? info.alias || info.name : info?.title}
          </span>
          {info?.custom && (
            <span className="block text-[11.5px] text-faint truncate">
              {t(`providers.protocols.${info.protocol}`, undefined, info.blurb || info.protocol || "")}
            </span>
          )}
        </span>
      </div>
      {info?.blurb && !info.custom && (
        <p className="text-[11.5px] text-faint mt-1">
          {t("providers." + info.name + ".blurb", undefined, info.blurb)}
        </p>
      )}

      <div className={"mt-5 mb-2 " + SEC_H}>{t("providers.sectionBasic", undefined, "Basic settings")}</div>

      <div className="rounded-xl border border-line bg-panel p-4">
      {/* Identity is fixed at creation: the alias IS the model routing prefix. Rendered
          read-only under its honest name (路由标识) — "服务名称" implied it was editable. */}
      {info?.custom && (
        <>
          <div className="mb-3">
            <label className={label}>{t("providers.routeId", undefined, "Routing prefix")}</label>
            <input
              className={input + " border-line opacity-60"}
              value={info.alias || info.name}
              readOnly
              data-testid={`${tp}-name`}
            />
            <p className="text-[11.5px] text-faint mt-1">
              {t(
                "providers.routeIdNote",
                { alias: info.alias || info.name },
                "Fixed after creation. This provider's models are named \"{alias}:<model>\".",
              )}
            </p>
          </div>
          <div className="mb-3">
            <label className={label}>{t("providers.apiProtocol", undefined, "API protocol")}</label>
            <input
              className={input + " border-line opacity-60"}
              value={t(`providers.protocols.${info.protocol}`, undefined, info.protocol || "")}
              readOnly
              data-testid={`${tp}-protocol-display`}
            />
          </div>
        </>
      )}

      {/* Connection fields in decision order: where to connect (base_url) before the
          credentials for it (api_key) — the address is what the user knows first. */}
      {[
        ...fieldsAll.filter((f) => f.key === "base_url"),
        ...fieldsAll.filter(
          (f) => f.key !== "base_url" && !f.secret && !f.show_when && !(f.choices && f.choices.length),
        ),
        ...fieldsAll.filter((f) => f.secret && !f.show_when && !(f.choices && f.choices.length)),
      ].map((f) => fieldRow(f))}

      {/* Auth-method segmented control + the selected method's panel (owner call
          2026-07-26): one joined track, then a soft inset card holding only that
          method's description, fields, and its own Test & save footer. */}
      {choice && (
        <div>
          <label className={label}>{fieldLabel(choice, t)}</label>
          <div
            className="inline-flex gap-0.5 rounded-[10px] border border-line bg-line/40 p-[3px]"
            role="radiogroup"
            aria-label={fieldLabel(choice, t)}
          >
            {(choice.choices || []).map((c) => {
              const active = method === c.value;
              return (
                <button
                  key={c.value}
                  role="radio"
                  aria-checked={active}
                  className={
                    "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] whitespace-nowrap transition-colors " +
                    (active
                      ? "bg-panel text-ink font-medium shadow-sm ring-1 ring-line"
                      : "text-muted hover:text-ink")
                  }
                  data-testid={`${tp}-choice-${choice.key}-${c.value}`}
                  onClick={() => ps.setFieldValue(choice.key, c.value)}
                >
                  {info?.custom ? choiceLabel(c.value, c.label, t) : c.label}
                  {c.tag && (
                    <span className="text-[9.5px] font-semibold uppercase tracking-wide text-accent bg-accentSoft rounded-full px-1.5 py-px">
                      {t(`providers.auth.${c.value}Tag`, undefined, c.tag)}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="mt-2.5 rounded-xl border border-line bg-paper/60 px-4 pb-3.5 pt-3">
            {selected?.desc && <p className="text-[12px] text-muted">{info?.custom ? choiceDesc(selected.value, selected.desc, t) : selected.desc}</p>}
            {selected?.command && (
              <button
                className="mt-2.5 inline-flex items-center gap-2 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[12px] font-mono text-ink hover:border-lineStrong"
                onClick={() => void navigator.clipboard?.writeText(selected.command || "")}
                title={t("common.copyCommand", undefined, "Copy command")}
                data-testid={`${tp}-cmd-copy`}
              >
                {selected.command}
                <span className="font-sans text-[11px] text-faint">⧉</span>
              </button>
            )}
            {methodFields.map((f) => fieldRow(f))}
          </div>
        </div>
      )}

      {info?.needs_key && KEY_HELP[sel] && (
        <p className="text-[11.5px] text-faint mt-2">
          {t("providers.noKeyYet", undefined, "No key yet? ")}
          <button
            className="text-muted underline decoration-line underline-offset-2 hover:text-ink"
            onClick={() => openExternal(KEY_HELP[sel].url)}
          >
            {t("providers.createOneAt", { n: KEY_HELP[sel].label }, `Create one at ${KEY_HELP[sel].label} ↗`)}
          </button>{" "}
          {t("providers.takesAboutAMinute", undefined, "— takes about a minute.")}
        </p>
      )}
      {/* Ollama-specific hint ONLY for the ollama protocol — a keyless openai-compatible
          custom provider (LM Studio/vLLM…) must not see "Install Ollama" copy. */}
      {info && !info.needs_key && (info as { protocol?: string }).protocol === "ollama" && (
        <p className="text-[11.5px] text-faint mt-2">
          {t("providers.noApiKeyNeeded", undefined, "No API key needed — Ollama runs models on this computer. ")}
          <button
            className="text-muted underline decoration-line underline-offset-2 hover:text-ink"
            onClick={() => openExternal("https://ollama.com/download")}
          >
            {t("providers.installOllama", undefined, "Install Ollama ↗")}
          </button>
        </p>
      )}

      {/* Test connection: one secondary action covering the WHOLE provider config
          (protocol + base_url + key), with an explicit status line — ● Connected /
          ● Failed + the server's reason. Replaces the old per-field inline Test and
          the permanent "✓ saved" pill. */}
      <div className="mt-4 flex items-center gap-3" data-testid={`${tp}-test-connection`}>
        <button
          className="btn-secondary shrink-0"
          onClick={() => ps.runTestAndSave()}
          disabled={
            ps.verify.state === "testing" ||
            (info?.needs_key && !ps.secretFilled && !ps.credentialed)
          }
          data-testid={`${tp}-test`}
        >
          {ps.verify.state === "testing" ? "…" : t("providers.testConnection", undefined, "Test connection")}
        </button>
        {ps.verify.state === "error" ? (
          <span className="flex items-start gap-1.5 text-[12.5px] text-warnInk min-w-0" data-testid={`${tp}-conn-status`}>
            <span className="mt-[6px] h-[7px] w-[7px] rounded-full bg-warnInk shrink-0" />
            <span className="min-w-0">
              {t("providers.connFail", undefined, "Connection failed")}
              {ps.verify.msg ? (
                <span className="block text-[11.5px] text-faint truncate">
                  {localizeVerifyMsg(ps.verify.msg, t)}
                </span>
              ) : null}
            </span>
          </span>
        ) : ps.verify.state === "ok" || (info?.configured && info.needs_key) || (!info?.needs_key && sel != null && ps.keylessOk.has(sel)) ? (
          <span className="flex items-center gap-1.5 text-[12.5px] text-ok" data-testid={`${tp}-conn-status`}>
            <span className="h-[7px] w-[7px] rounded-full bg-ok" />
            {t("providers.connOk", undefined, "Connected")}
          </span>
        ) : null}
      </div>
      </div>
      {footer}
    </div>
  );
}

/**
 * The custom-config-first create form (F1+F2): alias + protocol dropdown (default
 * "OpenAI compatible") + the selected protocol's dynamic field table, then
 * "Create & save" (register → verify) and "Fetch models" (auto-add `alias:{id}`).
 * Rendered by ProviderForm when `ps.creating` is true; distinct from the built-in
 * edit path because there is no `sel`/`info` to prefill from yet.
 */
/** The fetched model list as clickable chips — click sets that model as the default.
    Shown after a successful Fetch in both the create and the edit form. */
export function FetchedModelChips({ ps }: { ps: ProviderSetupState }) {
  const { t } = useI18n();
  if (!ps.fetchedModels.length) return null;
  return (
    <div className="mt-2.5" data-testid="fetched-models">
      <div className="text-[11.5px] text-faint mb-1.5">
        {t("providers.fetchedList", { n: ps.fetchedModels.length }, `${ps.fetchedModels.length} available — click one to make it the default:`)}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {ps.fetchedModels.map((m) => (
          <button
            key={m}
            className="px-2 py-1 rounded-md border border-line bg-panel text-[12px] font-mono text-ink hover:border-accent hover:text-accent text-left"
            onClick={() => void ps.pickFetchedDefault(m)}
          >
            {m}
          </button>
        ))}
      </div>
    </div>
  );
}

export function CustomCreateForm({ ps, tp, inline = false }: { ps: ProviderSetupState; tp: string; inline?: boolean }) {
  const { t } = useI18n();
  const proto = ps.protoDef;
  // ~10–15% tighter than the edit path's rhythm (mt-2.5 between fields, py-1.5 inputs)
  // so the four-step form reads compact without feeling crowded.
  const label = "block text-[12.5px] font-medium text-muted mt-2.5 mb-1";
  const input = "w-full px-3 py-1.5 rounded-lg border bg-panel text-[13.5px] font-normal outline-none focus:border-accent";
  const fieldsAll = proto?.fields || [];
  const choice = fieldsAll.find((f) => f.choices && f.choices.length);
  const method = choice ? ps.fields[choice.key] || choice.default || "" : "";
  const selected = choice?.choices?.find((c) => c.value === method);
  const methodFields = choice
    ? fieldsAll.filter(
        (f) => f.show_when && Object.entries(f.show_when).every(([k, v]) => (ps.fields[k] || "") === v),
      )
    : [];
  const createReady =
    !!ps.alias.trim() &&
    fieldsAll.every((f) => !f.secret || !f.required || (ps.fields[f.key] || "").trim());
  const busy = ps.verify.state === "testing" || ps.fetching;

  // A leaner row than the edit path's fieldRow: no inline Test button (that path needs a
  // registered `sel`), no saved-pill (nothing is saved until Create & save).
  const row = (f: ProviderFieldT) => (
    <div key={f.key}>
      <label className={label}>{fieldLabel(f, t)}</label>
      <input
        className={input + " border-line"}
        type={f.secret ? "password" : "text"}
        placeholder={f.placeholder}
        value={ps.fields[f.key] || ""}
        data-testid={`${tp}-field-${f.key}`}
        onChange={(e) => ps.setFieldValue(f.key, e.target.value)}
      />
      {f.help && <p className="text-[11.5px] text-faint mt-1">{fieldHelp(f, t)}</p>}
    </div>
  );

  return (
    <div className="max-w-[620px]">
      {!inline && (
        <button className="text-[12.5px] text-muted hover:text-ink" onClick={ps.backToGallery} data-testid={`${tp}-back`}>
          ‹ {t("providers.allProviders", undefined, "All providers")}
        </button>
      )}
      {/* The top of the form always carries the provider's live alias name so the
          provider is identifiable while editing; falls back to the generic
          "Add custom provider" title until an alias is typed. */}
      <div className={"flex items-center gap-3 " + (inline ? "mb-1" : "mt-3 mb-1")}>
        <ProviderMark name={proto?.id || "custom"} title={proto?.title || "Custom"} size={36} />
        <span className="min-w-0">
          <span
            className="block text-[14px] font-semibold leading-[20px] truncate"
            data-testid={`${tp}-custom-title`}
          >
            {ps.alias.trim() || t("providers.addCustomProvider", undefined, "Add provider")}
          </span>
          {proto && (
            <span className="block text-[13px] font-normal text-faint truncate">
              {t("providers.customProviderFormSub", undefined, "Configure an OpenAI-compatible or native-protocol service")}
            </span>
          )}
        </span>
      </div>

      {/* While /v1/protocols hasn't resolved, show real status instead of silently rendering
          an empty form (no API-key field, no protocol options → "no API Key input box" +
          "no default protocol"). The .catch in useProviderSetup surfaces load failures here. */}
      {ps.protocolsLoading && (
        <div className="text-[13px] text-muted mt-4" data-testid={`${tp}-protocols-loading`}>
          {t("providers.loadingProtocols", undefined, "Loading protocols…")}
        </div>
      )}
      {ps.protocolsErr && ps.protocolErrorMessage && (
        <div
          className="text-[13px] text-warnInk mt-4"
          data-testid={`${tp}-protocols-error`}
        >
          {ps.protocolErrorMessage}
        </div>
      )}
      {!ps.protocolsLoading && !ps.protocolsErr && !proto && !inline && (
        <div className="text-[13px] text-muted mt-4">{t("providers.noProtocols", undefined, "No protocols available.")}</div>
      )}

      {/* The alias is the routing prefix — `alias:{model}` is how its models are named.
          The rule lives HERE as field help (it's the one place the user needs it). */}
      <div>
        <label className={label}>{t("providers.alias", undefined, "Alias")}</label>
        <input
          className={input + " border-line"}
          type="text"
          placeholder={t("providers.aliasHint", undefined, "e.g. my-gateway")}
          value={ps.alias}
          data-testid={`${tp}-alias`}
          onChange={(e) => ps.setAlias(e.target.value)}
        />
        <p className="text-[11.5px] text-faint mt-1">
          {t("providers.prefixAutoNote", { alias: ps.alias.trim() || "alias" }, "Models are auto-added with the “{alias}:” prefix.")}
        </p>
      </div>

      <div>
        <label className={label}>{t("providers.protocol", undefined, "Protocol")}</label>
        <select
          className={input + " border-line form-select"}
          value={ps.protoId}
          data-testid={`${tp}-protocol`}
          onChange={(e) => ps.setProtoId(e.target.value)}
        >
          {ps.protocols.map((p) => (
            <option key={p.id} value={p.id}>
              {t("providers.protocols." + p.id, undefined, p.title)}
            </option>
          ))}
        </select>
      </div>

      {/* Decision order mirrors the edit path: where to connect (base_url) before the
          credentials for it (api_key) — the address is what the user knows first. */}
      {[
        ...fieldsAll.filter((f) => f.key === "base_url"),
        ...fieldsAll.filter(
          (f) => f.key !== "base_url" && !f.secret && !f.show_when && !(f.choices && f.choices.length),
        ),
        ...fieldsAll.filter((f) => f.secret && !f.show_when && !(f.choices && f.choices.length)),
      ].map((f) => row(f))}

      {/* Fallback API Key + Base URL: when the protocol's field definitions haven't
          loaded yet (or the endpoint failed), still show the two most common fields
          so the user can start typing. The default openai-compatible protocol always
          needs an API key + base URL; rendering them as a fallback ensures the form
          is never empty. Only shows when no secret field was already rendered above. */}
      {fieldsAll.filter((f) => f.secret).length === 0 && !ps.protocolsLoading && (
        <>
          <div>
            <label className={label}>
              {t("providers.baseUrl", undefined, "Base URL")}
            </label>
            <input
              className={input + " border-line"}
              type="text"
              placeholder="https://api.openai.com/v1"
              value={ps.fields["base_url"] || ""}
              data-testid={`${tp}-field-base_url-fallback`}
              onChange={(e) => ps.setFieldValue("base_url", e.target.value)}
            />
          </div>
          <div>
            <label className={label}>
              {t("providers.apiKey", undefined, "API Key")}
            </label>
            <input
              className={input + " border-line"}
              type="password"
              placeholder="sk-…"
              value={ps.fields["api_key"] || ""}
              data-testid={`${tp}-field-api_key-fallback`}
              onChange={(e) => ps.setFieldValue("api_key", e.target.value)}
            />
          </div>
        </>
      )}

      {/* Auth-method segmented control (bedrock/vertex protocols) — same control as the
          edit path, but the panel's footer is the create action instead of Test & save. */}
      {choice && (
        <div>
          <label className={label}>{fieldLabel(choice, t)}</label>
          <div
            className="inline-flex gap-0.5 rounded-[10px] border border-line bg-line/40 p-[3px]"
            role="radiogroup"
            aria-label={fieldLabel(choice, t)}
          >
            {(choice.choices || []).map((c) => {
              const active = method === c.value;
              return (
                <button
                  key={c.value}
                  role="radio"
                  aria-checked={active}
                  className={
                    "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] whitespace-nowrap transition-colors " +
                    (active
                      ? "bg-panel text-ink font-medium shadow-sm ring-1 ring-line"
                      : "text-muted hover:text-ink")
                  }
                  data-testid={`${tp}-choice-${choice.key}-${c.value}`}
                  onClick={() => ps.setFieldValue(choice.key, c.value)}
                >
                  {choiceLabel(c.value, c.label, t)}
                  {c.tag && (
                    <span className="text-[9.5px] font-semibold uppercase tracking-wide text-accent bg-accentSoft rounded-full px-1.5 py-px">
                      {t(`providers.auth.${c.value}Tag`, undefined, c.tag)}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="mt-2.5 rounded-xl border border-line bg-paper/60 px-4 pb-3.5 pt-3">
            {selected?.desc && <p className="text-[12px] text-muted">{choiceDesc(selected.value, selected.desc, t)}</p>}
            {methodFields.map((f) => row(f))}
          </div>
        </div>
      )}

      {/* Full footer: plain-language note on top, actions right-aligned below —
          Fetch models is the secondary action (pull from the provider), Create & save
          the primary. Status / error / success messages render directly below. */}
      <div className="mt-4 border-t border-line pt-3" data-testid={`${tp}-create-footer`}>
        <p className="text-[11.5px] text-faint">
          {t("providers.checkThenSaves", undefined, "Runs one read-only check, then saves.")}
        </p>
        <div className="mt-2.5 flex items-center justify-end gap-2">
          <button
            className="btn-secondary shrink-0"
            onClick={() => void ps.fetchCustomModels()}
            disabled={busy || !ps.alias.trim()}
            data-testid={`${tp}-fetch`}
          >
            {ps.fetching ? "…" : t("providers.fetchModels", undefined, "Fetch models")}
          </button>
          <button
            className="btn-primary shrink-0"
            onClick={() => void ps.runCustomCreate()}
            disabled={busy || !createReady}
            data-testid={`${tp}-create-save`}
          >
            {ps.verify.state === "testing" ? "…" : t("providers.createAndSave", undefined, "Create & save")}
          </button>
        </div>
      </div>

      {ps.fetchMsg && (
        <p
          className={"mt-2 text-[12px] " + (ps.fetchMsg.state === "ok" ? "text-ok" : "text-warnInk")}
          data-testid={`${tp}-fetch-msg`}
        >
          {ps.fetchMsg.text}
        </p>
      )}
      <FetchedModelChips ps={ps} />
      <div className="mt-3 min-h-[19px] text-[12.5px]">
        {ps.verify.state === "error" && <span className="text-warnInk">{localizeVerifyMsg(ps.verify.msg, t)}</span>}
      </div>
    </div>
  );
}
