import type { RootInfo } from "../api";
import { Icon } from "./Icon";
import { baseName } from "../paths";
import { useI18n } from "@delta/i18n/I18nContext";

// One directory row, shared by the composer popover and the session start panel. The primary is the
// session's bound workspace — the repo/folder for Code/Ops (shown by name), or a throwaway scratch
// for Cowork (shown as "Temporary space"). It's always read-write and can't be removed.
export function RootRow({
  root,
  busy,
  scratchPrimary,
  branch,
  onToggle,
  onRemove,
}: {
  root: RootInfo;
  busy?: boolean;
  scratchPrimary?: boolean;
  // The workspace's git branch — shown on the primary row (drawer's Working directories, §23).
  branch?: string | null;
  onToggle: (r: RootInfo) => void;
  onRemove: (path: string) => void;
}) {
  const { t } = useI18n();
  const label = root.primary
    ? scratchPrimary
      ? t("root.scratchPrimary", undefined, "Temporary space")
      : baseName(root.path)
    : root.label;
  return (
    <div className={"root-row" + (root.exists ? "" : " missing")}>
      <Icon name="folder" size={14} className="root-ico" />
      <span className="root-text" title={root.path}>
        <span className="root-label">
          {label}
          {root.primary && !scratchPrimary && <span className="root-tag">{t("root.primary", undefined, "main")}</span>}
          {branch && (
            <span className="root-tag root-branch">
              {" "}
              <Icon name="branch" size={11} /> {branch}
            </span>
          )}
        </span>
        <span className="root-path">{root.path}</span>
      </span>
      {!root.exists && <span className="root-tag warn">{t("root.missing", undefined, "missing")}</span>}
      <button
        className={"root-access" + (root.writable ? " rw" : " ro")}
        onClick={() => onToggle(root)}
        disabled={busy || root.primary}
        title={root.primary ? t("root.primaryAlwaysRW", undefined, "The main workspace is always read-write") : t("root.toggleAccessTitle", undefined, "Toggle read-only / read-write")}
      >
        {root.writable ? t("root.readWrite", undefined, "Read-write") : t("root.readOnly", undefined, "Read-only")}
      </button>
      {!root.primary && (
        <button className="root-x" onClick={() => onRemove(root.path)} disabled={busy} title={t("root.remove", undefined, "Remove")}>
          ×
        </button>
      )}
    </div>
  );
}
