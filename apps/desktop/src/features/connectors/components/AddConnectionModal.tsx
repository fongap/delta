import { useEffect, useState } from "react";
import {
  connectMcpBacked,
  getConnectors,
  type Connector,
} from "../../../api";
import { ConnectorBadge } from "../ConnectorIcon";
import { ConnectSetup } from "../../../components/ManageTabs";
import { PILL_ACCENT } from "./ui";
import { useI18n } from "@delta/i18n/I18nContext";

// The ONE place a connection gets added (UX-DECISIONS §21): the detail page's header
// button (or the list's Connect pill) opens this sheet. Managed OAuth removed (ADR-004);
// only manual connect remains. MCP-backed connectors still offer local one-click.

export function AddConnectionModal({
  c,
  title,
  onClose,
  onChanged,
}: {
  c: Connector;
  title?: string; // e.g. "Add a workspace" — defaults to "Connect {title}"
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useI18n();
  // MCP-backed one-click (§42): local OAuth against the vendor's hosted MCP server.
  const mcpBacked = !!c.mcp;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40" data-testid="add-connection-modal">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div
        className="absolute left-1/2 top-[14%] -translate-x-1/2 w-[480px] max-w-[calc(100vw-2rem)] bg-panel rounded-2xl border border-line shadow-2xl"
        role="dialog"
        aria-label={title || t("connectors.connectTitle", { name: c.title })}
      >
        <div className="flex items-center gap-3 px-5 pt-5">
          <ConnectorBadge connector={c} size={34} title={c.title} />
          <div className="flex-1 font-semibold text-[16px] tracking-tight">
            {title || t("connectors.connectTitle", { name: c.title })}
          </div>
          <button
            className="text-faint hover:text-ink text-[18px] leading-none"
            onClick={onClose}
            title={t("common.close")}
          >
            ×
          </button>
        </div>

        {mcpBacked ? (
          /* MCP-backed with no manual fields (monday): one-click IS the flow. */
          <McpOneClick c={c} onConnected={() => { onChanged(); onClose(); }} />
        ) : (
          <div className="px-1.5 pb-2">
            <ConnectSetup c={c} onConnected={() => { onChanged(); onClose(); }} manualOnly />
          </div>
        )}
      </div>
    </div>
  );
}

// One-click pane for MCP-BACKED connectors (monday, asana, jira — §42): the sidecar
// runs a fully LOCAL OAuth flow against the vendor's hosted MCP server (DCR — no
// client secret, no broker, no Delta sign-in required). Poll until the card
// flips to connected, then close.
function McpOneClick({ c, onConnected }: { c: Connector; onConnected: () => void }) {
  const { t } = useI18n();
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(async () => {
      try {
        const list = await getConnectors();
        if (list.find((x) => x.name === c.name)?.connected) onConnected();
      } catch {
        /* keep polling */
      }
    }, 2000);
    return () => clearInterval(t);
  }, [c.name, onConnected, waiting]);

  const start = async () => {
    setWaiting(true);
    setError(null);
    const res = await connectMcpBacked(c.name);
    if (!res.ok) {
      setWaiting(false);
      setError(res.error || "MCP connect failed");
    }
    // On ok=true the sidecar accepts the connect; the poll above closes the modal
    // when /v1/connectors reports connected. We must NOT setWaiting(false) here or
    // the effect's interval tears down and the modal never auto-closes.
  };

  return (
    <div className="px-5 py-4 space-y-3">
      <div className="text-[12.5px] text-muted">
        {t("connectors.mcpOneClickDesc")}
      </div>
      <button
        className={PILL_ACCENT}
        onClick={start}
        disabled={waiting}
        data-testid="mcp-one-click"
      >
        {waiting ? t("connectors.checkBrowser") : t("connectors.connectWithOneClick", { name: c.title })}
      </button>
      {error && <p className="text-[12px] text-warn">{error}</p>}
    </div>
  );
}