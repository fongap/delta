import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  createCustomProvider,
  fetchModels,
  getProtocols,
  getProviders,
  removeProvider,
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
    if (!create.ok) {
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
      (): { ok: false; error: string; added?: string[] } => ({ ok: false, error: "unreachable" }),
    );
    setFetching(false);
    if (!res.ok) {
      setFetchMsg({ state: "error", text: localizeVerifyMsg(res.error || "couldn't verify", t) });
      return;
    }
    const n = res.added?.length ?? 0;
    setFetchMsg({
      state: "ok",
      text: n > 0
        ? t("providers.fetchOk", { n }, `Fetched ${n} model${n === 1 ? "" : "s"}`)
        : t("providers.fetchOkNone", undefined, "Models already up to date"),
    });
    await refreshProviders();
    opts?.onSaved?.();
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

  const statusFor = (p: ProviderInfo, o?: { lastUsed?: boolean }) => {
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
            <span className="block text-[13px] font-semibold leading-tight truncate">{p.title}</span>
            {ps.statusFor(p, { lastUsed })}
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
  const label = "block text-[12px] text-muted mt-3 mb-1";
  const input =
    "w-full px-3 py-2 rounded-lg border bg-panel text-[13.5px] outline-none focus:border-accent";
  const fieldsAll = info?.fields || [];
  const keyed = fieldsAll.some((x) => x.secret);
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
  // Without a choice control, Test lives next to the required secret (the API key), or
  // the first field for keyless providers (Ollama's Detect).
  const requiredSecret = fieldsAll.find((x) => x.secret && x.required);
  const testKey = requiredSecret ? requiredSecret.key : fieldsAll[0]?.key;
  if (ps.creating) return <CustomCreateForm ps={ps} tp={tp} />;
  if (!sel) return null;

  const fieldRow = (f: ProviderFieldT, testable: boolean) => (
    <div key={f.key}>
      <label className={label}>{f.label}</label>
      <div className="flex gap-2">
        <div className="relative flex-1 min-w-0">
          <input
            className={input + (ps.savedState && testable ? " border-ok pr-32" : " border-line")}
            type={f.secret ? "password" : "text"}
            placeholder={f.secret && ps.credentialed && !ps.dirty ? "••••••••" : f.placeholder}
            value={ps.fields[f.key] || ""}
            data-testid={`${tp}-field-${f.key}`}
            onChange={(e) => ps.setFieldValue(f.key, e.target.value)}
            onBlur={f.secret ? undefined : () => void ps.saveField(f.key)}
          />
          {ps.fieldSaved === f.key && (
            <span
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[11px] font-medium text-ok bg-okSoft rounded-full px-2 py-0.5 pointer-events-none"
              data-testid={`${tp}-field-saved-${f.key}`}
            >
              ✓ {t("common.saved", undefined, "Saved")}
            </span>
          )}
          {/* §39: state lives IN the field — no status lines below. */}
          {ps.savedState && testable && (
            <span
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[11px] font-medium text-ok bg-okSoft rounded-full px-2 py-0.5 pointer-events-none"
              data-testid={`${tp}-saved-pill`}
            >
              {info?.needs_key ? (
                <>✓ {t("providers.testedAndSaved", undefined, "Tested & saved")}</>
              ) : (
                <>✓ {t("providers.detected", undefined, "Detected")}</>
              )}
            </span>
          )}
        </div>
        {testable && (
          <button
            className="px-4 rounded-lg border border-line text-[13px] font-medium text-ink hover:border-lineStrong shrink-0 disabled:opacity-40"
            onClick={() => ps.runTestAndSave()}
            disabled={ps.verify.state === "testing" || (!ps.secretFilled && !ps.credentialed)}
            data-testid={`${tp}-test`}
          >
            {ps.verify.state === "testing" ? "…" : info?.needs_key ? t("common.test", undefined, "Test") : t("providers.detect", undefined, "Detect")}
          </button>
        )}
      </div>
      {f.help && <p className="text-[11.5px] text-faint mt-1">{f.help}</p>}
    </div>
  );

  return (
    <div>
      <button className="text-[12.5px] text-muted hover:text-ink" onClick={ps.backToGallery} data-testid={`${tp}-back`}>
        ‹ {t("providers.allProviders", undefined, "All providers")}
      </button>
      <div className="flex items-center gap-3 mt-3 mb-1">
        <ProviderMark name={info?.name || ""} title={info?.title || ""} size={36} />
        <span className="min-w-0">
          <span className="block text-[15px] font-semibold leading-tight">{info?.title}</span>
          {info ? ps.statusFor(info) : null}
        </span>
      </div>
      {info?.blurb && (
        <p className="text-[11.5px] text-faint mt-1">
          {t("providers." + info.name + ".blurb", undefined, info.blurb)}
        </p>
      )}

      {fieldsAll
        .filter(
          (f) =>
            !f.show_when &&
            !(f.choices && f.choices.length) &&
            !(f.key === "base_url" && keyed),
        )
        .map((f) => fieldRow(f, !choice && f.key === testKey))}

      {/* Auth-method segmented control + the selected method's panel (owner call
          2026-07-26): one joined track, then a soft inset card holding only that
          method's description, fields, and its own Test & save footer. */}
      {choice && (
        <div>
          <label className={label}>{choice.label}</label>
          <div
            className="inline-flex gap-0.5 rounded-[10px] border border-line bg-line/40 p-[3px]"
            role="radiogroup"
            aria-label={choice.label}
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
                  {c.label}
                  {c.tag && (
                    <span className="text-[9.5px] font-semibold uppercase tracking-wide text-accent bg-accentSoft rounded-full px-1.5 py-px">
                      {c.tag}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="mt-2.5 rounded-xl border border-line bg-paper/60 px-4 pb-3.5 pt-3">
            {selected?.desc && <p className="text-[12px] text-muted">{selected.desc}</p>}
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
            {methodFields.map((f) => fieldRow(f, false))}
            <div className="mt-3.5 flex items-center justify-between gap-3 border-t border-line pt-3">
              {ps.savedState ? (
                <span className="text-[11.5px] font-medium text-ok" data-testid={`${tp}-saved-pill`}>
                  ✓ {t("providers.testedAndSaved", undefined, "Tested & saved")}
                </span>
              ) : (
                <span className="text-[11.5px] text-faint">{t("providers.checkThenSaves", undefined, "Runs one read-only check, then saves.")}</span>
              )}
              <button
                className="shrink-0 rounded-lg border border-accent bg-accent px-4 py-1.5 text-[13px] font-medium text-onAccent hover:brightness-105 disabled:opacity-40"
                onClick={() => ps.runTestAndSave()}
                disabled={ps.verify.state === "testing"}
                data-testid={`${tp}-test`}
              >
                {ps.verify.state === "testing" ? "…" : <>✓ {t("providers.testAndSave", undefined, "Test & save")}</>}
              </button>
            </div>
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
      {info && !info.needs_key && (
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

      {/* Custom endpoint (keyed providers only): a quiet disclosure BELOW the key help,
          with enough separation to read as its own advanced row — no explainer copy
          (owner calls 2026-07-18 + 2026-07-19). */}
      {(() => {
        const keyed = (info?.fields || []).some((x) => x.secret);
        const ep = keyed ? (info?.fields || []).find((f) => f.key === "base_url") : undefined;
        if (!ep) return null;
        if (!ps.showEndpoint)
          return (
            <button
              className="block self-start text-[12.5px] text-muted hover:text-ink mt-4"
              onClick={() => ps.setShowEndpoint(true)}
              data-testid={`${tp}-endpoint-link`}
            >
              {t("providers.customEndpoint", undefined, "Custom endpoint ⌄")}
            </button>
          );
        return (
          <div className="mt-4">
            <label className={label}>{ep.label}</label>
            <div className="relative">
              <input
                className={input + " border-line"}
                type="text"
                placeholder={ep.placeholder}
                value={ps.fields[ep.key] || ""}
                data-testid={`${tp}-field-${ep.key}`}
                onChange={(e) => ps.setFieldValue(ep.key, e.target.value)}
                onBlur={() => void ps.saveField(ep.key)}
              />
              {ps.fieldSaved === ep.key && (
                <span
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[11px] font-medium text-ok bg-okSoft rounded-full px-2 py-0.5 pointer-events-none"
                  data-testid={`${tp}-field-saved-${ep.key}`}
                >
                  ✓ {t("common.saved", undefined, "Saved")}
                </span>
              )}
            </div>
            {ep.help && <p className="text-[11.5px] text-faint mt-1">{ep.help}</p>}
          </div>
        );
      })()}

      {/* Error line: fixed height so failures never reflow the form. */}
      <div className="mt-3 min-h-[19px] text-[12.5px]">
        {ps.verify.state === "error" && <span className="text-warnInk">{localizeVerifyMsg(ps.verify.msg, t)}</span>}
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
export function CustomCreateForm({ ps, tp, inline = false }: { ps: ProviderSetupState; tp: string; inline?: boolean }) {
  const { t } = useI18n();
  const proto = ps.protoDef;
  const label = "block text-[12px] text-muted mt-3 mb-1";
  const input = "w-full px-3 py-2 rounded-lg border bg-panel text-[13.5px] outline-none focus:border-accent";
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
      <label className={label}>{f.label}</label>
      <input
        className={input + " border-line"}
        type={f.secret ? "password" : "text"}
        placeholder={f.placeholder}
        value={ps.fields[f.key] || ""}
        data-testid={`${tp}-field-${f.key}`}
        onChange={(e) => ps.setFieldValue(f.key, e.target.value)}
      />
      {f.help && <p className="text-[11.5px] text-faint mt-1">{f.help}</p>}
    </div>
  );

  return (
    <div>
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
            className="block text-[15px] font-semibold leading-tight truncate"
            data-testid={`${tp}-custom-title`}
          >
            {ps.alias.trim() || t("providers.addCustomProvider", undefined, "Add custom provider")}
          </span>
          {proto?.blurb && <span className="block text-[11.5px] text-faint truncate">{proto.blurb}</span>}
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

      {/* The alias is the routing prefix — `alias:{model}` is how its models are named. */}
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

      {fieldsAll
        .filter((f) => !f.show_when && !(f.choices && f.choices.length))
        .map((f) => row(f))}

      {/* Auth-method segmented control (bedrock/vertex protocols) — same control as the
          edit path, but the panel's footer is the create action instead of Test & save. */}
      {choice && (
        <div>
          <label className={label}>{choice.label}</label>
          <div
            className="inline-flex gap-0.5 rounded-[10px] border border-line bg-line/40 p-[3px]"
            role="radiogroup"
            aria-label={choice.label}
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
                  {c.label}
                  {c.tag && (
                    <span className="text-[9.5px] font-semibold uppercase tracking-wide text-accent bg-accentSoft rounded-full px-1.5 py-px">
                      {c.tag}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="mt-2.5 rounded-xl border border-line bg-paper/60 px-4 pb-3.5 pt-3">
            {selected?.desc && <p className="text-[12px] text-muted">{selected.desc}</p>}
            {methodFields.map((f) => row(f))}
          </div>
        </div>
      )}

      <div className="mt-3.5 flex items-center justify-between gap-3 border-t border-line pt-3">
        <span className="text-[11.5px] text-faint">
          {t("providers.checkThenSaves", undefined, "Runs one read-only check, then saves.")}
        </span>
        <div className="flex items-center gap-2">
          <button
            className="shrink-0 rounded-lg border border-line px-4 py-1.5 text-[13px] font-medium text-ink hover:border-lineStrong disabled:opacity-40"
            onClick={() => void ps.fetchCustomModels()}
            disabled={busy || !ps.alias.trim()}
            data-testid={`${tp}-fetch`}
          >
            {ps.fetching ? "…" : t("providers.fetchModels", undefined, "Fetch models")}
          </button>
          <button
            className="shrink-0 rounded-lg border border-accent bg-accent px-4 py-1.5 text-[13px] font-medium text-onAccent hover:brightness-105 disabled:opacity-40"
            onClick={() => void ps.runCustomCreate()}
            disabled={busy || !createReady}
            data-testid={`${tp}-create-save`}
          >
            {ps.verify.state === "testing" ? "…" : <>✓ {t("providers.createAndSave", undefined, "Create & save")}</>}
          </button>
        </div>
      </div>
      <p className="text-[11.5px] text-faint mt-1">
        {t("providers.prefixAutoNote", { alias: ps.alias.trim() || "alias" }, "Models are auto-added with the “{alias}:” prefix.")}
      </p>

      {ps.fetchMsg && (
        <p
          className={"mt-2 text-[12px] " + (ps.fetchMsg.state === "ok" ? "text-ok" : "text-warnInk")}
          data-testid={`${tp}-fetch-msg`}
        >
          {ps.fetchMsg.text}
        </p>
      )}
      <div className="mt-3 min-h-[19px] text-[12.5px]">
        {ps.verify.state === "error" && <span className="text-warnInk">{localizeVerifyMsg(ps.verify.msg, t)}</span>}
      </div>
    </div>
  );
}
