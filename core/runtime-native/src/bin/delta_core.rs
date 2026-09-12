//! delta-core — long-running Rust Core process for the Delta Runtime.
//!
//! R5.1 / AF-04/05/06: Multiplexed Runtime Protocol v16.
//!
//! Each request may carry an optional ``request_id: u64``. Responses and
//! stream frames echo it back so the Python demux client can route
//! concurrent in-flight requests without head-of-line blocking.
//!
//! Protocol (request):
//!
//! ```json
//! {"cmd": "<domain.action>", "request_id": 42, ...args}
//! ```
//!
//! Protocol (response):
//!
//! ```json
//! {"ok": true, "request_id": 42, "result": {...}}
//! {"ok": false, "request_id": 42, "error": "..."}
//! ```
//!
//! Streaming (v16): a ``start`` frame, zero or more ``delta`` frames,
//! and a terminal ``done`` (or ``error``) frame. Every frame carries
//! ``request_id``:
//!
//! ```json
//! {"ok": true, "request_id": 42, "stream": "start"}
//! {"ok": true, "request_id": 42, "stream": "delta", "data": {...}}
//! {"ok": true, "request_id": 42, "stream": "done", "result": {...}}
//! ```
//!
//! Cancellation (v16): ``request.cancel`` carries ``target_request_id``.
//! The server sets a cancel flag for the target stream; the stream thread
//! checks the flag at each delta boundary and exits early.
//!
//! Concurrency (v16): streaming commands run in a spawned thread so the
//! main stdin loop continues to read and dispatch other commands (ping,
//! ledger, task, etc.) while a provider stream is in-flight.

use std::collections::HashMap;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::sync::Mutex;

use delta_runtime_native::{
    args_sha256, classify, operation_id, run_validation, validate_source_citation,
    ApprovalRecordInput, ApprovalWriter, ArtifactInput, ArtifactRegistryWriter, CheckpointReader,
    CheckpointRegisterInput, CheckpointWriter, CitationValidationResult, CitationValidity,
    IdempotencyWriter, LedgerWriter, PolicyEvaluateInput, ProviderRequest, RetryClassifyInput,
    SideEffectEntry, SideEffectState, SourceCitationReader, SourceCitationWriter,
    SourceRegisterInput, TaskStore, ToolLifecycleCancelInput, ToolLifecyclePlanInput,
    ValidationReader, ValidationRegisterInput, ValidationWriter,
};
use serde::Deserialize;
use serde_json::Value;
use time::OffsetDateTime;

/// Delta Core wire-protocol version.
///
/// Bump this whenever the JSON command surface changes in a way
/// that would break an older client (added/removed/renamed fields,
/// changed semantics of existing fields, new required fields). The
/// Python client sends its expected version in the ``hello`` command
/// immediately after subprocess startup; a mismatch raises
/// :class:`DeltaCoreError` (fail-closed) so we never silently talk to
/// an incompatible binary.
const PROTOCOL_VERSION: u32 = 16;

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
    #[serde(rename = "ledger.events")]
    LedgerEvents { db: String, run_id: String },
    #[serde(rename = "ledger.events_in_workspace")]
    LedgerEventsInWorkspace {
        db: String,
        run_id: String,
        workspace: String,
    },
    #[serde(rename = "ledger.runs")]
    LedgerRuns { db: String },
    #[serde(rename = "ledger.open_runs")]
    LedgerOpenRuns { db: String },
    #[serde(rename = "ledger.run_status")]
    LedgerRunStatus { db: String, run_id: String },
    #[serde(rename = "ledger.verify")]
    LedgerVerify { db: String, run_id: String },
    #[serde(rename = "ledger.recover_stale")]
    LedgerRecoverStale { db: String },
    #[serde(rename = "ledger.close")]
    LedgerClose { db: String },
    #[serde(rename = "run.transition")]
    RunTransition {
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
    #[serde(rename = "task.get")]
    TaskGet { db: String, task_id: String },
    #[serde(rename = "task.list")]
    TaskList { db: String },
    #[serde(rename = "task.due")]
    TaskDue { db: String, now: f64 },
    #[serde(rename = "task.delete")]
    TaskDelete { db: String, task_id: String },
    #[serde(rename = "task.find_run")]
    TaskFindRun { db: String, run_id: String },
    #[serde(rename = "task.runs")]
    TaskRuns {
        db: String,
        task_id: String,
        limit: usize,
    },
    #[serde(rename = "task.task_for_run_session")]
    TaskForRunSession { db: String, session_id: String },
    #[serde(rename = "task.add_run")]
    TaskAddRun {
        db: String,
        run_id: String,
        task_id: String,
        started_at: f64,
        data: String,
        workspace: String,
    },
    #[serde(rename = "task.complete_run")]
    TaskCompleteRun {
        db: String,
        run_id: String,
        task_id: String,
        started_at: f64,
        run_data: String,
        workspace: String,
        finished_at: f64,
        next_run: Option<f64>,
    },
    #[serde(rename = "task.close")]
    TaskClose { db: String },
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
    /// R2.3: register (or re-register) a source. Returns the SourceRecord.
    #[serde(rename = "source.register")]
    SourceRegister {
        db: String,
        origin: String,
        location: String,
        fingerprint: String,
        captured_at: String,
        mtime_ns: Option<u64>,
        size_bytes: Option<u64>,
        permissions: Value,
        #[allow(dead_code)]
        run_id: String,
        ts: Option<f64>,
        workspace: Option<String>,
    },
    /// R2.3: get a source by ID.
    #[serde(rename = "source.get")]
    SourceGet { db: String, source_id: String },
    /// R2.3: list all sources.
    #[serde(rename = "source.list")]
    SourceList {
        db: String,
        origin: Option<String>,
        location: Option<String>,
        status: Option<String>,
    },
    /// R2.3: get the latest source for a location+origin.
    #[serde(rename = "source.latest")]
    SourceLatest {
        db: String,
        location: String,
        origin: String,
    },
    /// R2.3: batch refresh source status from Python observations.
    #[serde(rename = "source.refresh")]
    SourceRefresh {
        db: String,
        checks: Vec<Value>,
        ts: f64,
        workspace: String,
    },
    /// R2.3: mark a citation for a source.
    #[serde(rename = "citation.mark")]
    CitationMark {
        db: String,
        source_id: String,
        run_id: String,
        ranges: Vec<Value>,
        ts: Option<f64>,
        workspace: Option<String>,
    },
    /// R2.3: validate a citation against a source from the ledger.
    #[serde(rename = "citation.validate")]
    CitationValidate {
        db: String,
        source_id: String,
        range: Value,
        workspace: Option<String>,
    },
    /// R2.3: list all citations for a source.
    #[serde(rename = "citation.list")]
    CitationList { db: String, source_id: String },
    /// R2.2: evaluate the deterministic completion contract in Rust.
    #[serde(rename = "validation.run")]
    ValidationRun {
        criteria: Value,
        #[serde(default)]
        artifacts: Vec<Value>,
        workspace: Option<String>,
        valid_citation_count: Option<usize>,
    },
    /// R2.4: register validation criteria + result. Appends `validation.registered` to ledger.
    #[serde(rename = "validation.register")]
    ValidationRegister {
        db: String,
        run_id: String,
        criteria: Value,
        evaluated_at: String,
        result: Value,
        ts: Option<f64>,
        workspace: Option<String>,
    },
    /// R2.4: get a validation by ID.
    #[serde(rename = "validation.get")]
    ValidationGet { db: String, validation_id: String },
    /// R2.4: list all validations.
    #[serde(rename = "validation.list")]
    ValidationList { db: String, run_id: Option<String> },
    /// R2.4: get the latest validation for a run_id.
    #[serde(rename = "validation.latest")]
    ValidationLatest { db: String, run_id: String },
    /// R2.4: evaluate criteria against artifacts and persist the result.
    #[serde(rename = "validation.eval")]
    ValidationEval {
        db: String,
        run_id: String,
        criteria: Value,
        #[serde(default)]
        artifacts: Vec<Value>,
        workspace: String,
        valid_citation_count: Option<usize>,
        ts: Option<f64>,
    },
    /// R2.5: register a checkpoint (recovery snapshot).
    #[serde(rename = "checkpoint.register")]
    CheckpointRegister {
        db: String,
        #[serde(default)]
        checkpoint_id: Option<String>,
        run_id: String,
        session_id: String,
        phase: String,
        #[serde(default)]
        pending_tool_call: Option<Value>,
        #[serde(default)]
        pending_inbox_item_id: Option<String>,
        #[serde(default)]
        last_event_seq: Option<i64>,
        #[serde(default)]
        todo_summary: Vec<Value>,
        #[serde(default)]
        recent_artifacts: Vec<Value>,
        #[serde(default)]
        error: Option<String>,
        ts: Option<f64>,
        workspace: Option<String>,
    },
    /// R2.5: get a checkpoint by ID.
    #[serde(rename = "checkpoint.get")]
    CheckpointGet { db: String, checkpoint_id: String },
    /// R2.5: list checkpoints, optionally filtered by run_id / session_id.
    #[serde(rename = "checkpoint.list")]
    CheckpointList {
        db: String,
        run_id: Option<String>,
        session_id: Option<String>,
    },
    /// R2.5: get the latest checkpoint for a run_id.
    #[serde(rename = "checkpoint.latest")]
    CheckpointLatest { db: String, run_id: String },
    /// R2.5: validate a checkpoint by ID.
    #[serde(rename = "checkpoint.validate")]
    CheckpointValidate { db: String, checkpoint_id: String },
    /// R2.5: close the checkpoint DB handle in the cache.
    #[serde(rename = "checkpoint.close")]
    CheckpointClose { db: String },
    /// R2 / ADR-030: evaluate tool call policy (classify + enforce slices).
    #[serde(rename = "policy.evaluate")]
    PolicyEvaluate {
        tool_name: String,
        #[serde(default)]
        arguments: Option<Value>,
        #[serde(default)]
        metadata: Option<Value>,
        decision: Value,
        level: i64,
        workspace_root: String,
        #[serde(default)]
        roots: Vec<Value>,
    },
    /// R2 / ADR-030: classify a tool call into a risk level (L0–L4).
    #[serde(rename = "policy.classify")]
    PolicyClassify {
        tool_name: String,
        #[serde(default)]
        arguments: Option<Value>,
        #[serde(default)]
        metadata: Option<Value>,
    },
    /// R3 / ADR-035: decide a tool call's execution disposition and transition
    /// the idempotency row (record_planned + mark_executing) for fresh calls.
    #[serde(rename = "toollifecycle.plan")]
    ToolLifecyclePlan {
        db: String,
        run_id: String,
        tool_call_id: String,
        tool_name: String,
        #[serde(default)]
        args: Value,
    },
    /// R3 / ADR-037 / ADR-038: decide the lifecycle consequence of
    /// interrupting a tool call (user stop or timeout).
    /// Executing → Uncertain; Planned → Failed; terminal → no-op.
    /// `reason` ("user_stop", "timeout") is an audit label that does
    /// not change the state-machine decision.
    #[serde(rename = "toollifecycle.cancel")]
    ToolLifecycleCancel {
        db: String,
        run_id: String,
        tool_call_id: String,
        tool_name: String,
        #[serde(default)]
        reason: Option<String>,
    },
    /// R3 / ADR-039: classify a provider failure for retry policy.
    /// Returns error_class + retryable. Pure computation, no DB access.
    #[serde(rename = "retry.classify")]
    RetryClassify {
        error_type: String,
        error_message: String,
        #[serde(default)]
        is_context_overflow: bool,
    },
    /// R2 / ADR-031: record an approval audit event.
    #[serde(rename = "approval.record")]
    ApprovalRecord {
        db: String,
        session_id: String,
        agent: Option<String>,
        workspace: Option<String>,
        connector: Option<String>,
        tool: String,
        stage: String,
        status: Option<String>,
        approval: Option<String>,
        arguments: Option<Value>,
        result_preview: Option<String>,
        reason: Option<String>,
        resource: Option<String>,
        level: Option<String>,
        isolation: Option<String>,
        ts: Option<f64>,
    },
    /// R5 / ADR-047: test command that exercises the streaming ABI.
    /// Sends N delta frames with optional delay, then a done frame.
    #[serde(rename = "stream.echo")]
    StreamEcho { chunks: u32, delay_ms: Option<u64> },
    /// R5.1 v16: cancel an in-flight stream by request_id.
    #[serde(rename = "request.cancel")]
    RequestCancel { target_request_id: u64 },
    /// R5 / ADR-047 Phase 1: non-streaming provider completion.
    #[serde(rename = "provider.complete")]
    ProviderComplete {
        protocol: String,
        model: String,
        messages: Value,
        #[serde(default)]
        tools: Option<Value>,
        #[serde(default)]
        settings: Option<Value>,
        api_key: String,
        base_url: String,
    },
    /// R5 / ADR-047 Phase 1: streaming provider completion (SSE).
    #[serde(rename = "provider.stream")]
    ProviderStream {
        protocol: String,
        model: String,
        messages: Value,
        #[serde(default)]
        tools: Option<Value>,
        #[serde(default)]
        settings: Option<Value>,
        api_key: String,
        base_url: String,
    },
    /// R5 / ADR-047 Phase 2: model capabilities (matrix + heuristics).
    #[serde(rename = "provider.capabilities")]
    ProviderCapabilities { model: String },
    /// R5 / ADR-047 Phase 2b: read learned endpoint caps.
    #[serde(rename = "endpoint.caps")]
    EndpointCapsRead { path: String, endpoint_key: String },
    /// R5 / ADR-047 Phase 2b: record a param rejection.
    #[serde(rename = "endpoint.reject")]
    EndpointReject {
        path: String,
        endpoint_key: String,
        field: String,
    },
    /// R5 / ADR-047 Phase 2c: record a model call health outcome.
    #[serde(rename = "health.record")]
    HealthRecord {
        path: String,
        endpoint: String,
        model: String,
        ok: bool,
        #[serde(default)]
        ttft_ms: Option<f64>,
        #[serde(default)]
        duration_ms: Option<f64>,
        #[serde(default)]
        error_class: Option<String>,
    },
    /// R5 / ADR-047 Phase 2c: read one (endpoint, model) health profile.
    #[serde(rename = "health.profile")]
    HealthProfile {
        path: String,
        endpoint: String,
        model: String,
    },
    /// R5 / ADR-047 Phase 2c: read all health profiles.
    #[serde(rename = "health.all")]
    HealthAll { path: String },
    /// R5 / ADR-047 Phase 2d: resolve a model string to (provider, bare).
    #[serde(rename = "provider.routes")]
    ProviderRoutes {
        model: String,
        providers: Vec<String>,
        default: String,
    },
    /// R5 / ADR-047 Phase 3: translate a model access/quota error to a friendly message.
    #[serde(rename = "provider.friendly_error")]
    ProviderFriendlyError { model: String, message: String },
}

struct ConnCache {
    ledgers: HashMap<PathBuf, LedgerWriter>,
    idems: HashMap<PathBuf, IdempotencyWriter>,
    tasks: HashMap<PathBuf, TaskStore>,
    approvals: HashMap<PathBuf, ApprovalWriter>,
}

impl ConnCache {
    fn new() -> Self {
        Self {
            ledgers: HashMap::new(),
            idems: HashMap::new(),
            tasks: HashMap::new(),
            approvals: HashMap::new(),
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

    fn task(&mut self, db: &str) -> Result<&mut TaskStore, String> {
        let path = PathBuf::from(db);
        if !self.tasks.contains_key(&path) {
            let w = TaskStore::open(&path).map_err(|e| e.to_string())?;
            self.tasks.insert(path.clone(), w);
        }
        Ok(self.tasks.get_mut(&path).unwrap())
    }

    fn approval(&mut self, db: &str) -> Result<&mut ApprovalWriter, String> {
        let path = PathBuf::from(db);
        if !self.approvals.contains_key(&path) {
            let w = ApprovalWriter::open(db).map_err(|e| e.to_string())?;
            self.approvals.insert(path.clone(), w);
        }
        Ok(self.approvals.get_mut(&path).unwrap())
    }
}

fn err(s: String) -> Value {
    serde_json::json!({"ok": false, "error": s})
}

fn event_to_json(ev: delta_runtime_native::LedgerEvent) -> Value {
    serde_json::json!({
        "run_id": ev.run_id,
        "seq": ev.seq,
        "type": ev.r#type,
        "ts": ev.ts,
        "actor": ev.actor,
        "payload": ev.payload,
        "prev_hash": ev.prev_hash,
        "hash": ev.hash,
        "workspace": ev.workspace,
    })
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

impl Command {
    fn is_streaming(&self) -> bool {
        matches!(
            self,
            Command::StreamEcho { .. } | Command::ProviderStream { .. }
        )
    }
}
/// v16 stream context: carries request_id, cancel flag, and shared stdout.
struct StreamCtx {
    request_id: u64,
    cancel: Arc<AtomicBool>,
    out: Arc<io::Stdout>,
    cache: Arc<Mutex<ConnCache>>,
}

/// v16 helper: write one JSON line to the shared stdout.
/// Locks per-write so the main loop can interleave between stream frames.
fn emit(out: &Arc<io::Stdout>, frame: Value) {
    let mut guard = out.lock();
    writeln!(guard, "{frame}").ok();
    guard.flush().ok();
}

/// Wrapper around Arc<Stdout> that implements Write by locking per call.
/// This ensures no thread holds the stdout lock for an entire stream.
struct LineWriter(Arc<io::Stdout>);
impl io::Write for LineWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut g = self.0.lock();
        g.write(buf)
    }
    fn flush(&mut self) -> io::Result<()> {
        let mut g = self.0.lock();
        g.flush()
    }
}

fn handle_stream(cmd: Command, ctx: StreamCtx) {
    let StreamCtx {
        request_id,
        cancel,
        out,
        cache,
    } = ctx;

    // Start frame
    emit(
        &out,
        serde_json::json!({
            "ok": true, "request_id": request_id, "stream": "start"
        }),
    );

    match cmd {
        Command::StreamEcho { chunks, delay_ms } => {
            for i in 0..chunks {
                if cancel.load(Ordering::Relaxed) {
                    emit(
                        &out,
                        serde_json::json!({
                            "ok": true, "request_id": request_id, "stream": "done",
                            "result": {"cancelled": true, "chunk": i}
                        }),
                    );
                    return;
                }
                emit(
                    &out,
                    serde_json::json!({
                        "ok": true, "request_id": request_id, "stream": "delta",
                        "data": {"chunk": i, "total": chunks}
                    }),
                );
                if let Some(ms) = delay_ms {
                    std::thread::sleep(std::time::Duration::from_millis(ms));
                }
            }
            emit(
                &out,
                serde_json::json!({
                    "ok": true, "request_id": request_id, "stream": "done",
                    "result": {"chunks_sent": chunks}
                }),
            );
        }
        Command::ProviderStream {
            protocol,
            model,
            messages,
            tools,
            settings,
            api_key,
            base_url,
        } => {
            let req = ProviderRequest {
                protocol,
                model,
                messages,
                tools,
                settings,
                api_key,
                base_url,
            };
            // The stream runs in a background thread. Use a LineWriter that
            // locks stdout per-write so the main loop (and other streams)
            // can interleave between frames — no HOL blocking.
            let _ = &cache; // shared cache available for future provider needs
            let mut writer = LineWriter(out.clone());
            match delta_runtime_native::provider::stream(
                &req,
                &mut writer,
                &request_id.to_string(),
                &cancel,
            ) {
                Ok(result) => {
                    let cancelled = result
                        .get("cancelled")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false);
                    let result_val = if cancelled {
                        serde_json::json!({"cancelled": true})
                    } else {
                        result
                    };
                    emit(
                        &out,
                        serde_json::json!({
                            "ok": true, "request_id": request_id, "stream": "done",
                            "result": result_val
                        }),
                    );
                }
                Err(e) => {
                    emit(
                        &out,
                        serde_json::json!({
                            "ok": false, "request_id": request_id, "stream": "error",
                            "error": e
                        }),
                    );
                }
            }
        }
        _ => unreachable!(),
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
        Command::LedgerEvents { db, run_id } => {
            let writer = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match writer.reader() {
                Ok(r) => r,
                Err(e) => return err(e.to_string()),
            };
            match reader.events(&run_id) {
                Ok(events) => Ok(Value::Array(
                    events.into_iter().map(event_to_json).collect(),
                )),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::LedgerEventsInWorkspace {
            db,
            run_id,
            workspace,
        } => {
            let writer = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match writer.reader() {
                Ok(r) => r,
                Err(e) => return err(e.to_string()),
            };
            match reader.events_in_workspace(&run_id, &workspace) {
                Ok(events) => Ok(Value::Array(
                    events.into_iter().map(event_to_json).collect(),
                )),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::LedgerRuns { db } => {
            let writer = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match writer.reader() {
                Ok(r) => r,
                Err(e) => return err(e.to_string()),
            };
            match reader.runs() {
                Ok(runs) => Ok(Value::Array(runs.into_iter().map(Value::String).collect())),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::LedgerOpenRuns { db } => {
            let writer = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match writer.reader() {
                Ok(r) => r,
                Err(e) => return err(e.to_string()),
            };
            match reader.open_runs() {
                Ok(runs) => Ok(Value::Array(runs.into_iter().map(Value::String).collect())),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::LedgerRunStatus { db, run_id } => {
            let writer = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match writer.reader() {
                Ok(r) => r,
                Err(e) => return err(e.to_string()),
            };
            match reader.run_status(&run_id) {
                Ok(status) => Ok(serde_json::json!({"status": status})),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::LedgerVerify { db, run_id } => {
            let writer = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match writer.reader() {
                Ok(r) => r,
                Err(e) => return err(e.to_string()),
            };
            match reader.verify(&run_id) {
                Ok(valid) => Ok(serde_json::json!({"valid": valid})),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::LedgerRecoverStale { db } => {
            let writer = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match writer.recover_stale() {
                Ok(recovered) => Ok(serde_json::json!({
                    "recovered": recovered.len(),
                    "events": recovered,
                })),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::LedgerClose { db } => {
            let path = PathBuf::from(&db);
            let closed = cache.ledgers.remove(&path).is_some();
            Ok(serde_json::json!({ "closed": closed }))
        }
        Command::RunTransition {
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
            match writer.transition(&run_id, &event_type, &actor, ts, &payload, &workspace) {
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
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.save_task(&task_id, enabled, next_run, &data) {
                Ok(_) => Ok(serde_json::json!({"saved": true})),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskGet { db, task_id } => {
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.get_task(&task_id) {
                Ok(Some(entry)) => {
                    let mut val = entry.data.clone();
                    if let Some(obj) = val.as_object_mut() {
                        obj.insert("enabled".into(), Value::Bool(entry.enabled));
                        obj.insert(
                            "next_run".into(),
                            entry.next_run.map(Value::from).unwrap_or(Value::Null),
                        );
                    }
                    Ok(val)
                }
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskList { db } => {
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.list_tasks() {
                Ok(entries) => Ok(Value::Array(
                    entries
                        .into_iter()
                        .map(|e| {
                            let mut val = e.data.clone();
                            if let Some(obj) = val.as_object_mut() {
                                obj.insert("enabled".into(), Value::Bool(e.enabled));
                                obj.insert(
                                    "next_run".into(),
                                    e.next_run.map(Value::from).unwrap_or(Value::Null),
                                );
                            }
                            val
                        })
                        .collect(),
                )),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskDue { db, now } => {
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.due_tasks(now) {
                Ok(entries) => Ok(Value::Array(
                    entries
                        .into_iter()
                        .map(|e| {
                            let mut val = e.data.clone();
                            if let Some(obj) = val.as_object_mut() {
                                obj.insert("enabled".into(), Value::Bool(e.enabled));
                                obj.insert(
                                    "next_run".into(),
                                    e.next_run.map(Value::from).unwrap_or(Value::Null),
                                );
                            }
                            val
                        })
                        .collect(),
                )),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskDelete { db, task_id } => {
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.delete_task(&task_id) {
                Ok(deleted) => Ok(serde_json::json!({"deleted": deleted})),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskFindRun { db, run_id } => {
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.find_run(&run_id) {
                Ok(Some(entry)) => {
                    let mut val = entry.data.clone();
                    if let Some(obj) = val.as_object_mut() {
                        obj.insert("run_id".into(), Value::String(entry.run_id));
                        obj.insert("task_id".into(), Value::String(entry.task_id));
                        obj.insert("started_at".into(), Value::from(entry.started_at));
                        obj.insert("workspace".into(), Value::String(entry.workspace));
                    }
                    Ok(val)
                }
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskRuns { db, task_id, limit } => {
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.runs(&task_id, limit) {
                Ok(entries) => Ok(Value::Array(
                    entries
                        .into_iter()
                        .map(|e| {
                            let mut val = e.data.clone();
                            if let Some(obj) = val.as_object_mut() {
                                obj.insert("run_id".into(), Value::String(e.run_id));
                                obj.insert("task_id".into(), Value::String(e.task_id));
                                obj.insert("started_at".into(), Value::from(e.started_at));
                                obj.insert("workspace".into(), Value::String(e.workspace));
                            }
                            val
                        })
                        .collect(),
                )),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskForRunSession { db, session_id } => {
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.task_for_run_session(&session_id) {
                Ok(Some(entry)) => {
                    let mut val = entry.data.clone();
                    if let Some(obj) = val.as_object_mut() {
                        obj.insert("enabled".into(), Value::Bool(entry.enabled));
                        if let Some(nr) = entry.next_run {
                            obj.insert("next_run".into(), Value::from(nr));
                        }
                    }
                    Ok(val)
                }
                Ok(None) => Ok(Value::Null),
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
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.add_run(&run_id, &task_id, started_at, &data, &workspace) {
                Ok(_) => Ok(serde_json::json!({"added": true})),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskCompleteRun {
            db,
            run_id,
            task_id,
            started_at,
            run_data,
            workspace,
            finished_at,
            next_run,
        } => {
            let store = match cache.task(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            match store.complete_run(
                &run_id,
                &task_id,
                started_at,
                &run_data,
                &workspace,
                finished_at,
                next_run,
            ) {
                Ok(updated_task) => Ok(updated_task),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::TaskClose { db } => {
            let path = PathBuf::from(&db);
            let closed = cache.tasks.remove(&path).is_some();
            Ok(serde_json::json!({ "closed": closed }))
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
        Command::SourceRegister {
            db,
            origin,
            location,
            fingerprint,
            captured_at,
            mtime_ns,
            size_bytes,
            permissions,
            run_id: _,
            ts,
            workspace,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let writer = SourceCitationWriter::new(ledger);
            let input = SourceRegisterInput {
                origin,
                location,
                fingerprint,
                captured_at,
                mtime_ns,
                size_bytes,
                permissions,
            };
            match writer.register_source(
                input,
                ts.unwrap_or(0.0),
                workspace.as_deref().unwrap_or(""),
            ) {
                Ok(record) => Ok(serde_json::to_value(record).unwrap()),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::SourceGet { db, source_id } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => SourceCitationReader { reader: r },
                Err(e) => return err(e.to_string()),
            };
            match reader.get_source(&source_id) {
                Ok(Some(record)) => Ok(serde_json::to_value(record).unwrap()),
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::SourceList {
            db,
            origin,
            location,
            status,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => SourceCitationReader { reader: r },
                Err(e) => return err(e.to_string()),
            };
            match reader.list_sources() {
                Ok(sources) => {
                    let filtered: Vec<_> = sources
                        .into_iter()
                        .filter(|s| {
                            (origin.as_ref().is_none() || s.origin == *origin.as_ref().unwrap())
                                && (location.as_ref().is_none()
                                    || s.location == *location.as_ref().unwrap())
                                && (status.as_ref().is_none()
                                    || s.status == *status.as_ref().unwrap())
                        })
                        .collect();
                    Ok(Value::Array(
                        filtered
                            .into_iter()
                            .map(serde_json::to_value)
                            .collect::<Result<Vec<_>, _>>()
                            .unwrap(),
                    ))
                }
                Err(e) => Err(e.to_string()),
            }
        }
        Command::SourceLatest {
            db,
            location,
            origin,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => SourceCitationReader { reader: r },
                Err(e) => return err(e.to_string()),
            };
            match reader.latest_source(&location, &origin) {
                Ok(Some(record)) => Ok(serde_json::to_value(record).unwrap()),
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::SourceRefresh {
            db,
            checks,
            ts,
            workspace,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => SourceCitationReader { reader: r },
                Err(e) => return err(e.to_string()),
            };
            let existing = match reader.list_sources() {
                Ok(v) => v,
                Err(e) => return err(e.to_string()),
            };
            let mut source_map: std::collections::HashMap<_, _> =
                existing.into_iter().map(|s| (s.id.clone(), s)).collect();

            // Create writer once for persisting updates
            let writer = SourceCitationWriter::new(ledger);

            let mut drifted = Vec::new();
            for check in checks {
                let source_id = check.get("source_id").and_then(Value::as_str).unwrap_or("");
                if let Some(source) = source_map.get_mut(source_id) {
                    let current_fp = check.get("fingerprint").and_then(Value::as_str);
                    let new_status = match current_fp {
                        None => "missing",
                        Some(fp) if fp != source.fingerprint => "changed",
                        _ => "current",
                    };
                    if new_status != "current" {
                        source.status = new_status.to_string();
                        source.checked_at = Some(
                            OffsetDateTime::now_utc()
                                .format(&time::format_description::well_known::Rfc3339)
                                .unwrap_or_default(),
                        );
                        if let Some(mtime) = check.get("mtime_ns").and_then(Value::as_u64) {
                            source.mtime_ns = Some(mtime);
                        }
                        if let Some(size) = check.get("size_bytes").and_then(Value::as_u64) {
                            source.size_bytes = Some(size);
                        }
                        drifted.push(source.clone());
                    } else if check.get("mtime_ns").is_some() {
                        // Status is current but we have updated mtime/size - persist the refresh
                        source.checked_at = Some(
                            OffsetDateTime::now_utc()
                                .format(&time::format_description::well_known::Rfc3339)
                                .unwrap_or_default(),
                        );
                        if let Some(mtime) = check.get("mtime_ns").and_then(Value::as_u64) {
                            source.mtime_ns = Some(mtime);
                        }
                        if let Some(size) = check.get("size_bytes").and_then(Value::as_u64) {
                            source.size_bytes = Some(size);
                        }
                        // Persist the refreshed source
                        if let Err(e) = writer.append_source_registered(source, ts, &workspace) {
                            return err(e.to_string());
                        }
                    }
                }
            }
            // Persist all drifted sources
            for record in &drifted {
                if let Err(e) = writer.append_source_registered(record, ts, &workspace) {
                    return err(e.to_string());
                }
            }
            Ok(Value::Array(
                drifted
                    .into_iter()
                    .map(serde_json::to_value)
                    .collect::<Result<Vec<_>, _>>()
                    .unwrap(),
            ))
        }
        Command::CitationMark {
            db,
            source_id,
            run_id,
            ranges,
            ts,
            workspace,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let writer = SourceCitationWriter::new(ledger);
            match writer.mark_cited(
                &source_id,
                &run_id,
                ranges,
                ts.unwrap_or(0.0),
                workspace.as_deref().unwrap_or(""),
            ) {
                Ok(marked) => Ok(serde_json::json!({ "marked": marked })),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::CitationValidate {
            db,
            source_id,
            range,
            workspace,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => SourceCitationReader { reader: r },
                Err(e) => return err(e.to_string()),
            };
            let source = match reader.get_source(&source_id) {
                Ok(Some(s)) => s,
                Ok(None) => {
                    let result = CitationValidationResult {
                        validity: CitationValidity::SourceMissing,
                        valid: false,
                        status: "missing".to_string(),
                        reason: "source_gone".to_string(),
                        source_exists: false,
                        source_unchanged: false,
                        structure_valid: false,
                        revision_matches: false,
                        range_valid: None,
                        current_fingerprint: None,
                        current_sha256: None,
                        current_line_count: None,
                        detail: Some("source not found".to_string()),
                    };
                    return serde_json::json!({"ok": true, "result": serde_json::to_value(result).unwrap()});
                }
                Err(e) => return serde_json::json!({"ok": false, "error": e.to_string()}),
            };
            let source_value = serde_json::to_value(source).unwrap();
            Ok(serde_json::to_value(validate_source_citation(
                Some(&source_value),
                &range,
                workspace.as_deref().map(std::path::Path::new),
            ))
            .unwrap())
        }
        Command::CitationList { db, source_id } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => SourceCitationReader { reader: r },
                Err(e) => return err(e.to_string()),
            };
            match reader.get_citations(&source_id) {
                Ok(citations) => Ok(Value::Array(citations)),
                Err(e) => Err(e.to_string()),
            }
        }
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
        Command::ValidationRegister {
            db,
            run_id,
            criteria,
            evaluated_at,
            result,
            ts,
            workspace,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let writer = ValidationWriter::new(ledger);
            let input = ValidationRegisterInput {
                run_id,
                criteria,
                evaluated_at,
                result,
            };
            match writer.register_validation(
                input,
                ts.unwrap_or(0.0),
                workspace.as_deref().unwrap_or(""),
            ) {
                Ok(record) => Ok(serde_json::to_value(record).unwrap()),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::ValidationGet { db, validation_id } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => ValidationReader { reader: r },
                Err(e) => return err(e.to_string()),
            };
            match reader.get_validation(&validation_id) {
                Ok(Some(record)) => Ok(serde_json::to_value(record).unwrap()),
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::ValidationList { db, run_id } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => ValidationReader { reader: r },
                Err(e) => return err(e.to_string()),
            };
            let validations = match reader.list_validations() {
                Ok(v) => v,
                Err(e) => return err(e.to_string()),
            };
            let filtered: Vec<_> = validations
                .into_iter()
                .filter(|v| run_id.as_ref().is_none() || v.run_id == *run_id.as_ref().unwrap())
                .collect();
            Ok(Value::Array(
                filtered
                    .into_iter()
                    .map(serde_json::to_value)
                    .collect::<Result<Vec<_>, _>>()
                    .unwrap(),
            ))
        }
        Command::ValidationLatest { db, run_id } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => ValidationReader { reader: r },
                Err(e) => return err(e.to_string()),
            };
            match reader.latest_validation(&run_id) {
                Ok(Some(record)) => Ok(serde_json::to_value(record).unwrap()),
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::ValidationEval {
            db,
            run_id,
            criteria,
            artifacts,
            workspace,
            valid_citation_count,
            ts,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let writer = ValidationWriter::new(ledger);
            match writer.evaluate_and_register(
                &run_id,
                &criteria,
                &artifacts,
                &workspace,
                valid_citation_count,
                ts.unwrap_or(0.0),
            ) {
                Ok((record, result)) => Ok(serde_json::json!({
                    "record": serde_json::to_value(record).unwrap(),
                    "result": serde_json::to_value(result).unwrap(),
                })),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::CheckpointRegister {
            db,
            checkpoint_id,
            run_id,
            session_id,
            phase,
            pending_tool_call,
            pending_inbox_item_id,
            last_event_seq,
            todo_summary,
            recent_artifacts,
            error,
            ts,
            workspace,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let writer = CheckpointWriter::new(ledger);
            let input = CheckpointRegisterInput {
                checkpoint_id,
                run_id,
                session_id,
                phase,
                pending_tool_call,
                pending_inbox_item_id,
                last_event_seq,
                todo_summary,
                recent_artifacts,
                error,
            };
            match writer.register(input, ts.unwrap_or(0.0), workspace.as_deref().unwrap_or("")) {
                Ok(record) => Ok(serde_json::to_value(record).unwrap()),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::CheckpointGet { db, checkpoint_id } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => CheckpointReader::from_reader(r),
                Err(e) => return err(e.to_string()),
            };
            match reader.get(&checkpoint_id) {
                Ok(Some(record)) => Ok(serde_json::to_value(record).unwrap()),
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::CheckpointList {
            db,
            run_id,
            session_id,
        } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => CheckpointReader::from_reader(r),
                Err(e) => return err(e.to_string()),
            };
            match reader.list(run_id.as_deref(), session_id.as_deref()) {
                Ok(records) => Ok(Value::Array(
                    records
                        .into_iter()
                        .map(serde_json::to_value)
                        .collect::<Result<Vec<_>, _>>()
                        .unwrap(),
                )),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::CheckpointLatest { db, run_id } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => CheckpointReader::from_reader(r),
                Err(e) => return err(e.to_string()),
            };
            match reader.latest(&run_id) {
                Ok(Some(record)) => Ok(serde_json::to_value(record).unwrap()),
                Ok(None) => Ok(Value::Null),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::CheckpointValidate { db, checkpoint_id } => {
            let ledger = match cache.ledger(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let reader = match ledger.reader() {
                Ok(r) => CheckpointReader::from_reader(r),
                Err(e) => return err(e.to_string()),
            };
            match reader.validate(&checkpoint_id) {
                Ok(result) => Ok(serde_json::to_value(result).unwrap()),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::CheckpointClose { db } => {
            let path = PathBuf::from(&db);
            let closed = cache.ledgers.remove(&path).is_some();
            Ok(serde_json::json!({ "closed": closed }))
        }
        Command::PolicyEvaluate {
            tool_name,
            arguments,
            metadata,
            decision,
            level,
            workspace_root,
            roots,
        } => {
            let md = metadata.and_then(|v| serde_json::from_value(v).ok());
            let dec = match serde_json::from_value::<delta_runtime_native::Decision>(decision) {
                Ok(d) => d,
                Err(e) => return err(e.to_string()),
            };
            let root_entries: Vec<delta_runtime_native::RootEntry> = roots
                .into_iter()
                .map(|v| serde_json::from_value(v).unwrap_or_default())
                .collect();
            let input = PolicyEvaluateInput {
                tool_name,
                arguments,
                metadata: md,
                decision: dec,
                level,
                workspace_root,
                roots: root_entries,
            };
            match delta_runtime_native::evaluate(input) {
                Ok(output) => Ok(serde_json::to_value(output).unwrap()),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::PolicyClassify {
            tool_name,
            arguments,
            metadata,
        } => {
            let md = metadata.and_then(|v| serde_json::from_value(v).ok());
            let level = classify(&tool_name, arguments.as_ref(), md.as_ref());
            Ok(serde_json::json!({"level": level as i64}))
        }
        Command::ToolLifecyclePlan {
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
            let input = ToolLifecyclePlanInput {
                db,
                run_id,
                tool_call_id,
                tool_name,
                args,
            };
            match delta_runtime_native::plan_tool_lifecycle(writer, &input) {
                Ok(output) => Ok(serde_json::to_value(output).unwrap()),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::ToolLifecycleCancel {
            db,
            run_id,
            tool_call_id,
            tool_name,
            reason,
        } => {
            let writer = match cache.idem(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let input = ToolLifecycleCancelInput {
                db,
                run_id,
                tool_call_id,
                tool_name,
                reason,
            };
            match delta_runtime_native::cancel_tool_lifecycle(writer, &input) {
                Ok(output) => Ok(serde_json::to_value(output).unwrap()),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::RetryClassify {
            error_type,
            error_message,
            is_context_overflow,
        } => {
            let input = RetryClassifyInput {
                error_type,
                error_message,
                is_context_overflow,
            };
            Ok(serde_json::to_value(delta_runtime_native::classify_error(&input)).unwrap())
        }
        Command::ApprovalRecord {
            db,
            session_id,
            agent,
            workspace,
            connector,
            tool,
            stage,
            status,
            approval,
            arguments,
            result_preview,
            reason,
            resource,
            level,
            isolation,
            ts,
        } => {
            let writer = match cache.approval(&db) {
                Ok(w) => w,
                Err(e) => return err(e),
            };
            let input = ApprovalRecordInput {
                session_id,
                agent,
                workspace: workspace.clone(),
                connector,
                tool,
                stage,
                status,
                approval,
                arguments,
                result_preview,
                reason,
                resource,
                level,
                isolation,
                ts,
            };
            let ws = workspace.as_deref().unwrap_or("");
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            match writer.record(input, ts.unwrap_or(now), ws) {
                Ok(output) => Ok(serde_json::to_value(output).unwrap()),
                Err(e) => Err(e.to_string()),
            }
        }
        Command::StreamEcho { .. } => Err("streaming commands handled in handle_stream".into()),
        Command::RequestCancel {
            target_request_id: _,
        } => Ok(serde_json::json!({"handled_in_main": true})),
        Command::ProviderComplete {
            protocol,
            model,
            messages,
            tools,
            settings,
            api_key,
            base_url,
        } => {
            let req = ProviderRequest {
                protocol,
                model,
                messages,
                tools,
                settings,
                api_key,
                base_url,
            };
            match delta_runtime_native::provider::complete(&req) {
                Ok(v) => Ok(v),
                Err(e) => Err(e),
            }
        }
        Command::ProviderStream { .. } => Err("streaming commands handled in handle_stream".into()),
        Command::ProviderCapabilities { model } => {
            Ok(delta_runtime_native::provider::capabilities_for(&model))
        }
        Command::EndpointCapsRead { path, endpoint_key } => Ok(
            delta_runtime_native::provider::endpoint_caps_read(&path, &endpoint_key),
        ),
        Command::EndpointReject {
            path,
            endpoint_key,
            field,
        } => Ok(delta_runtime_native::provider::endpoint_reject(
            &path,
            &endpoint_key,
            &field,
        )),
        Command::HealthRecord {
            path,
            endpoint,
            model,
            ok,
            ttft_ms,
            duration_ms,
            error_class,
        } => Ok(delta_runtime_native::provider::health_record(
            &path,
            &endpoint,
            &model,
            ok,
            ttft_ms,
            duration_ms,
            error_class.as_deref(),
        )),
        Command::HealthProfile {
            path,
            endpoint,
            model,
        } => Ok(delta_runtime_native::provider::health_profile(
            &path, &endpoint, &model,
        )),
        Command::HealthAll { path } => Ok(delta_runtime_native::provider::health_all(&path)),
        Command::ProviderRoutes {
            model,
            providers,
            default,
        } => Ok(delta_runtime_native::provider::route(
            &model, &providers, &default,
        )),
        Command::ProviderFriendlyError { model, message } => Ok(
            delta_runtime_native::provider::friendly_model_error(&model, &message),
        ),
    };
    match result {
        Ok(v) => serde_json::json!({"ok": true, "result": v}),
        Err(e) => serde_json::json!({"ok": false, "error": e}),
    }
}

fn main() -> std::process::ExitCode {
    use std::sync::atomic::AtomicU64;

    let stdin = io::stdin();
    let stdout = Arc::new(io::stdout());
    let cache = Arc::new(Mutex::new(ConnCache::new()));

    // v16: active stream registry — maps request_id → cancel flag.
    // The main loop sets the flag when request.cancel arrives; the
    // stream thread checks it at each delta boundary.
    let active: Arc<Mutex<HashMap<u64, Arc<AtomicBool>>>> = Arc::new(Mutex::new(HashMap::new()));

    // Auto-incrementing request_id for commands that don't carry one
    // (v15 backward compat: hello, ping, etc.).
    let auto_id = AtomicU64::new(1);

    // Track spawned stream threads so we can join on shutdown.
    let mut threads: Vec<std::thread::JoinHandle<()>> = Vec::new();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        // Parse request_id from the raw JSON (it's not part of Command enum).
        let raw: Value = match serde_json::from_str(trimmed) {
            Ok(v) => v,
            Err(e) => {
                emit(
                    &stdout,
                    serde_json::json!({"ok": false, "error": format!("parse: {e}")}),
                );
                continue;
            }
        };
        let request_id: u64 = raw
            .get("request_id")
            .and_then(|v| v.as_u64())
            .unwrap_or_else(|| auto_id.fetch_add(1, Ordering::Relaxed));

        // Handle request.cancel specially (non-streaming, needs active registry).
        let is_cancel = raw.get("cmd").and_then(|v| v.as_str()) == Some("request.cancel");
        if is_cancel {
            let target = raw
                .get("target_request_id")
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            let cancelled = {
                let mut map = active.lock().unwrap();
                if let Some(flag) = map.get(&target) {
                    flag.store(true, Ordering::Relaxed);
                    true
                } else {
                    false
                }
            };
            emit(
                &stdout,
                serde_json::json!({
                    "ok": true, "request_id": request_id,
                    "result": {"cancelled": cancelled, "target_request_id": target}
                }),
            );
            continue;
        }

        let cmd: Command = match serde_json::from_value(raw) {
            Ok(c) => c,
            Err(e) => {
                emit(
                    &stdout,
                    serde_json::json!({
                        "ok": false, "request_id": request_id,
                        "error": format!("parse: {e}")
                    }),
                );
                continue;
            }
        };

        if cmd.is_streaming() {
            // v16: dispatch streaming commands to a background thread so
            // the main loop continues reading stdin (AF-04: no HOL blocking).
            let cancel = Arc::new(AtomicBool::new(false));
            {
                let mut map = active.lock().unwrap();
                map.insert(request_id, cancel.clone());
            }
            let ctx = StreamCtx {
                request_id,
                cancel,
                out: stdout.clone(),
                cache: cache.clone(),
            };
            let active_clone = active.clone();
            let handle = std::thread::spawn(move || {
                handle_stream(cmd, ctx);
                // Cleanup: remove from active registry.
                let mut map = active_clone.lock().unwrap();
                map.remove(&request_id);
            });
            threads.push(handle);
        } else {
            let resp = handle(cmd, &cache);
            let mut full = serde_json::to_value(&resp).unwrap_or(resp);
            if let Some(obj) = full.as_object_mut() {
                obj.insert("request_id".into(), Value::from(request_id));
            }
            emit(&stdout, full);
        }
    }

    // Graceful shutdown: join all stream threads.
    for t in threads {
        let _ = t.join();
    }
    std::process::ExitCode::SUCCESS
}
