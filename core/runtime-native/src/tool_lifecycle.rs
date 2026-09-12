//! Tool Lifecycle Execution Disposition (R3, ADR-035 + ADR-037).
//!
//! The idempotency state machine is already Rust-authoritative (ADR-022);
//! this module owns the **disposition decision** that Python formerly made in
//! `core/tool_lifecycle.py` `_execute_sync`: for a given tool call, decide
//! whether it must *execute*, *replay* a committed result, or *surface* an
//! uncertain previous attempt — and, when it must execute, perform the
//! `record_planned` + `mark_executing` transitions that must atomically precede
//! the actual (Python-side) tool execution.
//!
//! ADR-037 extends this with **cancellation decision authority**: when a tool
//! call is cancelled after execution started (side effect may or may not have
//! occurred), Rust decides the lifecycle state — Uncertain, never Failed,
//! unless the side effect provably did not start.
//!
//! Contract: docs/architecture/adr/ADR-035-r3-tool-lifecycle-hard-cut.md
//! and docs/architecture/adr/ADR-037-r3-cancellation-decision-authority.md
//! and core/tool_lifecycle.py (Python facade mirrors this).

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::idemlog::IdempotencyWriter;
use crate::ShadowReadError;

/// Input for a tool-lifecycle execution disposition request.
#[derive(Debug, Clone, Deserialize)]
pub struct ToolLifecyclePlanInput {
    pub db: String,
    pub run_id: String,
    pub tool_call_id: String,
    pub tool_name: String,
    #[serde(default)]
    pub args: Value,
}

/// The disposition Rust returns for a tool call.
#[derive(Debug, Clone, Serialize)]
pub enum PlanAction {
    /// The tool must execute now; `record_planned` + `mark_executing` already
    /// transitioned the row. Python runs `registry.execute` and reports back.
    #[serde(rename = "execute")]
    Execute,
    /// A committed result exists for these args; Python reuses it verbatim.
    #[serde(rename = "replay")]
    Replay,
    /// A prior attempt is uncertain; Python surfaces it for user resolution.
    #[serde(rename = "uncertain")]
    Uncertain,
}

/// Output of a `toollifecycle.plan` disposition.
#[derive(Debug, Clone, Serialize)]
pub struct ToolLifecyclePlanOutput {
    pub action: PlanAction,
    /// For `replay`: the stored committed result.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    /// For `uncertain`: the error payload Python surfaces to the user.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Value>,
    /// For `uncertain`: the stable operation id for user resolution.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_id: Option<String>,
}

/// Decide a tool call's execution disposition against the idempotency log.
///
/// Pure re-use of `IdempotencyWriter`: no new table, no new persisted state.
/// One `record_planned` + one `mark_executing` per fresh execution, identical
/// to the previous Python path.
pub fn plan(
    writer: &IdempotencyWriter,
    input: &ToolLifecyclePlanInput,
) -> Result<ToolLifecyclePlanOutput, ShadowReadError> {
    if input.run_id.is_empty() || input.tool_call_id.is_empty() {
        // Without a run identity there is no idempotency authority; Python
        // falls back to direct execution (no state machine) exactly as before.
        return Ok(ToolLifecyclePlanOutput {
            action: PlanAction::Execute,
            result: None,
            error: None,
            operation_id: None,
        });
    }

    // 1. Replay / uncertain decision (mirrors IdempotencyWriter::lookup).
    if let Some(entry) = writer.lookup(&input.run_id, &input.tool_call_id, &input.args)? {
        match entry.state {
            crate::idemlog::SideEffectState::Uncertain => Ok(ToolLifecyclePlanOutput {
                action: PlanAction::Uncertain,
                result: None,
                error: Some(serde_json::json!({
                    "error": "side effect is uncertain — the previous run may or may not have executed it. User resolution required.",
                    "operation_id": entry.operation_id.clone(),
                })),
                operation_id: Some(entry.operation_id.clone()),
            }),
            crate::idemlog::SideEffectState::Committed => Ok(ToolLifecyclePlanOutput {
                action: PlanAction::Replay,
                result: Some(entry.result.clone()),
                error: None,
                operation_id: None,
            }),
            _ => Ok(ToolLifecyclePlanOutput {
                action: PlanAction::Execute,
                result: None,
                error: None,
                operation_id: None,
            }),
        }
    } else {
        // 2. No committed/uncertain entry → fresh execution. Transition to
        //    Planned then Executing atomically before Python runs the tool.
        writer.record_planned(
            &input.run_id,
            &input.tool_call_id,
            &input.tool_name,
            &input.args,
        )?;
        writer.mark_executing(&input.run_id, &input.tool_call_id)?;
        Ok(ToolLifecyclePlanOutput {
            action: PlanAction::Execute,
            result: None,
            error: None,
            operation_id: None,
        })
    }
}

/// Input for an interruption lifecycle decision (ADR-037 / ADR-038).
///
/// `reason` is an optional audit label that distinguishes the interruption
/// cause ("user_stop", "timeout", etc.) without affecting the state-machine
/// decision itself. The lifecycle transition is identical regardless of
/// reason: Executing → Uncertain, Planned → Failed, terminal → no-op.
#[derive(Debug, Clone, Deserialize)]
pub struct ToolLifecycleCancelInput {
    pub db: String,
    pub run_id: String,
    pub tool_call_id: String,
    pub tool_name: String,
    #[serde(default)]
    pub reason: Option<String>,
}

/// The lifecycle decision Rust returns when a tool call is cancelled.
#[derive(Debug, Clone, Serialize)]
pub enum CancelAction {
    /// The side effect was Executing — result unknown. Rust marked it
    /// Uncertain. Python must surface it for user resolution.
    #[serde(rename = "uncertain")]
    Uncertain,
    /// The side effect was Planned but never started executing — safe to
    /// mark Failed (nothing happened).
    #[serde(rename = "failed")]
    Failed,
    /// The side effect was already terminal (Committed/Failed/Uncertain).
    /// No transition needed; Python reuses the existing state.
    #[serde(rename = "terminal")]
    Terminal,
    /// No side-effect row exists for this call (no run identity or no
    /// idempotency log). Python decides locally.
    #[serde(rename = "none")]
    None,
}

/// Output of a `toollifecycle.cancel` decision.
#[derive(Debug, Clone, Serialize)]
pub struct ToolLifecycleCancelOutput {
    pub action: CancelAction,
    /// For `uncertain`: the stable operation id for user resolution.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_id: Option<String>,
}

/// Decide the lifecycle consequence of interrupting a tool call
/// (ADR-037 / ADR-038).
///
/// Authority rule:
/// - **Executing** → mark Uncertain (side effect may or may not have
///   occurred). Never Failed. Python surfaces this for user resolution.
/// - **Planned** → mark Failed (nothing executed, safe to fail).
/// - **Committed / Failed / Uncertain** → terminal, no transition.
/// - **No row** → no idempotency authority; Python decides locally.
///
/// `reason` (e.g. "user_stop", "timeout") is an audit label; it does
/// not change the state-machine decision, only the recorded failure
/// message when the state was Planned.
pub fn cancel(
    writer: &IdempotencyWriter,
    input: &ToolLifecycleCancelInput,
) -> Result<ToolLifecycleCancelOutput, ShadowReadError> {
    if input.run_id.is_empty() || input.tool_call_id.is_empty() {
        return Ok(ToolLifecycleCancelOutput {
            action: CancelAction::None,
            operation_id: None,
        });
    }

    let Some(entry) = writer.get(&input.run_id, &input.tool_call_id)? else {
        return Ok(ToolLifecycleCancelOutput {
            action: CancelAction::None,
            operation_id: None,
        });
    };

    let reason_label = input.reason.as_deref().unwrap_or("cancelled");

    match entry.state {
        crate::idemlog::SideEffectState::Executing => {
            // Side effect started but result unknown — MUST be Uncertain.
            writer.mark_uncertain(&input.run_id, &input.tool_call_id)?;
            Ok(ToolLifecycleCancelOutput {
                action: CancelAction::Uncertain,
                operation_id: Some(entry.operation_id.clone()),
            })
        }
        crate::idemlog::SideEffectState::Planned => {
            // Nothing executed yet — safe to mark Failed.
            let msg = format!("{reason_label} before execution");
            writer.mark_failed(&input.run_id, &input.tool_call_id, &msg)?;
            Ok(ToolLifecycleCancelOutput {
                action: CancelAction::Failed,
                operation_id: Some(entry.operation_id.clone()),
            })
        }
        // Already terminal — no transition needed.
        crate::idemlog::SideEffectState::Committed
        | crate::idemlog::SideEffectState::Failed
        | crate::idemlog::SideEffectState::Uncertain => Ok(ToolLifecycleCancelOutput {
            action: CancelAction::Terminal,
            operation_id: Some(entry.operation_id.clone()),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::idemlog::args_sha256;
    use tempfile::tempdir;

    fn input(
        db: &str,
        run_id: &str,
        call_id: &str,
        name: &str,
        args: Value,
    ) -> ToolLifecyclePlanInput {
        ToolLifecyclePlanInput {
            db: db.to_string(),
            run_id: run_id.to_string(),
            tool_call_id: call_id.to_string(),
            tool_name: name.to_string(),
            args,
        }
    }

    #[test]
    fn first_call_executes() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let args = serde_json::json!({"path": "a.txt"});
        let out = plan(
            &writer,
            &input(db.to_str().unwrap(), "r1", "c1", "write_file", args),
        )
        .unwrap();
        assert!(matches!(out.action, PlanAction::Execute));

        // The row is now Executing (transitioned ahead of Python execution).
        let entry = writer.get("r1", "c1").unwrap().unwrap();
        assert_eq!(entry.state, crate::idemlog::SideEffectState::Executing);
    }

    #[test]
    fn committed_call_replays() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let args = serde_json::json!({"path": "a.txt"});
        writer
            .record_planned("r1", "c1", "write_file", &args)
            .unwrap();
        writer.mark_executing("r1", "c1").unwrap();
        let result = serde_json::json!({"ok": true});
        writer
            .commit("r1", "c1", "write_file", &args, &result)
            .unwrap();

        let out = plan(
            &writer,
            &input(db.to_str().unwrap(), "r1", "c1", "write_file", args),
        )
        .unwrap();
        assert!(matches!(out.action, PlanAction::Replay));
        assert_eq!(out.result, Some(result));
    }

    #[test]
    fn uncertain_call_surfaces() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let args = serde_json::json!({"to": "bob"});
        writer
            .record_planned("r2", "c2", "send_message", &args)
            .unwrap();
        writer.mark_executing("r2", "c2").unwrap();
        writer.mark_uncertain("r2", "c2").unwrap();

        let out = plan(
            &writer,
            &input(db.to_str().unwrap(), "r2", "c2", "send_message", args),
        )
        .unwrap();
        assert!(matches!(out.action, PlanAction::Uncertain));
        assert!(out.operation_id.is_some());
    }

    #[test]
    fn args_sha256_mismatch_is_not_replay() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let committed = serde_json::json!({"path": "a.txt"});
        writer
            .record_planned("r3", "c3", "write_file", &committed)
            .unwrap();
        writer.mark_executing("r3", "c3").unwrap();
        writer
            .commit(
                "r3",
                "c3",
                "write_file",
                &committed,
                &serde_json::json!({"ok": true}),
            )
            .unwrap();

        // AF-10: same (run_id, tool_call_id) with different args is an
        // identity collision — must NOT be treated as a fresh execute.
        // The system must fail-closed instead of silently overwriting
        // the committed result.
        let different = serde_json::json!({"path": "b.txt"});
        let result = plan(
            &writer,
            &input(db.to_str().unwrap(), "r3", "c3", "write_file", different),
        );
        assert!(result.is_err(), "identity collision must fail-closed");
        let err_msg = result.unwrap_err().to_string();
        assert!(
            err_msg.contains("identity_collision"),
            "error must mention identity_collision: {err_msg}"
        );
    }

    #[test]
    fn args_sha_is_stable_for_plan() {
        let a = args_sha256(&serde_json::json!({"b": 1, "a": 2}));
        let b = args_sha256(&serde_json::json!({"a": 2, "b": 1}));
        assert_eq!(a, b);
    }

    // -- ADR-037: Cancellation decision authority tests --

    fn cancel_input(db: &str, run_id: &str, call_id: &str, name: &str) -> ToolLifecycleCancelInput {
        ToolLifecycleCancelInput {
            db: db.to_string(),
            run_id: run_id.to_string(),
            tool_call_id: call_id.to_string(),
            tool_name: name.to_string(),
            reason: None,
        }
    }

    #[test]
    fn cancel_executing_marks_uncertain() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let args = serde_json::json!({"path": "a.txt"});
        writer
            .record_planned("r1", "c1", "write_file", &args)
            .unwrap();
        writer.mark_executing("r1", "c1").unwrap();

        // Cancel while executing — must be Uncertain, never Failed.
        let out = cancel(
            &writer,
            &cancel_input(db.to_str().unwrap(), "r1", "c1", "write_file"),
        )
        .unwrap();
        assert!(matches!(out.action, CancelAction::Uncertain));
        assert!(out.operation_id.is_some());

        // Verify the row is now Uncertain.
        let entry = writer.get("r1", "c1").unwrap().unwrap();
        assert_eq!(entry.state, crate::idemlog::SideEffectState::Uncertain);
    }

    #[test]
    fn cancel_planned_marks_failed() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let args = serde_json::json!({"path": "a.txt"});
        writer
            .record_planned("r2", "c2", "write_file", &args)
            .unwrap();
        // Note: NOT mark_executing — still Planned.

        let out = cancel(
            &writer,
            &cancel_input(db.to_str().unwrap(), "r2", "c2", "write_file"),
        )
        .unwrap();
        assert!(matches!(out.action, CancelAction::Failed));

        let entry = writer.get("r2", "c2").unwrap().unwrap();
        assert_eq!(entry.state, crate::idemlog::SideEffectState::Failed);
    }

    #[test]
    fn cancel_committed_is_terminal() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let args = serde_json::json!({"path": "a.txt"});
        writer
            .record_planned("r3", "c3", "write_file", &args)
            .unwrap();
        writer.mark_executing("r3", "c3").unwrap();
        writer
            .commit(
                "r3",
                "c3",
                "write_file",
                &args,
                &serde_json::json!({"ok": true}),
            )
            .unwrap();

        let out = cancel(
            &writer,
            &cancel_input(db.to_str().unwrap(), "r3", "c3", "write_file"),
        )
        .unwrap();
        assert!(matches!(out.action, CancelAction::Terminal));

        // State unchanged.
        let entry = writer.get("r3", "c3").unwrap().unwrap();
        assert_eq!(entry.state, crate::idemlog::SideEffectState::Committed);
    }

    #[test]
    fn cancel_nonexistent_row_is_none() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let out = cancel(
            &writer,
            &cancel_input(db.to_str().unwrap(), "r4", "c4", "write_file"),
        )
        .unwrap();
        assert!(matches!(out.action, CancelAction::None));
    }

    // -- ADR-038: Timeout decision authority tests --

    fn timeout_input(
        db: &str,
        run_id: &str,
        call_id: &str,
        name: &str,
    ) -> ToolLifecycleCancelInput {
        ToolLifecycleCancelInput {
            db: db.to_string(),
            run_id: run_id.to_string(),
            tool_call_id: call_id.to_string(),
            tool_name: name.to_string(),
            reason: Some("timeout".to_string()),
        }
    }

    #[test]
    fn timeout_executing_marks_uncertain() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let args = serde_json::json!({"path": "a.txt"});
        writer
            .record_planned("r1", "c1", "write_file", &args)
            .unwrap();
        writer.mark_executing("r1", "c1").unwrap();

        // Timeout while executing — must be Uncertain, never Failed.
        let out = cancel(
            &writer,
            &timeout_input(db.to_str().unwrap(), "r1", "c1", "write_file"),
        )
        .unwrap();
        assert!(matches!(out.action, CancelAction::Uncertain));

        let entry = writer.get("r1", "c1").unwrap().unwrap();
        assert_eq!(entry.state, crate::idemlog::SideEffectState::Uncertain);
    }

    #[test]
    fn timeout_planned_marks_failed_with_timeout_reason() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let args = serde_json::json!({"path": "a.txt"});
        writer
            .record_planned("r2", "c2", "write_file", &args)
            .unwrap();

        let out = cancel(
            &writer,
            &timeout_input(db.to_str().unwrap(), "r2", "c2", "write_file"),
        )
        .unwrap();
        assert!(matches!(out.action, CancelAction::Failed));

        let entry = writer.get("r2", "c2").unwrap().unwrap();
        assert_eq!(entry.state, crate::idemlog::SideEffectState::Failed);
        assert_eq!(entry.result["error"], "timeout before execution");
    }
}
