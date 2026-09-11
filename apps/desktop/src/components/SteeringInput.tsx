/**
 * R5.1 C2/C3 — Steering input that appears when a run is active.
 *
 * Shows three natural-language actions instead of requiring users
 * to understand internal "Steer" terminology:
 *
 *   [立即调整当前任务]  → Steer
 *   [任务完成后继续]    → Follow-up
 *   [停止任务]          → Cancel
 *
 * The input box is always available during a run (C2: Active Run
 * 输入始终可用). The user can type and choose one of the three actions.
 */

import { useState } from "react";

export interface SteeringInputProps {
  active: boolean;
  onSteer: (text: string) => void;
  onFollowUp: (text: string) => void;
  onCancel: () => void;
}

export function SteeringInput({
  active,
  onSteer,
  onFollowUp,
  onCancel,
}: SteeringInputProps) {
  const [text, setText] = useState("");
  const [showActions, setShowActions] = useState(false);

  if (!active) return null;

  const hasText = text.trim().length > 0;

  return (
    <div
      className="steering-input"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "6px",
        padding: "4px 12px",
        borderTop: "1px solid var(--border-line, #e0e0e0)",
      }}
    >
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onFocus={() => setShowActions(true)}
        onBlur={() => setTimeout(() => setShowActions(false), 200)}
        placeholder="输入调整或后续要求..."
        style={{
          flex: 1,
          border: "none",
          outline: "none",
          background: "transparent",
          fontSize: "13px",
          color: "var(--text-primary, #333)",
        }}
      />
      {showActions && hasText && (
        <>
          <button
            className="btn small accent"
            onMouseDown={(e) => {
              e.preventDefault();
              onSteer(text);
              setText("");
            }}
            style={{ fontSize: "12px", padding: "2px 10px" }}
          >
            立即调整
          </button>
          <button
            className="btn small"
            onMouseDown={(e) => {
              e.preventDefault();
              onFollowUp(text);
              setText("");
            }}
            style={{ fontSize: "12px", padding: "2px 10px" }}
          >
            完成后继续
          </button>
        </>
      )}
      <button
        className="btn small danger"
        onClick={() => {
          onCancel();
          setText("");
        }}
        style={{ fontSize: "12px", padding: "2px 10px" }}
      >
        停止
      </button>
    </div>
  );
}
