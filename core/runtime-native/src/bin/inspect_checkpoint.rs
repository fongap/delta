//! inspect_checkpoint — read and validate a recovery snapshot from
//! a JSON file.
//!
//! Usage:
//!   inspect_checkpoint <snapshot.json>
//!
//! Exit codes:
//!   0 — success (snapshot parsed and schema validated)
//!   1 — parse / validation failure
//!   2 — usage error

use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use delta_runtime_native::{read_snapshot_file, SNAPSHOT_SCHEMA_VERSION};
use serde_json::json;

fn main() -> ExitCode {
    let path = match env::args().nth(1) {
        Some(p) => p,
        None => {
            eprintln!("usage: inspect_checkpoint <snapshot.json>");
            return ExitCode::from(2);
        }
    };

    let p = PathBuf::from(&path);
    let snapshot = match read_snapshot_file(&p) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::from(1);
        }
    };

    let report = json!({
        "path": path,
        "schema": snapshot.schema,
        "expected_schema": SNAPSHOT_SCHEMA_VERSION,
        "run_id": snapshot.run_id,
        "session_id": snapshot.session_id,
        "phase": snapshot.phase,
        "snapshot_at": snapshot.snapshot_at,
        "last_event_seq": snapshot.last_event_seq,
        "pending_inbox_item_id": snapshot.pending_inbox_item_id,
        "pending_tool_call": snapshot.pending_tool_call,
        "todo_count": snapshot.todo_count,
        "recent_artifact_count": snapshot.recent_artifact_count,
        "error": snapshot.error,
    });
    println!("{}", serde_json::to_string_pretty(&report).unwrap());
    ExitCode::SUCCESS
}
