//! Tool Lifecycle Execution Disposition (R3, ADR-035).
//!
//! The idempotency state machine is already Rust-authoritative (ADR-022);
//! this module owns the **disposition decision** that Python formerly made in
//! `core/tool_lifecycle.py` `_execute_sync`: for a given tool call, decide
//! whether it must *execute*, *replay* a committed result, or *surface* an
//! uncertain previous attempt — and, when it must execute, perform the
//! `record_planned` + `mark_executing` transitions that must atomically precede
//! the actual (Python-side) tool execution.
//!
//! Contract: docs/architecture/adr/ADR-035-r3-tool-lifecycle-hard-cut.md
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
pub fn plan(writer: &IdempotencyWriter, input: &ToolLifecyclePlanInput) -> Result<ToolLifecyclePlanOutput, ShadowReadError> {
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::idemlog::args_sha256;
    use tempfile::tempdir;

    fn input(db: &str, run_id: &str, call_id: &str, name: &str, args: Value) -> ToolLifecyclePlanInput {
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
        let out = plan(&writer, &input(db.to_str().unwrap(), "r1", "c1", "write_file", args)).unwrap();
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
        writer.record_planned("r1", "c1", "write_file", &args).unwrap();
        writer.mark_executing("r1", "c1").unwrap();
        let result = serde_json::json!({"ok": true});
        writer.commit("r1", "c1", "write_file", &args, &result).unwrap();

        let out = plan(&writer, &input(db.to_str().unwrap(), "r1", "c1", "write_file", args)).unwrap();
        assert!(matches!(out.action, PlanAction::Replay));
        assert_eq!(out.result, Some(result));
    }

    #[test]
    fn uncertain_call_surfaces() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let args = serde_json::json!({"to": "bob"});
        writer.record_planned("r2", "c2", "send_message", &args).unwrap();
        writer.mark_executing("r2", "c2").unwrap();
        writer.mark_uncertain("r2", "c2").unwrap();

        let out = plan(&writer, &input(db.to_str().unwrap(), "r2", "c2", "send_message", args)).unwrap();
        assert!(matches!(out.action, PlanAction::Uncertain));
        assert!(out.operation_id.is_some());
    }

    #[test]
    fn args_sha256_mismatch_is_not_replay() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("side_effects.db");
        let writer = IdempotencyWriter::open(&db).unwrap();

        let committed = serde_json::json!({"path": "a.txt"});
        writer.record_planned("r3", "c3", "write_file", &committed).unwrap();
        writer.mark_executing("r3", "c3").unwrap();
        writer.commit("r3", "c3", "write_file", &committed, &serde_json::json!({"ok": true})).unwrap();

        // Different args → different sha → not a replay; treated as execute.
        let different = serde_json::json!({"path": "b.txt"});
        let out = plan(&writer, &input(db.to_str().unwrap(), "r3", "c3", "write_file", different)).unwrap();
        assert!(matches!(out.action, PlanAction::Execute));
    }

    #[test]
    fn args_sha_is_stable_for_plan() {
        let a = args_sha256(&serde_json::json!({"b": 1, "a": 2}));
        let b = args_sha256(&serde_json::json!({"a": 2, "b": 1}));
        assert_eq!(a, b);
    }
}