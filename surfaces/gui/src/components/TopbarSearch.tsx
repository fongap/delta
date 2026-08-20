import { useEffect, useRef, useState } from "react";
import type { Persona } from "../api";
import type { SessionInfo } from "../types";
import { SearchModal } from "./SearchModal";
import { useI18n } from "../i18n/I18nContext";
import { Icon } from "./Icon";

// A1: the global search entry lives ON the top toolbar, not in the sidebar. It renders as a bare
// magnifier (the toolbar is tight); clicking expands it into a real input, auto-focuses, and
// typing opens the command-palette SearchModal. Clicking outside / blur collapses it back to an
// icon. Keeping the icon-only default means the topbar never grows a wide box on every screen.

export function TopbarSearch({
  sessions,
  personas,
  onSelect,
}: {
  sessions: SessionInfo[];
  personas?: Persona[];
  onSelect: (id: string, workspace: string, agent: string) => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [palette, setPalette] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-focus when the box expands.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Collapse on outside interaction (blur is unreliable for a shell that may open a palette).
  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDocDown);
    return () => document.removeEventListener("pointerdown", onDocDown);
  }, [open]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      // Escape first closes the palette (if open), then collapses the box.
      if (palette) setPalette(false);
      else {
        setOpen(false);
        setQuery("");
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      setPalette(true);
    }
  };

  return (
    <div className="topbar-search" ref={wrapRef} data-testid="topbar-search">
      {open ? (
        <div className="topbar-search-box">
          <Icon name="search" size={14} className="topbar-search-ico" />
          <input
            ref={inputRef}
            className="topbar-search-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKey}
            placeholder={t("search.placeholder", undefined, "Search chats")}
          />
        </div>
      ) : (
        <button
          className="topbar-icon-btn"
          onClick={() => setOpen(true)}
          aria-label={t("common.search", undefined, "Search")}
          title={t("common.search", undefined, "Search")}
        >
          <Icon name="search" size={16} />
        </button>
      )}

      {palette && (
        <SearchModal
          sessions={sessions}
          personas={personas}
          onSelect={(id, ws, ag) => {
            setPalette(false);
            setOpen(false);
            setQuery("");
            onSelect(id, ws, ag);
          }}
          onClose={() => setPalette(false)}
        />
      )}
    </div>
  );
}
