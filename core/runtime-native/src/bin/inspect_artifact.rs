//! inspect_artifact — list artifacts recorded for a run in a
//! run_events.db and (optionally) verify their on-disk sha256 against
//! a workspace.
//!
//! Usage:
//!   inspect_artifact --db <path> --run-id <id> [--workspace <dir>]
//!
//! Exit codes:
//!   0 — success (mismatches are reported in the JSON output, not as errors)
//!   1 — open / read failure
//!   2 — usage error

use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use delta_runtime_native::ArtifactReader;
use serde_json::json;

fn main() -> ExitCode {
    let mut db: Option<String> = None;
    let mut run_id: Option<String> = None;
    let mut workspace: Option<String> = None;

    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--db" => db = args.next(),
            "--run-id" => run_id = args.next(),
            "--workspace" => workspace = args.next(),
            _ => {}
        }
    }

    let db = match db {
        Some(p) => p,
        None => {
            eprintln!("usage: inspect_artifact --db <path> --run-id <id> [--workspace <dir>]");
            return ExitCode::from(2);
        }
    };
    let run_id = match run_id {
        Some(r) => r,
        None => {
            eprintln!("usage: inspect_artifact --db <path> --run-id <id> [--workspace <dir>]");
            return ExitCode::from(2);
        }
    };

    let reader = match ArtifactReader::open(&db) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("error: open {db}: {e}");
            return ExitCode::from(1);
        }
    };

    let records = match reader.list_for_run(&run_id) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("error: list: {e}");
            return ExitCode::from(1);
        }
    };

    let mut out_records: Vec<serde_json::Value> = records
        .iter()
        .map(|r| {
            json!({
                "path": r.path,
                "kind": r.kind,
                "size": r.size,
                "sha256": r.sha256,
                "incomplete": r.incomplete,
            })
        })
        .collect();

    let mut mismatches_out: Vec<serde_json::Value> = Vec::new();
    if let Some(ws) = workspace.as_ref() {
        let ws_path = PathBuf::from(ws);
        match reader.verify_against_workspace(&run_id, &ws_path) {
            Ok(mismatches) => {
                for m in mismatches {
                    mismatches_out.push(json!({
                        "path": m.path,
                        "recorded_sha256": m.recorded_sha256,
                        "on_disk_sha256": m.on_disk_sha256,
                        "reason": m.reason,
                    }));
                }
            }
            Err(e) => {
                eprintln!("error: verify: {e}");
                return ExitCode::from(1);
            }
        }
    }

    // Sort records by path for deterministic output.
    out_records.sort_by(|a, b| {
        a.get("path")
            .and_then(|v| v.as_str())
            .cmp(&b.get("path").and_then(|v| v.as_str()))
    });

    let report = json!({
        "run_id": run_id,
        "db": db,
        "workspace": workspace,
        "artifact_count": out_records.len(),
        "mismatch_count": mismatches_out.len(),
        "artifacts": out_records,
        "mismatches": mismatches_out,
    });
    println!("{}", serde_json::to_string_pretty(&report).unwrap());
    ExitCode::SUCCESS
}
