//! inspect_validation — evaluate validation criteria against a list
//! of artifacts using the Rust reimplementation of
//! ``core/validation.py:run_validation``.
//!
//! Reads a JSON object from stdin with shape:
//!
//! ```json
//! {
//!   "criteria": {...ValidationCriteria.to_dict()...},
//!   "artifacts": [...Artifact.to_dict()...],
//!   "workspace": "/abs/path/to/workspace" (optional),
//!   "valid_citation_count": 3 (optional)
//! }
//! ```
//!
//! Writes a JSON object to stdout with the ``ValidationResult.to_dict()`` shape.
//!
//! Exit codes:
//!   0 — success (the verdict is reported in stdout regardless of pass/fail)
//!   1 — parse / validation failure
//!   2 — usage error

use std::io::{self, Read};
use std::process::ExitCode;

use delta_runtime_native::run_validation;
use serde_json::{json, Value};

fn main() -> ExitCode {
    let mut text = String::new();
    if let Err(e) = io::stdin().read_to_string(&mut text) {
        eprintln!("error: stdin read: {e}");
        return ExitCode::from(2);
    }
    let input: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("error: json parse: {e}");
            return ExitCode::from(1);
        }
    };
    let criteria = match input.get("criteria") {
        Some(v) => v,
        None => {
            eprintln!("error: input must have 'criteria'");
            return ExitCode::from(1);
        }
    };
    let artifacts = input.get("artifacts").and_then(Value::as_array);
    let artifacts: Vec<Value> = artifacts.cloned().unwrap_or_default();
    let workspace = input
        .get("workspace")
        .and_then(Value::as_str)
        .map(std::path::Path::new);
    let valid_citation_count = input
        .get("valid_citation_count")
        .and_then(Value::as_u64)
        .map(|n| n as usize);

    let result = match run_validation(&artifacts, criteria, workspace, valid_citation_count) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::from(1);
        }
    };

    let checks: Vec<Value> = result
        .checks
        .iter()
        .map(|c| json!({"name": c.name, "ok": c.ok, "detail": c.detail}))
        .collect();
    let report = json!({
        "ok": result.ok,
        "checks": checks,
        "evidence": result.evidence,
    });
    println!("{}", serde_json::to_string_pretty(&report).unwrap());
    ExitCode::SUCCESS
}
