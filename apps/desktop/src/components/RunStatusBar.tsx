/**
 * Lightweight active-run status.
 *
 * The label is derived from events the desktop is actually receiving (reasoning,
 * streaming, or generic execution). It deliberately does not fabricate a
 * timeline or technical detail history.
 */

export interface RunStatusProps {
  status: string;
  active: boolean;
}

export function RunStatusBar({ status, active }: RunStatusProps) {
  if (!active) return null;

  return (
    <div
      className="run-status-bar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "4px 12px",
        fontSize: "13px",
        color: "var(--text-secondary, #666)",
        background: "var(--bg-subtle, #f5f5f5)",
        borderBottom: "1px solid var(--border-line, #e0e0e0)",
      }}
      aria-live="polite"
    >
      <span
        className="run-status-dot"
        style={{
          width: "6px",
          height: "6px",
          borderRadius: "50%",
          background: "var(--accent, #3b82f6)",
          animation: "pulse 1.5s ease-in-out infinite",
          flexShrink: 0,
        }}
      />
      <span>{status}</span>
    </div>
  );
}
