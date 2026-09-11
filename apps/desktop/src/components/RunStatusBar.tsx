/**
 * R5.1 C3 — Lightweight run status bar.
 *
 * Shows human-readable status during an active run:
 *   "正在分析 3 个文件"
 *   "正在生成报告"
 *   "等待你的确认"
 *   "已应用你的修改"
 *
 * Technical details (tool calls, ledger, retries) are hidden by default
 * and expandable by the user. This is NOT a workflow diagram.
 *
 * The status text comes from real runtime events (C4: Timeline must
 * come from real ledger/runtime events, not a fake UI state history).
 */

import { useState } from "react";

export interface RunStatusProps {
  /** Human-readable status text from runtime events */
  status: string;
  /** Whether the run is currently active */
  active: boolean;
  /** Optional: expandable detail lines from runtime events */
  details?: string[];
}

export function RunStatusBar({ status, active, details }: RunStatusProps) {
  const [expanded, setExpanded] = useState(false);

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
        cursor: details && details.length > 0 ? "pointer" : "default",
      }}
      onClick={() => details && details.length > 0 && setExpanded(!expanded)}
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
      <span style={{ flex: 1 }}>{status}</span>
      {details && details.length > 0 && (
        <span style={{ fontSize: "11px", opacity: 0.6 }}>
          {expanded ? "收起" : `${details.length} 条详情`}
        </span>
      )}
      {expanded && details && (
        <div
          style={{
            position: "absolute",
            marginTop: "24px",
            background: "var(--bg-paper, #fff)",
            border: "1px solid var(--border-line, #e0e0e0)",
            borderRadius: "6px",
            padding: "8px 12px",
            fontSize: "12px",
            color: "var(--text-secondary, #888)",
            maxHeight: "200px",
            overflowY: "auto",
            zIndex: 10,
            boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
          }}
        >
          {details.map((d, i) => (
            <div key={i} style={{ padding: "2px 0" }}>
              {d}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
