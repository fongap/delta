import { useEffect, useState } from "react";
import { getConnectors } from "../api";
import { McpTab } from "./ManageTabs";
import { ConnectorsSection } from "../features/connectors/components/ConnectorsSection";
import { Icon } from "./Icon";
import { useI18n, type TranslationKey } from "@delta/i18n/I18nContext";

// The Connectors surface (renamed from "Integrations", §26) keeps the left sub-nav, now just
// Connectors · MCP. The old "Messaging routing" tab (and its ⚠ unrouted badge) moved whole to
// Inbox ▸ Configure (§28): inbox-delivery config belongs with the Inbox, and Unrouted is
// "messages that never reached you". The one remaining Activity is the audit log, reached from
// the account menu.
type IntTab = "connectors" | "mcp";

// Fixed sub-nav (UX-DECISIONS §21): connector detail lives as a SUBPAGE under
// Connectors, never as a nav item — the nav must not grow per connector.
const INT_TABS: { key: IntTab; labelKey: TranslationKey; icon: "plug" | "code" }[] = [
  { key: "connectors", labelKey: "connectors.title", icon: "plug" },
  { key: "mcp", labelKey: "connectors.mcp", icon: "code" },
];

export function IntegrationsView() {
  const [tab, setTab] = useState<IntTab>("connectors");
  const { t } = useI18n();
  // Sub-nav count: how many connectors exist. Polled so the badge stays live.
  const [connCount, setConnCount] = useState<number | null>(null);

  useEffect(() => {
    const load = () => {
      getConnectors().then((cs) => setConnCount(cs.length)).catch(() => {});
    };
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <main className="flex-1 min-w-0 flex bg-paper">
      <nav className="page-subnav w-[208px] shrink-0 border-r border-line bg-panel/40 px-3 py-4">
        <div className="px-2 text-[13.5px] font-semibold mb-3 flex items-center gap-2">
          <Icon name="plug" size={16} /> {t("connectors.title")}
        </div>
        {INT_TABS.map((tb) => {
          const active = tab === tb.key;
          return (
            <button
              key={tb.key}
              className={
                "w-full text-left px-2.5 py-2 rounded-lg text-[13px] flex items-center justify-between " +
                (active
                  ? "bg-paper text-accent font-medium"
                  : "text-muted hover:bg-paper hover:text-ink")
              }
              onClick={() => setTab(tb.key)}
            >
              <span className="flex items-center gap-2 min-w-0">
                <Icon name={tb.icon} size={15} /> {t(tb.labelKey)}
              </span>
              {tb.key === "connectors" && connCount != null && (
                <span className={"text-[11px] shrink-0 " + (active ? "text-accent" : "text-faint")}>
                  {connCount}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="flex-1 min-w-0 overflow-y-auto hairline-scroll">
        <div className="max-w-4xl mx-auto px-7 py-6">
          {tab === "connectors" ? (
            <section>
              <PanelHead
                title={t("connectors.title")}
                sub={t("connectors.subList")}
              />
              <ConnectorsSection />
            </section>
          ) : (
            <section>
              <PanelHead
                title={t("connectors.mcp")}
                sub={t("connectors.mcpSub")}
              />
              <McpTab />
            </section>
          )}
        </div>
      </div>
    </main>
  );
}

export function PanelHead({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-[18px] font-semibold tracking-tight">{title}</h2>
      <p className="text-[12.5px] text-muted mt-0.5">{sub}</p>
    </div>
  );
}
