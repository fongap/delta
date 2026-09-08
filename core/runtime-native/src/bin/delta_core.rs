//! delta-core — long-running Rust Core process for the Delta Runtime.
//!
//! R1 (P1-E): unifies the per-write subprocess pattern into a single
//! host process. Reads line-delimited JSON commands from stdin and
//! writes one JSON response per line to stdout. The Python delegate
//! modules (`core/idemlog_delegate.py`, `core/ledger_delegate.py`,
//! `core/automation/store_delegate.py`) hold a persistent connection
//! to this process instead of spawning a fresh `write_idemlog` /
//! `write_ledger` / `write_tasks` subprocess for every command.
//!
//! Protocol (request):
//!
//! ```json
//! {"cmd": "<domain.action>", ...args}
//! ```
//!
//! Protocol (response): one line of JSON per request.
//!
//! ```json
//! {"ok": true, "result": {...}}  // success
//! {"ok": false, "error": "..."}  // failure
//! ```
//!
//! Startup handshake: the Python client sends a ``hello`` command
//! immediately after spawning the subprocess. The server replies
//! with its protocol version; a mismatch raises an error and the
//! subprocess is closed (fail-closed). This prevents the Python
//! runtime from silently talking to a stale or upgraded binary.
//!
//! ```json
//! {"cmd": "hello", "protocol_version": 1}
//! {"ok": true, "result": {"protocol_version": 1, "server": "delta_core"}}
//! ```
//!
//! Supported commands (R1 minimum):
//!
//! - `ledger.append` — append one event to ``run_events.db``.
//! - `idem.record_planned` / `idem.mark_executing` / `idem.commit` /
//!   `idem.mark_failed` / `idem.mark_uncertain` — state transitions
//!   on ``side_effects.db``.
//! - `task.save` / `task.delete` / `task.add_run` — identity writes
//!   to ``automation.db``.
//!
//! Design notes (R1):
//!
//! - This is a minimum host interface, not a JSON-RPC framework. No
//!   id negotiation, no streaming, no async. The Python side keeps
//!   a single subprocess open and round-robins commands.
//! - The existing per-operation CLI binaries (`write_idemlog`,
//!   `write_ledger`, `write_tasks`) remain in place as migration
//!   diagnostic tools. The new `delta-core` is the production
//!   writer.
//! - Each request opens (and holds) a connection to the named DB
//!   the first time. Subsequent requests reuse the same connection.
//!   This is the main performance win over per-subprocess invocation.

use std::collections::HashMap;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::sync::Mutex;

use delta_runtime_native::{
    args_sha256, operation_id, run_validation, validate_all, validate_source_citation,
    ArtifactInput, ArtifactRegistryWriter, IdempotencyWriter, LedgerWriter, SideEffectEntry,
    SideEffectState, TaskStoreWriter,
};
use serde::Deserialize;
use serde_json::Value;

/// Delta Core wire-protocol version.
///
/// Bump this whenever the JSON command surface changes in a way
/// that would break an older client (added/removed/renamed fields,
/// changed semantics of existing fields, new required fields). The
/// Python client sends its expected version in the ``hello`` command
/// immediately after subprocess startup; a mismatch raises
/// :class:`DeltaCoreError` (fail-closed) so we never silently talk to
/// an incompatible binary.
const PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Deserialize)]
#[serde(tag = "cmd")]
enum Command {
    #[serde(rename = "ledger.append")]
    LedgerAppend {
        db: String,
        run_id: String,
        #[serde(rename = "type")]
        event_type: String,
        actor: Option<String>,
        ts: Option<f64>,
        payload: Option<Value>,
        workspace: Option<String>,
    },
    #[serde(rename = "idem.identify")]
    IdemIdentify {
        run_id: String,
        tool_call_id: String,
        #[serde(default)]
        args: Value,
    },
    #[serde(rename = "idem.record_planned")]
    IdemRecordPlanned {
        db: String,
        run_id: String,
        tool_call_id: String,
        tool_name: String,
        #[serde(default)]
        args: Value,
    },
    #[serde(rename = "idem.mark_executing")]
    IdemMarkExecuting {
        db: String,
        run_id: String,
        tool_call_id: String,
    },
    #[serde(rename = "idem.commit")]
    IdemCommit {
        db: String,
        run_id: String,
        tool_call_id: String,
        tool_name: String,
        #[serde(default)]
        args: Value,
        #[serde(default)]
        result: Value,
    },
    #[serde(rename = "idem.mark_failed")]
    IdemMarkFailed {
        db: String,
        run_id: String,
        tool_call_id: String,
        error: String,
    },
    #[serde(rename = "idem.mark_uncertain")]
    IdemMarkUncertain {
        db: String,
        run_id: String,
        tool_call_id: String,
    },
    #[serde(rename = "idem.initialize")]
    IdemInitialize { db: String },
    #[serde(rename = "idem.lookup")]
    IdemLookup {
        db: String,
        run_id: String,
        tool_call_id: String,
        #[serde(default)]
        args: Value,
    },
    #[serde(rename = "idem.get")]
    IdemGet {
        db: String,
        run_id: String,
        tool_call_id: String,
    },
    #[serde(rename = "idem.list")]
    IdemList {
        db: String,
        run_id: String,
        view: String,
    },
    #[serde(rename = "idem.sweep_stale")]
    IdemSweepStale {
        db: String,
        interrupted_run_ids: Vec<String>,
    },
    #[serde(rename = "idem.resolve_uncertain")]
    IdemResolveUncertain {
        db: String,
        run_id: String,
        tool_call_id: String,
        resolution: String,
        #[serde(default)]
        result: Value,
    },
    #[serde(rename = "task.save")]
    TaskSave {
        db: String,
        task_id: String,
        enabled: bool,
        next_run: Option<f64>,
        data: String,
    },
    #[serde(rename = "task.delete")]
    TaskDelete { db: String, task_id: String },
    #[serde(rename = "task.add_run")]
    TaskAddRun {
        db: String,
        run_id: String,
        task_id: String,
        started_at: f64,
        data: String,
        workspace: String,
    },
    #[serde(rename = "ping")]
    Ping {},
    #[serde(rename = "hello")]
    Hello { protocol_version: u32 },
    /// R2 / PR132: register one artifact. Appends `artifact.registered`
    /// and (if not incomplete) `artifact.completed` events to the run
    /// ledger, mirroring `core/artifact.py:register_artifact`.
    #[serde(rename = "artifact.register")]
    ArtifactRegister {
        db: String,
        path: String,
        name: String,
        kind: String,
        size: i64,
        modified_at: f64,
        run_id: String,
        sha256: String,
        incomplete: bool,
        registered_at: f64,
        ts: Option<f64>,
        workspace: Option<String>,
    },
    /// R2.1: evaluate a citation against a compatibility SourceRef snapshot.
    #[serde(rename = "citation.validate")]
    CitationValidate {
        source: Option<Value>,
        range: Value,
        workspace: Option<String>,
    },
    /// R2.1: canonicalize candidate ranges before persistence.
    #[serde(rename = "citation.canonicalize")]
    CitationCanonicalize {
        #[serde(default)]
        ranges: Vec<Value>,
    },
    /// R2.2: evaluate the deterministic completion contract in Rust.
    #[serde(rename = "validation.run")]
    ValidationRun {
        criteria: Value,
        #[serde(default)]
        artifacts: Vec<Value>,
        workspace: Option<String>,
        valid_citation_count: Option<usize>,
    },
}

struct ConnCache {
    ledgers: HashMap<PathBuf, LedgerWriter>,
    idems: HashMap<PathBuf, IdempotencyWriter>,
    tasks: HashMap<PathBuf, TaskStoreWriter>,
}

impl ConnCache {
    fn new() -> Self {
        Self {
            ledgers: HashMap::new(),
            idems: HashMap::new(),
            tasks: HashMap::new(),
        }
    }

    fn ledger(&mut self, db: &str) -> Result<&mut LedgerWriter, String> {
        let path = PathBuf::from(db);
        if !self.ledgers.contains_key(&path) {
            let w = LedgerWriter::open(&path).map_err(|e| e.to_string())?;
            self.ledgers.insert(path.clone(), w);
        }
        Ok(self.ledgers.get_mut(&path).unwrap())
    }

    fn idem(&mut self, db: &str) -> Result<&mut IdempotencyWriter, String> {
        let path = PathBuf::from(db);
        if !self.idems.contains_key(&path) {
            let w = IdempotencyWriter::open(&path).map_err(|e| e.to_string())?;
            self.idems.insert(path.clone(), w);
        }
        Ok(self.idems.get_mut(&path).unwrap())
    }

    fn task(&mut self, db: &str) -> Result<&mut TaskStoreWriter, String> {
        let path = PathBuf::from(db);
        if !self.tasks.contains_key(&path) {
            let w = TaskStoreWriter::open(&path).map_err(|e| e.to_string())?;
            self.tasks.insert(path.clone(), w);
        }
        Ok(self.tasks.get_mut(&path).unwrap())
    }
}

fn err(s: String) -> Value {
    serde_json::json!({"ok": false, "error": s})
}

fn lookup_entry_json(entry: SideEffectEntry) -> Value {
    match entry.state {
        SideEffectState::Uncertain => serde_json::json!({
            "tool_name": entry.tool_name,
            "result": Value::Null,
            "state": "uncertain",
            "operation_id": entry.operation_id,
            "committed_at": entry.committed_at,
        }),
        SideEffectState::Committed => serde_json::json!({
            "tool_name": entry.tool_name,
            "result": entry.result,
            "state": "committed",
            "operation_id": entry.operation_id,
            "committed_at": entry.committed_at,
        }),
        _ => Value::Null,
    }
}

fn raw_entry_json(entry: SideEffectEntry) -> Value {
    serde_json::json!({
        "run_id": entry.run_id,
        "tool_call_id": entry.tool_call_id,
        "tool_name": entry.tool_name,
        "args_sha256": entry.args_sha256,
        "result": entry.result,
        "state": entry.state.as_str(),
        "operation_id": entry.operation_id,
        "committed_at": entry.committed_at,
        "updated_at": entry.updated_at,
    })
}

fn listed_entry_json(entry: SideEffectEntry, view: &str) -> Value {
    match view {
        "uncommitted" => serde_json::json!({
            "tool_call_id": entry.tool_call_id,
            "tool_name": entry.tool_name,
            "state": entry.state.as_str(),
            "operation_id": entry.operation_id,
            "updated_at": entry.updated_at,
        }),
        "uncertain" => serde_json::json!({
            "tool_call_id": entry.tool_call_id,
            "tool_name": entry.tool_name,
            "operation_id": entry.operation_id,
            "updated_at": entry.updated_at,
        }),
        "committed" => serde_json::json!({
            "tool_call_id": entry.tool_call_id,
            "tool_name": entry.tool_name,
            "args_sha256": entry.args_sha256,
            "result": entry.result,
            "operation_id": entry.operation_id,
            "committed_at": entry.committed_at,
        }),
        "swept" => serde_json::json!({
            "run_id": entry.run_id,
            "tool_call_id": entry.tool_call_id,
            "tool_name": entry.tool_name,
            "operation_id": entry.operation_id,
        }),
        _ => Value::Null,
    }
}

fn handle(cmd: Command, cache: &Mutex<ConnCache>) -> Value {
    let mut cache = cache.lock().unwrap();
    let result: Result<Value, String> = match cmd {
        Command::LedgerAppend {
            db,
            run_id,
            event_type,
            actor,
            ts,
            payload,
            workspace,
        } => {
            let writer = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let ts = ts.unwrap_or_else(|| {
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs_f64())
                    .unwrap_or(0.0)
            });
            let actor = actor.unwrap_or_else(|| "system".to_string());
            let payload = payload.unwrap_or(Value::Null);
            let workspace = workspace.unwrap_or_default();
            match writer.append(&run_id, &event_type, &actor, ts, &payload, &workspace) {
                Ok(v) => Ok(v),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemIdentify {
            run_id,
            tool_call_id,
            args,
        } => Ok(serde_json::json!({
            "args_sha256": args_sha256(&args),
            "operation_id": operation_id(&run_id, &tool_call_id),
        })),
        Command::IdemInitialize { db } => match cache.idem(&db) {
            Ok(_) => Ok(serde_json::json!({"initialized": true})),
            Err(e) => Err(e),
        },
        Command::IdemLookup {
            db,
            run_id,
            tool_call_id,
            args,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.lookup(&run_id, &tool_call_id, &args) {
                Ok(Some(entry)) => Ok(lookup_entry_json(entry)),
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemGet {
            db,
            run_id,
            tool_call_id,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.get(&run_id, &tool_call_id) {
                Ok(Some(entry)) => Ok(raw_entry_json(entry)),
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemList { db, run_id, view } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let entries = match view.as_str() {
                "uncommitted" => writer.uncommitted_for_run(&run_id),
                "uncertain" => writer.uncertain_for_run(&run_id),
                "committed" => writer.committed_for_run(&run_id),
                _ => return err(format!("unknown idempotency list view: {view}")),
            };
            match entries {
                Ok(items) => Ok(Value::Array(
                    items
                        .into_iter()
                        .map(|entry| listed_entry_json(entry, &view))
                        .collect(),
                )),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemSweepStale {
            db,
            interrupted_run_ids,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.sweep_stale(&interrupted_run_ids) {
                Ok(items) => Ok(Value::Array(
                    items
                        .into_iter()
                        .map(|entry| listed_entry_json(entry, "swept"))
                        .collect(),
                )),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemResolveUncertain {
            db,
            run_id,
            tool_call_id,
            resolution,
            result,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.resolve_uncertain(&run_id, &tool_call_id, &resolution, &result) {
                Ok(_) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemRecordPlanned {
            db,
            run_id,
            tool_call_id,
            tool_name,
            args,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.record_planned(&run_id, &tool_call_id, &tool_name, &args) {
                Ok(op_id) => Ok(serde_json::json!({"operation_id": op_id})),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemMarkExecuting {
            db,
            run_id,
            tool_call_id,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.mark_executing(&run_id, &tool_call_id) {
                Ok(_) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemCommit {
            db,
            run_id,
            tool_call_id,
            tool_name,
            args,
            result,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.commit(&run_id, &tool_call_id, &tool_name, &args, &result) {
                Ok(_) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemMarkFailed {
            db,
            run_id,
            tool_call_id,
            error,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.mark_failed(&run_id, &tool_call_id, &error) {
                Ok(_) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::IdemMarkUncertain {
            db,
            run_id,
            tool_call_id,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.mark_uncertain(&run_id, &tool_call_id) {
                Ok(_) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskSave {
            db,
            task_id,
            enabled,
            next_run,
            data,
        } => {
            let writer = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.save_task(&task_id, enabled, next_run, &data) {
                Ok(_) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskDelete { db, task_id } => {
            let writer = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.delete_task(&task_id) {
                Ok(_) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskAddRun {
            db,
            run_id,
            task_id,
            started_at,
            data,
            workspace,
        } => {
            let writer = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.add_run(&run_id, &task_id, started_at, &data, &workspace) {
                Ok(_) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::Ping {} => Ok(serde_json::json!({"pong": true})),
        Command::Hello { protocol_version } => {
            if protocol_version != PROTOCOL_VERSION {
                Err(format!(
                    "protocol mismatch: client={protocol_version}, server={PROTOCOL_VERSION}"
                ))
            } else {
                Ok(serde_json::json!({
                    "protocol_version": PROTOCOL_VERSION,
                    "server": "delta_core",
                }))
            }
        }
        Command::ArtifactRegister {
            db,
            path,
            name,
            kind,
            size,
            modified_at,
            run_id,
            sha256,
            incomplete,
            registered_at,
            ts,
            workspace,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let artifact = ArtifactInput {
                path,
                name,
                kind,
                size,
                modified_at,
                run_id,
                sha256,
                incomplete,
                registered_at,
            };
            let writer = ArtifactRegistryWriter::new(ledger);
            // Default ts to 0.0; LedgerWriter doesn't need a real ts
            // for hash correctness (it serializes whatever is given).
            match writer.register(
                &artifact,
                ts.unwrap_or(0.0),
                workspace.as_deref().unwrap_or(""),
            ) {
                Ok(result) => {
                    let completed_present = result.completed.is_some();
                    Ok(serde_json::json!({
                        "registered": result.registered,
                        "completed_present": completed_present,
                    }))
                }
                Err(e) => Err(e.to_string()),
            }
        }
        Command::CitationValidate {
            source,
            range,
            workspace,
        } => serde_json::to_value(validate_source_citation(
            source.as_ref(),
            &range,
            workspace.as_deref().map(std::path::Path::new),
        ))
        .map_err(|error| error.to_string()),
        Command::CitationCanonicalize { ranges } => validate_all(&ranges).map(|validated| {
            Value::Array(
                validated
                    .into_iter()
                    .map(|citation| citation.range)
                    .collect(),
            )
        }),
        Command::ValidationRun {
            criteria,
            artifacts,
            workspace,
            valid_citation_count,
        } => run_validation(
            &artifacts,
            &criteria,
            workspace.as_deref().map(std::path::Path::new),
            valid_citation_count,
        )
        .and_then(|result| serde_json::to_value(result).map_err(|error| error.to_string())),
    };
    match result {
        Ok(v) => serde_json::json!({"ok": true, "result": v}),
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

fn main() -> std::process::ExitCode {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    let cache = Mutex::new(ConnCache::new());

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let cmd: Command = match serde_json::from_str(trimmed) {
            Ok(c) => c,
            Err(e) => {
                let resp = serde_json::json!({"ok": false, "error": format!("parse: {e}")});
                writeln!(out, "{resp}").ok();
                out.flush().ok();
                continue;
            }
        };
        let resp = handle(cmd, &cache);
        writeln!(out, "{resp}").ok();
        out.flush().ok();
    }
    std::process::ExitCode::SUCCESS
}
