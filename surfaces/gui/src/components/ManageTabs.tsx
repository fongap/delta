import { useEffect, useState } from "react";
import {
  addMcpServer,
  allowUser,
  connectConnector,
  connectManaged,
  connectMcpBacked,
  connectMcp,
  deleteMcpServer,
  disallowUser,
  getMcpServers,
  getMcpTools,
  signoutMcp,
  getSettings,
  getSubscriptions,
  resolveUnauthorized,
  unsubscribeChannel,
  patchMcpServer,
  reloadMcp,
  updateConnectorTools,
  type CloudStatus,
  type Connector,
  type Subscription,
  type McpServer,
  type ModelSettings,
} from "../api";
import { CloudSignInInline, CloudStatusPending } from "./connectors/CloudSignIn";
import { ModelChecklist } from "./ModelChecklist";
import { CustomCreateForm, ProviderCards, ProviderForm, useProviderSetup } from "../providers/ProviderSetup";
import { Toggle } from "./Toggle";
import { useI18n } from "../i18n/I18nContext";

type T = (key: string, vars?: Record<string, string | number>) => string;

// "2h ago"-style label for the providers' Last-used line (null when never used).
function relTime(t: T, epoch?: number | null): string | null {
  if (!epoch) return null;
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (secs < 90) return t("providers.justNow");
  const mins = Math.floor(secs / 60);
  if (mins < 60) return t("providers.minutesAgo", { n: mins });
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return t("providers.hoursAgo", { n: hrs });
  return t("providers.daysAgo", { n: Math.floor(hrs / 24) });
}

// Shared tab bodies for the Settings and Integrations pages (the old top-tab ManageModal was retired
// when Settings/Activity became full-page surfaces): ModelsTab → Settings ▸ Models; ConnectorsTab +
// McpTab → Integrations ▸ Connectors / MCP servers.
const SEC_H = "text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";
const CARD = "rounded-xl2 border border-line bg-panel";
const BTN_BORDERED =
  "text-[12.5px] px-3 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";
const BTN_ACCENT = "text-[12.5px] px-3 py-1.5 rounded-lg bg-accent text-onAccent shrink-0 disabled:opacity-50";
const BTN_DANGER = "text-[12.5px] text-danger/80 hover:text-danger shrink-0";

/** Two-letter initials for a chip/avatar (first+last word, else first two chars). */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

const EXAMPLE = `{
  "filesystem": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
    "enabled": true
  }
}`;

// -- Configure Models tab (UX-021: the shared provider gallery + key form) ----
// Settings ▸ Models reuses onboarding §39's ProviderCards/ProviderForm so the two
// surfaces can't drift. Settings-only extras: per-card "used Nh ago", a "Remove
// key…" affordance, and the per-provider ModelChecklist / read-only model preview
// (form view). Model setup is unified at custom provider → fetch models → pick.
export function ModelsTab() {
  const { t } = useI18n();
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const refreshSettings = () => getSettings().then(setSettings).catch(() => setSettings(null));
  const ps = useProviderSetup({ onSaved: refreshSettings });
  useEffect(() => {
    refreshSettings();
  }, []);

  if (!settings) return <div className="text-[13px] text-muted">{t("common.loading")}</div>;

  const info = ps.info;
  const knownNames = ps.providers.map((p) => p.name);

  if (ps.sel === null && !ps.creating) {
    return (
      <div>
        <div className="rounded-xl border border-line bg-panel p-4" data-testid="set-custom-card">
          <div className="text-[15px] font-semibold">{t("providers.customProviderCard")}</div>
          <p className="text-[12px] text-muted mt-0.5 mb-3 leading-relaxed">
            {t("providers.customProviderCardSub")}
          </p>
          <CustomCreateForm ps={ps} tp="set" inline />
        </div>
        {ps.orderedCustom.length > 0 && (
          <div className="mt-4">
            <div className={SEC_H + " mb-1.5"}>{t("providers.yourProviders")}</div>
            <ProviderCards ps={ps} tp="set" gridClass="grid grid-cols-2 xl:grid-cols-3 gap-2.5" lastUsed customOnly hideAdd />
          </div>
        )}
      </div>
    );
  }

  return (
    <div>
      <ProviderForm
        ps={ps}
        tp="set"
        footer={
          !ps.creating && info?.custom ? (
            <button
              className="text-[12.5px] text-danger/80 hover:text-danger hover:underline underline-offset-2"
              data-testid="set-remove-custom"
              onClick={() => {
                if (window.confirm(t("providers.removeCustomConfirm", { name: info.alias || info.name }))) void ps.removeCustom();
              }}
            >
              {t("providers.removeCustom")}
            </button>
          ) : !ps.creating && ps.credentialed ? (
            <button
              className="text-[12.5px] text-danger/80 hover:text-danger hover:underline underline-offset-2"
              data-testid="set-remove-key"
              onClick={() => {
                if (window.confirm(t("providers.removeKeyConfirm", { name: info?.title ?? "" }))) ps.removeKey();
              }}
            >
              {t("providers.removeKey")}
            </button>
          ) : null
        }
      />

      {ps.sel === "openai" && settings.source === "env" && (
        <p className="text-[12px] text-muted mt-3 leading-relaxed">
          {t("providers.envKeyNotePrefix")}
          <code>OPENAI_API_KEY</code>
          {t("providers.envKeyNoteSuffix")}
        </p>
      )}

      {ps.sel && info?.configured ? (
        <div className="mt-6">
          <div className={SEC_H + " mb-1.5"}>{t("models.title")}</div>
          <p className="text-[12px] text-muted mb-2.5 leading-relaxed">
            {t("models.pickerNote")}
          </p>
          <ModelChecklist
            provider={ps.sel}
            knownProviders={knownNames}
            suggested={info?.suggested_models || []}
            curated={settings.models}
            defaultModel={settings.model}
            labels={settings.model_labels}
            onChanged={(next) => setSettings((s) => (s ? { ...s, models: next.models, model: next.model } : s))}
          />
        </div>
      ) : (
        // Unconfigured providers still show their curated models as a read-only preview — what a
        // key unlocks is part of deciding to get one at all (owner ask, 2026-07-04).
        ps.sel &&
        (info?.suggested_models?.length || 0) > 0 && (
          <div className="mt-6" data-testid="model-preview">
            <div className={SEC_H + " mb-1.5"}>{t("models.includedModels")}</div>
            <p className="text-[12px] text-muted mb-2.5 leading-relaxed">
              {t("models.previewNote")}
            </p>
            <div className="space-y-1">
              {(info?.suggested_models || []).map((m) => {
                const full = ps.sel === "openai" ? m : `${ps.sel}:${m}`;
                return (
                  <div
                    key={m}
                    className="px-2.5 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-muted"
                    title={full}
                  >
                    {settings.model_labels?.[full] || m}
                  </div>
                );
              })}
            </div>
          </div>
        )
      )}
    </div>
  );
}

// Curated OAuth quick-adds: remote MCP servers with browser sign-in (OAuth 2.1 + DCR) —
// no keys to paste, tokens stay in the local secret store. First: Granola.
const MCP_PRESETS: { name: string; label: string; blurb: string; config: Record<string, any> }[] = [
  {
    name: "granola",
    label: "Granola",
    blurb: "connectors.mcpPresetGranola",
    config: { type: "http", url: "https://mcp.granola.ai/mcp", auth: "oauth" },
  },
];

export function McpTab() {
  const { t } = useI18n();
  const [servers, setServers] = useState<McpServer[]>([]);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => getMcpServers().then(setServers).catch(() => setServers([]));
  useEffect(() => {
    refresh();
  }, []);

  // While a browser sign-in is in flight, poll so the row flips to connected (or
  // surfaces the error) without the user having to touch anything.
  const authorizing = servers.some((s) => s.status === "authorizing");
  useEffect(() => {
    if (!authorizing) return;
    const timer = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer);
  }, [authorizing]);

  const toggle = async (s: McpServer) => {
    await patchMcpServer(s.name, { enabled: !s.enabled });
    refresh();
  };
  const remove = async (s: McpServer) => {
    await deleteMcpServer(s.name);
    refresh();
  };

  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-muted leading-relaxed">
        {t("connectors.mcpSub")}
        {t("connectors.mcpPermissionGated")}
        <button
          className="text-accent font-medium hover:underline"
          onClick={() => reloadMcp().then(refresh)}
        >
          {t("connectors.reloadNow")}
        </button>
        .
      </p>

      {servers.length === 0 && !adding ? (
        <div className={CARD + " p-4 text-[13px] text-muted"}>
          {t("connectors.noMcpServers")}
          <button className="text-accent font-medium" onClick={() => setAdding(true)}>
            {t("connectors.addServer")}
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {servers.map((s) => (
            <McpRow
              key={s.name}
              server={s}
              onToggle={() => toggle(s)}
              onRemove={() => remove(s)}
              onRefresh={refresh}
            />
          ))}
        </div>
      )}

      {/* One-click OAuth presets not yet configured. */}
      {MCP_PRESETS.filter((p) => !servers.some((s) => s.name === p.name)).map((p) => (
        <div key={p.name} className={CARD + " p-3.5 flex items-center gap-3"} data-testid={`mcp-preset-${p.name}`}>
          <div className="flex-1 min-w-0">
            <div className="text-[14px] font-medium">{p.label}</div>
            <div className="text-[11.5px] text-faint">{t(p.blurb)}</div>
          </div>
          <button
            className={BTN_ACCENT}
            onClick={async () => {
              await addMcpServer(p.name, p.config);
              await connectMcp(p.name); // opens the browser sign-in right away
              refresh();
            }}
          >
            {t("connectors.connect")}
          </button>
        </div>
      ))}

      {adding ? (
        <AddForm
          onCancel={() => {
            setAdding(false);
            setError(null);
          }}
          onError={setError}
          onAdded={() => {
            setAdding(false);
            setError(null);
            refresh();
          }}
        />
      ) : servers.length > 0 ? (
        <button className={BTN_ACCENT} onClick={() => setAdding(true)}>
          {t("connectors.addServerPlus")}
        </button>
      ) : null}
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
    </div>
  );
}

function McpRow({
  server,
  onToggle,
  onRemove,
  onRefresh,
}: {
  server: McpServer;
  onToggle: () => void;
  onRemove: () => void;
  onRefresh: () => void;
}) {
  const { t } = useI18n();
  const [tools, setTools] = useState<{ name: string; description: string }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [toolErr, setToolErr] = useState<string | null>(null);

  const isOauth = server.auth === "oauth";
  const authorizing = server.status === "authorizing";
  const signIn = async () => {
    await connectMcp(server.name); // browser opens; the tab's poll flips the status
    onRefresh();
  };
  const signOut = async () => {
    await signoutMcp(server.name);
    onRefresh();
  };

  const loadTools = async () => {
    if (tools) {
      setTools(null);
      return;
    }
    setBusy(true);
    setToolErr(null);
    const res = await getMcpTools(server.name);
    setBusy(false);
    if (res.ok) setTools(res.tools);
    else setToolErr(res.error || t("connectors.failedToConnect"));
  };

  return (
    <div className={CARD + " p-3.5"}>
      <div className="flex items-center gap-3">
        <Toggle checked={server.enabled} onChange={onToggle} title={t("connectors.enableServer")} />
        <div className="flex-1 min-w-0">
          <div className="text-[14px] font-medium">{server.name}</div>
          <div className="text-[11.5px] text-faint">
            {server.transport} · {authorizing ? t("connectors.signingIn") : server.status.replace("_", " ")}
            {server.tool_count != null ? ` · ${t("connectors.nTools", { n: server.tool_count })}` : ""}
            {server.requires_approval ? ` · ${t("connectors.asks")}` : ""}
            {isOauth ? " · oauth" : ""}
          </div>
        </div>
        {isOauth &&
          (server.status === "needs_auth" ? (
            <button className={BTN_ACCENT} onClick={signIn} data-testid={`mcp-signin-${server.name}`}>
              {t("common.signIn")}
            </button>
          ) : authorizing ? (
            <span className="text-[12px] text-muted shrink-0">{t("connectors.waitingForBrowser")}</span>
          ) : server.status === "connected" ? (
            <button
              className="text-[12px] text-muted hover:text-ink shrink-0"
              onClick={signOut}
              data-testid={`mcp-signout-${server.name}`}
            >
              {t("common.signOut")}
            </button>
          ) : null)}
        <button
          className="text-[12px] text-muted hover:text-ink shrink-0"
          onClick={loadTools}
          disabled={busy}
        >
          {busy ? "…" : tools ? t("connectors.hideTools") : t("connectors.tools")}
        </button>
        <button className={BTN_DANGER} onClick={onRemove}>
          {t("common.remove")}
        </button>
      </div>
      {server.last_error && server.status !== "connected" && (
        <div className="text-[12.5px] text-danger mt-1.5">{server.last_error}</div>
      )}
      {toolErr && <div className="text-[12.5px] text-danger mt-1.5">{toolErr}</div>}
      {tools && (
        <div className="mt-2.5 pt-2.5 border-t border-line flex flex-wrap gap-1.5">
          {tools.length === 0 && <div className="text-[12px] text-faint">{t("connectors.noTools")}</div>}
          {tools.map((tool) => (
            <span
              key={tool.name}
              title={tool.description}
              className="font-mono text-[11.5px] px-1.5 py-0.5 rounded-md bg-paper border border-line"
            >
              {tool.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function AddForm({
  onCancel,
  onAdded,
  onError,
}: {
  onCancel: () => void;
  onAdded: () => void;
  onError: (e: string | null) => void;
}) {
  const { t } = useI18n();
  const [text, setText] = useState(EXAMPLE);

  const save = async () => {
    onError(null);
    let parsed: any;
    try {
      parsed = JSON.parse(text);
    } catch (e: any) {
      onError(t("connectors.invalidJson", { message: e.message }));
      return;
    }
    // Accept either {mcpServers:{...}}, {name:{...}}, or a single bare config.
    const map = parsed.mcpServers || parsed;
    const entries =
      map && typeof map === "object" && !map.command && !map.url
        ? Object.entries(map)
        : null;
    if (!entries || entries.length === 0) {
      onError(t("connectors.pasteServerJson"));
      return;
    }
    for (const [name, config] of entries) {
      await addMcpServer(name, config as Record<string, any>);
    }
    onAdded();
  };

  return (
    <div className="space-y-2">
      <div className="text-[12.5px] text-muted">{t("connectors.pasteMcpJson")}</div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        rows={9}
        className="w-full font-mono text-[12px] px-3 py-2.5 rounded-lg border border-line bg-paper text-ink outline-none focus:border-accent resize-y"
      />
      <div className="flex items-center gap-3">
        <button className={BTN_ACCENT} onClick={save}>
          {t("common.add")}
        </button>
        <button className="text-[12.5px] text-muted hover:text-ink" onClick={onCancel}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}

// -- Connectors ---------------------------------------------------------------
// The Connectors tab body moved to connectors/ConnectorsSection.tsx (UX-DECISIONS
// §21: connected-first list + per-connector detail subpages). This file keeps the
// shared building blocks the detail pages reuse: ConnectSetup, ConnectorTools, and
// the two-way blocks (Allowlist/Unauthorized/ListeningSessions).

// Parked messages from senders not on the allow-list (§19). The gateway keeps what they said
// instead of dropping it, so first contact is one step: Allow & deliver replays the original
// message through the normal inbound path — no "message the bot again".
// With `teamId` (the Slack-workspaces page) only that workspace's parked messages show;
// resolving routes the allow to the right workspace server-side (the item carries its team).
export function UnauthorizedBlock({
  c,
  onChanged,
  teamId,
}: {
  c: Connector;
  onChanged: () => void;
  teamId?: string;
}) {
  const { t } = useI18n();
  const items = (c.unauthorized ?? []).filter(
    (m) => teamId === undefined || m.team_id === teamId,
  );
  if (items.length === 0) return null;
  const act = async (id: string, action: "dismiss" | "allow" | "allow_deliver") => {
    await resolveUnauthorized(c.name, id, action);
    onChanged();
  };
  return (
    <div
      className="border-t border-line px-3.5 py-3"
      data-testid={teamId ? `unauthorized-${c.name}-${teamId}` : `unauthorized-${c.name}`}
    >
      <div className={SEC_H + " mb-2"}>
        {t("connectors.parkedMessages", { n: items.length })}
      </div>
      <div className="space-y-2">
        {items.map((m) => (
          <div key={m.id} className="rounded-xl border border-line bg-paper p-2.5">
            <div className="flex items-center gap-2 text-[12px] text-muted">
              <span className="font-medium text-ink">{m.user_name || m.user_id}</span>
              <span>{t("connectors.inChannel", { name: m.chat_name || m.chat_id })}</span>
              <span className="ml-auto shrink-0">{relTime(t, m.ts) || ""}</span>
            </div>
            <div className="text-[12.5px] mt-1 break-words">{m.text}</div>
            <div className="flex items-center gap-1.5 mt-2">
              <button
                className="text-[11.5px] px-2 py-1 rounded-md bg-accent text-onAccent"
                data-testid={`parked-allow-deliver-${m.id}`}
                title={t("connectors.allowDeliverTitle")}
                onClick={() => act(m.id, "allow_deliver")}
              >
                {t("connectors.allowDeliver")}
              </button>
              <button
                className={BTN_BORDERED}
                data-testid={`parked-allow-${m.id}`}
                title={t("connectors.allowTitle")}
                onClick={() => act(m.id, "allow")}
              >
                {t("connectors.allowOnly")}
              </button>
              <button
                className="text-[11.5px] px-2 py-1 rounded-md text-faint hover:text-danger"
                data-testid={`parked-dismiss-${m.id}`}
                title={t("connectors.dismissTitle")}
                onClick={() => act(m.id, "dismiss")}
              >
                {t("common.dismiss")}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Which sessions listen to this connector's channels — the per-connector cut of the global
// Channel-subscriptions table (Integrations ▸ Messaging routing). Subscribing happens from a
// session's Sources ▸ Channels panel; here the owner can see and revoke.
export function ListeningSessionsBlock({ c }: { c: Connector }) {
  const { t } = useI18n();
  const [subs, setSubs] = useState<Subscription[] | null>(null);
  const load = () => getSubscriptions().then(setSubs).catch(() => setSubs([]));
  useEffect(() => {
    load();
  }, [c.name]);
  const platformOf = (channel: string) =>
    channel.includes(":") ? channel.split(":")[0] : "slack";
  const mine = (subs ?? []).filter((s) => platformOf(s.channel) === c.name);
  return (
    <div className="border-t border-line px-3.5 py-3" data-testid={`listening-${c.name}`}>
      <div className={SEC_H + " mb-2"}>{t("connectors.sessionsListening", { name: c.title, n: mine.length })}</div>
      {mine.length === 0 ? (
        <div className="text-[12px] text-faint">
          {t("connectors.noneListeningYet")}
        </div>
      ) : (
        <div className="space-y-1.5">
          {mine.map((s) => (
            <div className="flex items-center gap-2 text-[12.5px]" key={s.session_id + s.channel}>
              <span className="min-w-0 truncate" title={s.session_id}>
                {s.session_title || s.session_id}
                {s.agent ? <span className="text-faint"> · {s.agent}</span> : null}
              </span>
              <span className="text-muted shrink-0" title={s.channel}>
                ← {s.channel_name ? `#${s.channel_name}` : s.channel}
              </span>
              <button
                className="ml-auto text-faint hover:text-danger shrink-0"
                title={t("connectors.unsubscribeSession")}
                onClick={async () => {
                  await unsubscribeChannel(s.session_id, s.channel);
                  load();
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Who may message this two-way bot. Recent senders surface here once they DM/mention the bot, so you
// can Allow them; allowed users are chips you can remove. (Was orphaned in the super-agent view.)
// With `teamId` (the Slack-workspaces page) the list is that WORKSPACE's — ids are
// workspace-scoped, so allow/remove target `slack:team:<id>` and recents filter to the team.
export function AllowlistBlock({
  c,
  onChanged,
  teamId,
  allowed,
  allowedNames,
}: {
  c: Connector;
  onChanged: () => void;
  teamId?: string;
  allowed?: string[];
  allowedNames?: Record<string, string | null>;
}) {
  const { t } = useI18n();
  const allowedUsers = allowed ?? c.allowed_users;
  const names = allowedNames ?? c.allowed_user_names;
  const recent = (c.recent ?? []).filter(
    (r) => teamId === undefined || r.team_id === teamId,
  );
  const unknownRecent = recent.filter((r) => !r.authorized);

  return (
    <div className="border-t border-line px-3.5 py-3 grid grid-cols-2 gap-5">
      <div>
        <div className={SEC_H + " mb-2"}>{t("connectors.allowedToMessage")}</div>
        <div className="flex flex-wrap gap-1.5">
          {allowedUsers.length === 0 && (
            <span className="text-[12px] text-faint">{t("connectors.nobodyYetAllow")}</span>
          )}
          {allowedUsers.map((u) => (
            <span
              key={u}
              className="inline-flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-full bg-paper border border-line text-[12px]"
              title={`id ${u}`}
            >
              <span className="w-4 h-4 rounded-full bg-accentSoft text-accent grid place-items-center text-[9px] font-bold">
                {initials(names?.[u] || u)}
              </span>
              {names?.[u] || u}
              <button
                className="w-4 h-4 grid place-items-center text-faint hover:text-danger"
                title={t("common.remove")}
                onClick={async () => {
                  await disallowUser(c.name, u, teamId);
                  onChanged();
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      </div>
      <div>
        <div className={SEC_H + " mb-2"}>{t("connectors.recentSenders")}</div>
        {unknownRecent.length === 0 ? (
          <div className="text-[12px] text-faint">{t("connectors.noneYetRecent")}</div>
        ) : (
          <div className="space-y-1.5">
            {unknownRecent.map((r) => (
              <div className="flex items-center gap-2 text-[12.5px]" key={r.user_id}>
                <span className="w-5 h-5 rounded-full bg-paper border border-line grid place-items-center text-[9px] font-bold text-muted shrink-0">
                  {initials(r.user_name || "?")}
                </span>
                <span className="min-w-0 truncate" title={`id ${r.user_id}`}>
                  {r.user_name || t("common.unknown")} <span className="text-faint">· {r.chat_type}</span>
                </span>
                <button
                  className="ml-auto text-[11.5px] px-2 py-0.5 rounded-md bg-accent text-onAccent shrink-0"
                  onClick={async () => {
                    await allowUser(c.name, r.user_id, teamId);
                    onChanged();
                  }}
                >
                  {t("common.allow")}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function ConnectorTools({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const { t } = useI18n();
  const toggle = async (toolName: string, enabled: boolean) => {
    await updateConnectorTools(c.name, { [toolName]: enabled });
    onChanged();
  };
  if (!c.tools?.length)
    return (
      <div className="border-t border-line px-3.5 py-3 text-[12.5px] text-muted">
        {t("connectors.noToolsForConnector")}
      </div>
    );
  return (
    <div className="border-t border-line px-3.5 py-3">
      <div className={SEC_H + " mb-2"}>{t("connectors.toolsExposed")}</div>
      <div className="space-y-1.5">
        {c.tools.map((tool) => (
          <label
            className="flex items-start gap-2.5 p-2 rounded-lg border border-line bg-paper"
            key={tool.name}
          >
            <input
              type="checkbox"
              className="mt-0.5 shrink-0"
              checked={tool.enabled}
              onChange={(e) => toggle(tool.name, e.target.checked)}
            />
            <span className="min-w-0">
              <span className="block text-[13px]">{tool.label}</span>
              <span className="block text-[11.5px] text-faint">
                {tool.name} · {tool.kind} · {t("connectors.asksApproval")}
              </span>
              <span className="block text-[11.5px] text-faint">{tool.description}</span>
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

// Exported: also hosted inside the SourcesDrawer's connect-in-context child panel, so a
// recommended connector can be connected without leaving the session (owner ask, 2026-07-03).
export function ConnectSetup({
  c,
  cloud,
  onConnected,
  manualOnly = false,
}: {
  c: Connector;
  cloud: CloudStatus | null;
  onConnected: () => void;
  // The add-modal's Manual pane: the one-click button lives on the sibling
  // pill, so don't render the managed block again here.
  manualOnly?: boolean;
}) {
  const { t } = useI18n();
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false); // managed flow: browser is open
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    const res = await connectConnector(c.name, values);
    setBusy(false);
    if (res.ok) onConnected();
    else setError(res.error || t("connectors.errorCouldNotConnect"));
  };

  const oneClick = async () => {
    setError(null);
    const res = await connectManaged(c.name);
    // Completion arrives via the tab's poll: the broker form-POSTs the profile
    // to the sidecar, the connector flips to connected, this card closes itself.
    if (res.ok) setWaiting(true);
    else setError(res.error || t("connectors.errorStartConnect"));
  };

  const mcpOneClick = async () => {
    setError(null);
    const res = await connectMcpBacked(c.name);
    // Completion likewise arrives via the poll — the sidecar flips the connector
    // to connected once the local OAuth flow lands.
    if (res.ok) setWaiting(true);
    else setError(res.error || t("connectors.errorStartManaged"));
  };

  return (
    <div className="border-t border-line px-3.5 py-3 space-y-3">
      {c.mcp && !manualOnly && (
        /* MCP-backed one-click needs no cloud sign-in — the OAuth flow is local. */
        <div className="space-y-2" data-testid="mcp-connect">
          <button className={BTN_ACCENT} onClick={mcpOneClick} disabled={waiting}>
            {waiting
              ? t("connectors.checkBrowser")
              : t("connectors.connectWithOneClick", { name: c.title })}
          </button>
          {c.fields.length > 0 && (
            <div className="text-[11.5px] text-faint">{t("connectors.orConnectManually")}</div>
          )}
        </div>
      )}
      {c.managed && !c.mcp && !manualOnly && (
        <div className="space-y-2" data-testid="managed-connect">
          {c.managed_paused ? (
            // One-click temporarily off (e.g. Google pending CASA verification):
            // a visibly-parked button, and the manual path below stays fully live.
            <>
              <button className={BTN_ACCENT + " opacity-50"} disabled data-testid="managed-coming-soon">
                {t("connectors.connectWithOneClick", { name: c.title })}
                <span className="ml-2 text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-white/25">
                  {t("onboarding.comingSoon")}
                </span>
              </button>
              <div className="text-[11.5px] text-faint">
                {t("connectors.oneClickComingSoon")}
              </div>
            </>
          ) : cloud?.signed_in ? (
            <button className={BTN_ACCENT} onClick={oneClick} disabled={waiting}>
              {waiting
                ? t("connectors.checkBrowser")
                : t("connectors.connectWithOneClick", { name: c.title })}
            </button>
          ) : cloud ? (
            <CloudSignInInline
              blurb={t("connectors.signInUnlocksOneClick", { name: c.title })}
            />
          ) : (
            // Status unknown (fetch pending/failed): never show the sign-in ask to a
            // possibly-signed-in user (FB-013); the host keeps polling.
            <CloudStatusPending />
          )}
          {!c.managed_paused && cloud?.signed_in && (
            <div className="text-[11.5px] text-faint">{t("connectors.orConnectManually")}</div>
          )}
        </div>
      )}
      {c.instructions.length > 0 && (
        <ol className="list-decimal pl-4 text-[12.5px] text-muted leading-relaxed space-y-1">
          {c.instructions.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
      {c.fields.map((f) => (
        <label className="conn-field" key={f.key}>
          <span className="conn-field-label">
            {f.label}
            {!f.required && <em> ({t("common.optional")})</em>}
          </span>
          <input
            type={f.secret ? "password" : "text"}
            placeholder={f.placeholder}
            value={values[f.key] || ""}
            spellCheck={false}
            onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
          />
          {f.help && <span className="conn-field-help">{f.help}</span>}
        </label>
      ))}
      <div>
        <button className={BTN_ACCENT} onClick={submit} disabled={busy}>
          {busy ? t("connectors.validating") : t("connectors.connect")}
        </button>
      </div>
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
    </div>
  );
}
