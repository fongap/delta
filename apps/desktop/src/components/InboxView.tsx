import { useEffect, useState } from "react";
import {
  getConnectors,
  getInbox,
  getInboxRouting,
  getRecentChannels,
  getUnrouted,
  resolveInboxItem,
  type InboxItem,
  type RecentChannel,
} from "../api";
import { Icon } from "./Icon";
import { InboxItemCard } from "./InboxItemCard";
import { InboxConfigure } from "./InboxConfigure";
import { PanelHead } from "./IntegrationsView";
import { useI18n } from "@delta/i18n/I18nContext";

const KIND_TABS: { key: string; label: string }[] = [
  { key: "all", label: "inbox.kind.all" },
  { key: "approval", label: "inbox.kind.approvals" },
  { key: "question", label: "inbox.kind.questions" },
];

const CHIP = (active: boolean) =>
  "text-[11.5px] px-2.5 py-1 rounded-full border " +
  (active
    ? "border-accent text-accent bg-accentSoft"
    : "border-line text-muted hover:border-lineStrong");

// Page-level tabs (§28): underline style, one visual level ABOVE the filter chips.
const TAB = (active: boolean) =>
  "pb-2 -mb-px text-[13px] border-b-2 flex items-center gap-1.5 " +
  (active
    ? "text-ink font-medium border-accent"
    : "text-muted border-transparent hover:text-ink");

// The Inbox: pending approvals / questions / notifications from across sessions, including
// unattended ones. Resolving here releases any agent suspended on the item. Each item links back
// to its originating session so you can see the context before answering. Items whose session
// was deleted are closed server-side (an orphaned prompt can never be answered), so everything
// listed here is actionable. Filters are intentionally task-kind only.
// Two page tabs (§28): Pending (the queue) and Configure (the former Connectors ▸ Messaging
// routing page — mirror channel, DM route, subscriptions, Unrouted). Pending's routing status
// is read-only and links to Configure; the old inline editor was the mirror setting's SECOND
// editor and is gone.
export function InboxView({
  onOpenSession,
}: {
  onOpenSession: (sessionId: string, workspace: string) => void;
}) {
  const { t } = useI18n();
  const [tab, setTab] = useState<"pending" | "configure">("pending");
  const [items, setItems] = useState<InboxItem[]>([]);
  const [routing, setRouting] = useState<string | null>(null); // e.g. "slack:C0123" or null
  const [slackConnected, setSlackConnected] = useState(false);
  const [recent, setRecent] = useState<RecentChannel[]>([]);
  const [unroutedCount, setUnroutedCount] = useState(0);
  const [kind, setKind] = useState<string>("all");

  const load = () => {
    getInbox(undefined, "pending").then(setItems).catch(() => {});
    getUnrouted().then((u) => setUnroutedCount(u.length)).catch(() => setUnroutedCount(0));
  };
  const loadRouting = () =>
    getInboxRouting()
      .then((bindings) => {
        const bound = bindings.find((b) => b.channel);
        setRouting(bound ? `${bound.channel}:${bound.target}` : null);
      })
      .catch(() => setRouting(null));
  useEffect(() => {
    load();
    loadRouting();
    getConnectors()
      .then((cs) => setSlackConnected(!!cs.find((c) => c.name === "slack" && c.connected)))
      .catch(() => {});
    getRecentChannels().then(setRecent).catch(() => setRecent([]));
    const timer = setInterval(() => {
      load();
      loadRouting(); // edits happen on the Configure tab; keep Pending's status line honest
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  const resolve = async (id: string, resolution: string) => {
    await resolveInboxItem(id, resolution);
    load();
  };

  const visible = items.filter((it) => kind === "all" || it.kind === kind);

  // The originating-session chip links back to the Delta task.
  const sessionChip = (it: InboxItem) => {
    const exists = it.session_exists !== false;
    const label = it.session_title || it.session_id;
    return (
      <button
        className="inbox-session-chip"
        title={exists ? t("inbox.sessionChip.open", { label }) : t("inbox.sessionChip.unavailable")}
        disabled={!exists}
        onClick={() =>
          exists && onOpenSession(it.session_id, it.session_workspace || "")
        }
      >
        <span className="inbox-chip-ico ico-delta">
          <Icon name="diamond" size={11} />
        </span>
        <span className="inbox-chip-label">{label}</span>
        {exists && <Icon name="chevronRight" size={13} className="inbox-chip-go" />}
      </button>
    );
  };

  const routingName = routing ? recent.find((c) => c.channel === routing)?.name : undefined;
  const routingLabel = routingName ? `#${routingName}` : routing;

  return (
    <main className="flex-1 min-w-0 flex bg-paper">
      <div className="flex-1 min-w-0 overflow-y-auto hairline-scroll">
        <div className="max-w-4xl mx-auto px-7 py-6">
          <PanelHead
            title={t("nav.inbox")}
            sub={t("inbox.sub")}
          />

          <div className="flex gap-5 border-b border-line mb-4">
            <button
              className={TAB(tab === "pending")}
              data-testid="inbox-tab-pending"
              onClick={() => {
                setTab("pending");
                // Configure-tab edits change the mirror target — re-read so the status line
                // is honest the moment the user lands back on Pending, not a poll later.
                loadRouting();
                load();
              }}
            >
              {t("common.pending")}
              {items.length > 0 && (
                <span className="text-[11px] px-1.5 rounded-full bg-accentSoft text-accent leading-4">
                  {items.length}
                </span>
              )}
            </button>
            <button
              className={TAB(tab === "configure")}
              data-testid="inbox-tab-configure"
              onClick={() => setTab("configure")}
            >
              {t("inbox.tab.configure")}
              {unroutedCount > 0 && (
                <span className="text-[11px] px-1.5 rounded-full bg-warnSoft text-warnInk leading-4">
                  ⚠ {unroutedCount}
                </span>
              )}
            </button>
          </div>

          {tab === "configure" ? (
            <InboxConfigure />
          ) : (
            <>
              <div className="text-[12px] text-faint -mt-1 mb-4" data-testid="inbox-routing">
                {routing ? (
                  <span>
                    {t("inbox.routing.alsoDeliveredTo")}
                    <span className="text-muted" title={routing}>
                      {routingLabel}
                    </span>
                    {t("inbox.routing.repliesResolveHere")}
                  </span>
                ) : slackConnected ? (
                  <span>{t("inbox.routing.deliveredHereOnly")}</span>
                ) : (
                  <span>{t("inbox.routing.deliveredHereOnlyConnect")}</span>
                )}
                <button
                  className="text-accent hover:underline"
                  data-testid="inbox-route-configure"
                  onClick={() => setTab("configure")}
                >
                  {t("inbox.routing.configureCta")}
                </button>
              </div>

              <div className="flex items-center gap-2 flex-wrap mb-4" data-testid="inbox-filters">
                {KIND_TABS.map((tab) => (
                  <button key={tab.key} className={CHIP(kind === tab.key)} onClick={() => setKind(tab.key)}>
                    {t(tab.label)}
                  </button>
                ))}
              </div>

              {visible.length === 0 ? (
                <div className="manage-empty">
                  {items.length === 0
                    ? t("inbox.empty.nothingPending")
                    : t("inbox.empty.nothingPendingForFilter")}
                </div>
              ) : null}

              <div className="space-y-4">
                {visible.map((it) => (
                  <InboxItemCard key={it.id} item={it} onResolve={resolve} chip={sessionChip(it)} />
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
