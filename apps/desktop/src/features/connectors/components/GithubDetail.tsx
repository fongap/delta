import { useEffect, useState } from "react";
import {
  disallowUser,
  disconnectGithubInstallation,
  getGithubStatus,
  getSubscriptions,
  resolveUnauthorized,
  unsubscribeChannel,
  type Connector,
  type GithubInstallation,
  type GithubStatus,
  type ParkedMessage,
  type Subscription,
} from "../../../api";
import { ConnectorBadge } from "../ConnectorIcon";
import { AddConnectionModal } from "./AddConnectionModal";
import type { DetailProps } from "./ConnectorsSection";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { FOOT, GRP, GRP_H, PILL_ACCENT, PILL_LINE, ROW, TAG_WARN, XBTN } from "./ui";
import { useI18n } from "@delta/i18n/I18nContext";

// The GitHub detail page (github-relay-spec §8), the Slack page's shape: one
// group per App INSTALLATION (the allow-list scope) — People (sender logins
// allowed to trigger work) · Waiting (parked mentions) · per-installation
// disconnect — plus a page-level Listening group (a subscription names a repo
// thread, which the GUI can't map back to an installation). Adding an
// installation goes through the ONE entry point: header button → modal.

const LABEL = "text-[12.5px] text-muted w-24 shrink-0";

type T = (key: string, vars?: Record<string, string | number>) => string;

/** The relay status line, one honest layer at a time (the Slack rule). */
function relayHealth(gh: GithubStatus | null, t: T): { dot: string; text: string } {
  if (!gh) return { dot: "bg-ok", text: t("connectors.healthLiveRelay") };
  if (gh.relay.state === "offline")
    return { dot: "bg-faint/60", text: t("connectors.healthOfflineRelay") };
  if (gh.relay.state === "reconnecting")
    return { dot: "bg-warnInk", text: t("connectors.healthReconnectingRelay") };
  return { dot: "bg-ok", text: t("connectors.healthLiveRelay") };
}

export function GithubDetail({ c, onChanged }: DetailProps) {
  const { t } = useI18n();
  const [adding, setAdding] = useState(false);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [status, setStatus] = useState<GithubStatus | null>(null);
  const load = () => {
    getSubscriptions().then(setSubs).catch(() => setSubs([]));
    getGithubStatus().then(setStatus).catch(() => setStatus(null));
  };
  useEffect(() => {
    load();
  }, [c.name]);

  const relay = c.mode === "relay";
  const installations = c.installations ?? [];
  const changed = () => {
    onChanged();
    load();
  };
  const listening = subs.filter((s) => s.channel.startsWith("github:"));

  return (
    <div data-testid="github-installations">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title={t("connectors.brand.github")} />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">
            {t("connectors.brand.github")}
          </h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span
                  className={
                    "w-2 h-2 rounded-full " + (relay ? relayHealth(status, t).dot : "bg-ok")
                  }
                />
                <span data-testid="github-mode-badge">
                  {relay
                    ? relayHealth(status, t).text
                    : t("connectors.connectedPat")}
                </span>
              </>
            ) : (
              <span>{t("connectors.notConnected")}</span>
            )}
          </div>
        </div>
        {(relay || !c.connected) && (
          <button
            className={PILL_ACCENT}
            data-testid="add-installation-btn"
            onClick={() => setAdding(true)}
          >
            {t("connectors.addInstallation")}
          </button>
        )}
      </div>

      {!c.connected && (
        <div className={GRP}>
          <div className={ROW + " text-[12.5px] text-muted"}>
            {t("connectors.githubNotConnectedBlurb")}
          </div>
        </div>
      )}

      {relay &&
        installations.map((inst) => (
          <InstallationGroup
            key={inst.installation_id}
            c={c}
            inst={inst}
            tokenOk={status?.installs?.[inst.installation_id]?.token_ok !== false}
            onChanged={changed}
          />
        ))}

      {/* Manual PAT: request/response tools only — no inbound triggers. */}
      {c.connected && !relay && (
        <div className={GRP} data-testid="github-manual-card">
          <div className={ROW + " text-[12.5px] text-muted"}>
            {t("connectors.githubPatNote")}
          </div>
        </div>
      )}

      {relay && listening.length > 0 && (
        <>
          <div className={GRP_H}>{t("connectors.listening")}</div>
          <div className={GRP}>
            <ListeningRows subs={listening} onChanged={changed} />
          </div>
        </>
      )}

      <ToolsDisclosure c={c} onChanged={onChanged} />
      {c.connected && relay && (
        <div className={FOOT + " mt-2"}>{t("connectors.githubTriggers")}</div>
      )}

      {adding && (
        <AddConnectionModal
          c={c}
          title={t("connectors.addInstallation")}
          onClose={() => setAdding(false)}
          onChanged={changed}
        />
      )}
    </div>
  );
}

function InstallationGroup({
  c,
  inst,
  tokenOk,
  onChanged,
}: {
  c: Connector;
  inst: GithubInstallation;
  tokenOk: boolean;
  onChanged: () => void;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const parked = (c.unauthorized ?? []).filter((m) => m.team_id === inst.installation_id);
  const empty = inst.allowed_users.length === 0 && parked.length === 0;

  const disconnect = async () => {
    setBusy(true);
    await disconnectGithubInstallation(inst.installation_id);
    setBusy(false);
    onChanged();
  };

  return (
    <div data-testid={`github-install-${inst.installation_id}`}>
      <div className={GRP_H + " flex items-center gap-2"}>
        <span>
          {inst.account_login}{" "}
          <span className="font-normal text-faint" title={t("connectors.installationId", { id: inst.installation_id })}>
            · {inst.repo_selection === "all" ? t("connectors.allRepos") : t("connectors.selectedRepos")}
          </span>
        </span>
        {!tokenOk && (
          <span className={TAG_WARN} data-testid={`token-warn-${inst.installation_id}`}>
            {t("connectors.installationRevoked")}
          </span>
        )}
      </div>
      <div className={GRP}>
        {empty ? (
          <div className={ROW}>
            <span className="min-w-0 flex-1 text-[12.5px] text-muted">
              {t("connectors.noOneAllowed")}
            </span>
            <DisconnectBtn id={inst.installation_id} busy={busy} onClick={disconnect} />
          </div>
        ) : (
          <>
            <PeopleRow
              allowed={inst.allowed_users}
              installationId={inst.installation_id}
              onChanged={onChanged}
            />
            {parked.map((m) => (
              <WaitingRow key={m.id} m={m} onChanged={onChanged} />
            ))}
            <div className={ROW}>
              <span className="flex-1" />
              <DisconnectBtn id={inst.installation_id} busy={busy} onClick={disconnect} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function DisconnectBtn({ id, busy, onClick }: { id: string; busy: boolean; onClick: () => void }) {
  const { t } = useI18n();
  return (
    <button
      className="text-[12.5px] text-danger/80 hover:text-danger shrink-0"
      data-testid={`disconnect-install-${id}`}
      title={t("connectors.disconnectInstallTitle")}
      onClick={onClick}
      disabled={busy}
    >
      {busy ? t("connectors.disconnecting") : t("connectors.disconnectInstallation")}
    </button>
  );
}

function PeopleRow({
  allowed,
  installationId,
  onChanged,
}: {
  allowed: string[];
  installationId: string;
  onChanged: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className={ROW}>
      <span className={LABEL}>{t("connectors.people")}</span>
      <span className="min-w-0 flex-1 flex flex-wrap items-center gap-1.5">
        {allowed.length === 0 && (
          <span className="text-[12px] text-faint">{t("connectors.nobodyYet")}</span>
        )}
        {allowed.map((login) => (
          <span
            key={login}
            className="inline-flex items-center gap-1.5 pl-2 pr-2 py-0.5 rounded-full bg-paper border border-line text-[12.5px]"
          >
            {/* GitHub logins ARE the readable identity — no resolution needed. */}
            @{login}
            <button
              className={XBTN}
              title={t("common.remove")}
              onClick={() => disallowUser("github", login, installationId).then(onChanged)}
            >
              ×
            </button>
          </span>
        ))}
      </span>
    </div>
  );
}

function WaitingRow({ m, onChanged }: { m: ParkedMessage; onChanged: () => void }) {
  const { t } = useI18n();
  const act = async (action: "dismiss" | "allow" | "allow_deliver") => {
    await resolveUnauthorized("github", m.id, action);
    onChanged();
  };
  return (
    <div className={ROW + " bg-warnSoft/25"} data-testid={`waiting-${m.id}`}>
      <span className={LABEL}>{t("connectors.waiting")}</span>
      <span className="min-w-0 flex-1">
        <span className="font-medium text-[13px]">@{m.user_name || m.user_id}</span>{" "}
        <span className="text-[12.5px] text-muted">
          {t("connectors.inChannel", { name: m.chat_name || m.chat_id })}
        </span>
        <span className="block text-[12.5px] text-muted truncate">“{m.text}”</span>
      </span>
      <button
        className={PILL_ACCENT + " !py-1"}
        data-testid={`parked-allow-deliver-${m.id}`}
        title={t("connectors.allowDeliverTitle")}
        onClick={() => act("allow_deliver")}
      >
        {t("connectors.allowDeliver")}
      </button>
      <button
        className={PILL_LINE + " !py-1"}
        data-testid={`parked-allow-${m.id}`}
        title={t("connectors.allowTitle")}
        onClick={() => act("allow")}
      >
        {t("common.allow")}
      </button>
      <button className={XBTN + " px-1"} data-testid={`parked-dismiss-${m.id}`} title={t("common.dismiss")} onClick={() => act("dismiss")}>
        ×
      </button>
    </div>
  );
}

function ListeningRows({ subs, onChanged }: { subs: Subscription[]; onChanged: () => void }) {
  const { t } = useI18n();
  return (
    <div className={ROW} data-testid="listening-github">
      <span className={LABEL}>{t("connectors.listening")}</span>
      <span className="min-w-0 flex-1 space-y-1">
        {subs.map((s) => (
          <span key={s.session_id + s.channel} className="flex items-center gap-2 text-[12.5px]">
            <span className="font-medium truncate" title={s.session_id}>
              {s.session_title || s.session_id}
            </span>
            <span className="text-faint">←</span>
            <span className="text-muted truncate" title={s.channel}>
              {s.channel.replace(/^github:/, "")}
            </span>
            <button
              className={XBTN + " ml-auto"}
              title={t("connectors.unsubscribeSession")}
              onClick={async () => {
                await unsubscribeChannel(s.session_id, s.channel);
                onChanged();
              }}
            >
              ×
            </button>
          </span>
        ))}
      </span>
    </div>
  );
}
