import { useEffect, useState } from "react";
import { AUTOMATIONS_CHANGED, getAutomations, type Automation } from "../api";
import type { SessionInfo } from "../types";
import { ConnectorIcon } from "../features/connectors/ConnectorIcon";
import { Icon, type IconName } from "./Icon";
import { SearchModal } from "./SearchModal";
import { useI18n } from "@delta/i18n/I18nContext";

function AttnBadge({ n }: { n: number }) {
  const { t } = useI18n();
  if (!n) return null;
  return (
    <span
      className="text-[10px] font-semibold text-ink bg-faint/30 rounded-full px-1.5 leading-[15px] shrink-0"
      title={t("nav.attention", { n }, `${n} awaiting your attention`)}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

function SidebarFooterIcon({
  icon,
  label,
  active,
  onClick,
  badge,
  badgeTitle,
  testid,
}: {
  icon: IconName;
  label: string;
  active?: boolean;
  onClick: () => void;
  badge?: number | null;
  badgeTitle?: string;
  testid: string;
}) {
  return (
    <button
      className={
        "tip tip-nowrap relative w-8 h-8 grid place-items-center rounded-lg text-muted transition-colors " +
        (active ? "bg-paper text-ink" : "hover:bg-paper")
      }
      data-testid={testid}
      data-tip={label}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
    >
      <Icon name={icon} size={16} />
      {badge ? (
        <span className="absolute top-0.5 right-0.5 leading-none" title={badgeTitle}>
          <AttnBadge n={badge} />
        </span>
      ) : null}
    </button>
  );
}

function UnseenBadge({ n, failed }: { n: number; failed?: boolean }) {
  const { t } = useI18n();
  if (!n) return null;
  return (
    <span
      className="text-[10px] font-semibold text-ink bg-faint/30 rounded-full px-1.5 leading-[15px] shrink-0"
      title={
        failed
          ? t("nav.unseenFailed", { n, s: n > 1 ? "s" : "" }, `${n} new run${n > 1 ? "s" : ""} — the latest failed`)
          : t("nav.unseen", { n, s: n > 1 ? "s" : "" }, `${n} new run${n > 1 ? "s" : ""}`)
      }
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

function LiveDot({ state }: { state?: "working" | "sleeping" | "idle" }) {
  const { t } = useI18n();
  if (state !== "working" && state !== "sleeping") return null;
  return state === "working" ? (
    <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse shrink-0" title={t("nav.working", undefined, "Working now")} />
  ) : (
    <span className="w-1.5 h-1.5 rounded-full bg-faint/60 shrink-0" title={t("nav.sleeping", undefined, "Sleeping (will wake itself)")} />
  );
}

function OriginIcon({ session }: { session: SessionInfo }) {
  const { t } = useI18n();
  if (session.origin !== "slack") return null;
  return (
    <ConnectorIcon
      connector={{ logo: "slack", brand_color: "#611f69" }}
      size={12}
      title={session.origin_label || t("nav.fromSlack", undefined, "From Slack")}
    />
  );
}

function ConnectorDot({ subscriptions }: { subscriptions?: string[] }) {
  if (!subscriptions?.length) return null;
  return (
    <span
      className="w-1.5 h-1.5 rounded-full bg-faint shrink-0"
      data-brand={subscriptions[0]}
      title={subscriptions.join(", ")}
    />
  );
}

interface Props {
  sessions: SessionInfo[];
  activeSession: string;
  onNewSession: () => void;
  onSelectSession: (id: string, workspace: string) => void;
  onRenameSession: (id: string, title: string) => void;
  onDeleteSession: (id: string) => void;
  onArchiveSession: (id: string, archived: boolean) => void;
  onTogglePin: (id: string, pinned: boolean) => void;
  onSetReasoningEffort: (id: string, effort: string) => void;
  onManage: () => void;
  onOpenScheduled: () => void;
  onOpenAutomation: (id: string) => void;
  onOpenIntegrations: () => void;
  onOpenAudit: () => void;
  onOpenInbox: () => void;
  scheduledActive: boolean;
  integrationsActive: boolean;
  auditActive: boolean;
  inboxActive: boolean;
  collapsed?: boolean;
  onCollapse?: () => void;
  onPeekLeave?: () => void;
}

export function Sidebar(props: Props) {
  const { t } = useI18n();
  const [searchOpen, setSearchOpen] = useState(false);
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [rowMenuId, setRowMenuId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [recentExpanded, setRecentExpanded] = useState(false);
  const [showArchived, setShowArchived] = useState(false);

  useEffect(() => {
    const load = () => getAutomations().then(setAutomations).catch(() => {});
    load();
    const timer = window.setInterval(load, 15_000);
    window.addEventListener(AUTOMATIONS_CHANGED, load);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(AUTOMATIONS_CHANGED, load);
    };
  }, []);

  const real = props.sessions.filter((session) => !session.session_id.startsWith("__"));
  const pinned = real.filter((session) => session.pinned && !session.archived);
  const recent = real
    .filter((session) => !session.pinned && !session.archived)
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  const archived = real.filter((session) => session.archived);
  const totalAttention = real.reduce((sum, session) => sum + (session.attention || 0), 0);
  const recentLimit = 4;

  const closeRowMenu = () => {
    setRowMenuId(null);
    setConfirmDeleteId(null);
  };

  const row = (session: SessionInfo) => {
    const title = session.title || session.session_id;
    const active = session.session_id === props.activeSession;
    const editing = editingId === session.session_id;
    const menuOpen = rowMenuId === session.session_id;
    const commitRename = () => {
      const next = editValue.trim();
      if (next && next !== title) props.onRenameSession(session.session_id, next);
      setEditingId(null);
    };
    return (
      <div
        key={session.session_id}
        className={
          "group w-full flex items-center gap-2.5 px-2 py-2 rounded-lg cursor-pointer text-left " +
          (active ? "bg-ink/[0.055]" : "hover:bg-paper")
        }
        title={editing ? undefined : title}
        onClick={() => !editing && props.onSelectSession(session.session_id, session.workspace)}
      >
        {editing ? (
          <input
            className="flex-1 min-w-0 px-1.5 py-0.5 rounded-md bg-panel border border-accent text-[13px] text-ink outline-none"
            value={editValue}
            autoFocus
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => setEditValue(event.target.value)}
            onBlur={commitRename}
            onKeyDown={(event) => {
              event.stopPropagation();
              if (event.key === "Enter") commitRename();
              if (event.key === "Escape") setEditingId(null);
            }}
          />
        ) : (
          <>
            <span className="min-w-0 flex-1 block truncate text-[13px] font-medium">{title}</span>
            <span className={(menuOpen ? "hidden" : "flex") + " items-center gap-1.5 shrink-0 group-hover:hidden"}>
              <OriginIcon session={session} />
              <ConnectorDot subscriptions={session.subscriptions} />
              <LiveDot state={session.liveness} />
              <AttnBadge n={session.attention || 0} />
            </span>
            <span className={(menuOpen ? "flex" : "hidden group-hover:flex") + " items-center shrink-0"} onClick={(event) => event.stopPropagation()}>
              <button
                title={t("common.sessionActions", undefined, "Session actions")}
                aria-label={t("common.sessionActions", undefined, "Session actions")}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                data-testid="row-menu"
                className="w-5 h-5 grid place-items-center rounded hover:bg-paper text-faint hover:text-ink"
                onClick={() => menuOpen ? closeRowMenu() : setRowMenuId(session.session_id)}
              >
                <Icon name="moreHorizontal" size={14} className="rotate-90" />
              </button>
              {menuOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={closeRowMenu} />
                  <div className="absolute z-50 mt-7 right-3 w-40 rounded-xl border border-line bg-panel shadow-xl py-1" role="menu">
                    <RowAction label={t("common.rename", undefined, "Rename")} icon="pencil" testid="row-menu-rename" onClick={() => {
                      closeRowMenu();
                      setEditingId(session.session_id);
                      setEditValue(title);
                    }} />
                    <RowAction label={session.pinned ? t("common.unpin", undefined, "Unpin") : t("common.pin", undefined, "Pin")} icon="pin" testid="row-menu-pin" onClick={() => {
                      closeRowMenu();
                      props.onTogglePin(session.session_id, !session.pinned);
                    }} />
                    <RowAction label={session.archived ? t("common.unarchive", undefined, "Unarchive") : t("common.archive", undefined, "Archive")} icon="archive" testid="row-menu-archive" onClick={() => {
                      closeRowMenu();
                      props.onArchiveSession(session.session_id, !session.archived);
                    }} />
                    <div className="h-px bg-line my-1 mx-2" />
                    <div className="px-2.5 pt-1.5 pb-1 text-[10.5px] uppercase tracking-wide text-faint">
                      {t("nav.reasoningDepth", undefined, "Reasoning depth")}
                    </div>
                    {(["auto", "low", "high", "max"] as const).map((level) => (
                      <button
                        key={level}
                        className="w-full flex items-center gap-2 px-2.5 py-1 text-[12.5px] text-left hover:bg-paper"
                        role="menuitemradio"
                        aria-checked={(session.reasoning_effort || "auto") === level}
                        onClick={() => {
                          closeRowMenu();
                          props.onSetReasoningEffort(session.session_id, level);
                        }}
                      >
                        <span className="w-3.5 shrink-0 text-accent">{(session.reasoning_effort || "auto") === level ? "✓" : ""}</span>
                        <span className="flex-1">{t(`nav.reasoning.${level}`, undefined, level === "auto" ? "Default" : level)}</span>
                      </button>
                    ))}
                    <div className="h-px bg-line my-1 mx-2" />
                    <button
                      className="w-full flex items-center gap-2 px-2.5 py-1.5 text-[12.5px] text-left text-danger hover:bg-paper"
                      data-testid="row-menu-delete"
                      role="menuitem"
                      onClick={() => {
                        if (confirmDeleteId === session.session_id) {
                          closeRowMenu();
                          props.onDeleteSession(session.session_id);
                        } else {
                          setConfirmDeleteId(session.session_id);
                        }
                      }}
                    >
                      <Icon name="trash" size={13} className="shrink-0" />
                      <span className="flex-1">{t("common.delete", undefined, "Delete")}{confirmDeleteId === session.session_id ? "?" : ""}</span>
                    </button>
                  </div>
                </>
              )}
            </span>
          </>
        )}
      </div>
    );
  };

  return (
    <div className="sidebar flex flex-col min-h-0 bg-panel border-r border-line" onMouseLeave={props.onPeekLeave}>
      <div className="brand px-3.5 pt-2.5 pb-3 flex items-center gap-2" data-tauri-drag-region>
        {props.onCollapse && (
          <button
            className="nav-pin-btn w-7 h-7 grid place-items-center rounded-md text-faint hover:text-ink hover:bg-paper shrink-0"
            title={props.collapsed ? t("nav.dockSidebar", undefined, "Dock sidebar (⌘B)") : t("nav.collapse", undefined, "Collapse sidebar (⌘B)")}
            aria-label={props.collapsed ? t("nav.dockSidebar", undefined, "Dock sidebar") : t("nav.collapse", undefined, "Collapse sidebar")}
            onClick={props.onCollapse}
          >
            <Icon name="sidebar" size={16} />
          </button>
        )}
        <div className="brand-wordmark text-[15px]">Delta</div>
        <button
          className="tip tip-below tip-start tip-nowrap nav-search-btn w-7 h-7 grid place-items-center rounded-md text-faint hover:text-ink hover:bg-paper shrink-0 ml-auto"
          data-tip={t("common.search", undefined, "Search")}
          aria-label={t("common.search", undefined, "Search")}
          onClick={() => setSearchOpen(true)}
        >
          <Icon name="search" size={16} />
        </button>
        {searchOpen && (
          <SearchModal
            sessions={props.sessions}
            onSelect={(id, workspace) => {
              setSearchOpen(false);
              props.onSelectSession(id, workspace);
            }}
            onClose={() => setSearchOpen(false)}
          />
        )}
      </div>

      <div className="px-3 pt-2">
        <button
          className="w-full text-left px-3 py-2 bg-accent text-onAccent text-[13px] font-medium hover:opacity-95 flex items-center gap-2 rounded-lg"
          onClick={props.onNewSession}
        >
          <Icon name="plus" size={15} className="shrink-0" /> {t("nav.newSession", undefined, "New task")}
        </button>
      </div>

      <div className="px-2.5 mt-1">
        <button
          className={
            "w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] text-left hover:bg-paper hover:text-ink " +
            (props.scheduledActive ? "text-ink bg-paper" : "text-muted")
          }
          data-testid="nav-automations"
          onClick={props.onOpenScheduled}
        >
          <Icon name="clock" size={15} className="shrink-0" />
          <span className="flex-1">{t("nav.scheduled", undefined, "Automations")}</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2.5 mt-3 pb-2">
        <div className="space-y-4">
          {pinned.length > 0 && <TaskBand title={t("common.pinned", undefined, "Pinned")}>{pinned.map(row)}</TaskBand>}
          {automations.length > 0 && (
            <TaskBand title={t("common.scheduled", undefined, "Scheduled")} testid="scheduled-band">
              {automations.map((automation) => (
                <button
                  key={automation.id}
                  className="w-full flex items-center gap-2 px-1.5 py-1 rounded-lg text-left hover:bg-paper"
                  data-testid={`scheduled-${automation.id}`}
                  title={automation.title}
                  onClick={() => props.onOpenAutomation(automation.id)}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] text-ink truncate">{automation.title}</div>
                    <div className="text-[11px] text-faint truncate">{automation.schedule}</div>
                  </div>
                  <UnseenBadge n={automation.unseen_runs || 0} failed={automation.unseen_failed} />
                </button>
              ))}
            </TaskBand>
          )}
          <TaskBand title={t("nav.recent", undefined, "Recent")} testid="recent-header">
            {recent.length === 0 ? (
              <div className="px-2 py-1.5 text-[12px] text-faint leading-snug">{t("nav.noConversations", undefined, "No conversations yet.")}</div>
            ) : (
              <>
                {(recentExpanded ? recent : recent.slice(0, recentLimit)).map(row)}
                {recent.length > recentLimit && (
                  <button className="w-full text-left px-2 py-1.5 text-[12px] text-muted hover:text-ink" onClick={() => setRecentExpanded((value) => !value)}>
                    {recentExpanded
                      ? t("common.showLess", undefined, "Show less")
                      : t("nav.showMoreFlat", { n: recent.length - recentLimit }, `Show ${recent.length - recentLimit} more`)}
                  </button>
                )}
              </>
            )}
          </TaskBand>
          {archived.length > 0 && (
            <div>
              <button className="w-full flex items-center gap-1.5 px-1.5 py-1 rounded text-[12px] text-faint hover:text-muted" onClick={() => setShowArchived((value) => !value)}>
                <Icon name={showArchived ? "chevronDown" : "chevronRight"} size={13} />
                {t("common.archived", undefined, "Archived")} ({archived.length})
              </button>
              {showArchived && <div className="space-y-0.5 mt-0.5">{archived.map(row)}</div>}
            </div>
          )}
        </div>
      </div>

      <div className="px-2.5 py-2 border-t border-line">
        <div className="relative flex items-center justify-between gap-1">
          <SidebarFooterIcon icon="inbox" label={t("nav.inbox", undefined, "Inbox")} active={props.inboxActive} onClick={props.onOpenInbox} badge={totalAttention || null} badgeTitle={totalAttention ? t("nav.inboxItemsNeedYou", { n: totalAttention }, `Inbox — ${totalAttention} items need you`) : undefined} testid="sidebar-footer-inbox" />
          <SidebarFooterIcon icon="audit" label={t("nav.activity", undefined, "Activity")} active={props.auditActive} onClick={props.onOpenAudit} testid="sidebar-footer-activity" />
          <SidebarFooterIcon icon="plug" label={t("nav.integrations", undefined, "Connectors")} active={props.integrationsActive} onClick={props.onOpenIntegrations} testid="sidebar-footer-integrations" />
          <SidebarFooterIcon icon="gear" label={t("nav.settings", undefined, "Settings")} onClick={props.onManage} testid="sidebar-footer-settings" />
        </div>
      </div>
    </div>
  );
}

function TaskBand({ title, testid, children }: { title: string; testid?: string; children: React.ReactNode }) {
  return (
    <div data-testid={testid}>
      <div className="px-1.5 text-[10.5px] uppercase tracking-[0.07em] text-faint font-semibold mb-1">{title}</div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function RowAction({ label, icon, testid, onClick }: { label: string; icon: IconName; testid: string; onClick: () => void }) {
  return (
    <button className="w-full flex items-center gap-2 px-2.5 py-1.5 text-[12.5px] text-left hover:bg-paper" data-testid={testid} role="menuitem" onClick={onClick}>
      <Icon name={icon} size={13} className="shrink-0 text-muted" />
      <span className="flex-1">{label}</span>
    </button>
  );
}
