//! R6 Capability ABI — the Delta-owned contract between the Rust Runtime and
//! a controlled Worker / Capability execution environment.
//!
//! Per R6 §8: `aisuite` must no longer be the core Runtime abstraction. Instead
//! every capability (File, Search, Shell, Web, MCP, Skill, Automation tools,
//! Connector tools) runs behind this contract. The Rust Runtime is the sole
//! supervisor: it starts the worker, enforces bounds (timeout, cancel,
//! permission/network/secrets grants), and formalizes the typed result /
//! artifact. A Worker (Python / PowerShell / Shell) only executes.
//!
//! This module is pure data + typing: the contract is versioned and
//! serializable so the runtime can pass it over the Worker Runner boundary
//! (subprocess stdin/stdout, MCP stdio, or an in-Tauri-process trait object).
//!
//! Contract fields (R6 §8):
//!   capability id · job id · workspace · input files · input hashes ·
//!   arguments · permission grants · network grants · secrets grants ·
//!   timeout · cancel · progress · typed result · artifact staging · stderr ·
//!   diagnostics · exit state

use std::collections::{BTreeMap, HashMap};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, RwLock};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::Digest;

use crate::runtime::{StagedArtifact, ToolCall, ToolExecutionContext, ToolExecutor, ToolResult};

/// The Capability ABI wire version. Bump on any breaking change to the contract.
pub const CAPABILITY_ABI_VERSION: u32 = 2;

/// A file the capability is permitted to read (workspace-relative or absolute).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityInputFile {
    pub path: String,
    /// SHA-256 of the file content at job start, for integrity / idempotency.
    #[serde(default)]
    pub sha256: Option<String>,
    #[serde(default)]
    pub size: Option<u64>,
}

/// Permission grants scoped to a single job. A grant is the *most* a capability
/// may do; policy/approval already gated it upstream.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CapabilityGrants {
    /// Absolute read roots the job may read (the workspace is always included).
    pub read_roots: Vec<String>,
    /// Absolute write roots the job may write (empty = read-only).
    pub write_roots: Vec<String>,
    /// Allowed outbound network targets, e.g. "https://api.example.com:443".
    pub network: Vec<String>,
    /// Secret grants: keys the capability may read from the secrets boundary.
    pub secrets: Vec<String>,
    /// Whether the job may spawn processes (shell/exec).
    pub exec: bool,
}

impl CapabilityGrants {
    pub fn read_only() -> Self {
        Self {
            write_roots: Vec::new(),
            ..Default::default()
        }
    }
}

/// The full capability invocation contract sent to a Worker Runner.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityJob {
    pub abi_version: u32,
    /// The capability id (e.g. "file.read", "shell.exec", "web.fetch", "mcp.call").
    pub capability_id: String,
    /// Unique job id for cancelling / correlating side effects.
    pub job_id: String,
    /// Optional owning run id (ledger/audit correlation).
    #[serde(default)]
    pub run_id: Option<String>,
    /// Optional owning session id.
    #[serde(default)]
    pub session_id: Option<String>,
    /// The workspace root the job executes under. None = workspace-less (chat).
    #[serde(default)]
    pub workspace: Option<String>,
    /// Files the job may read, with input hashes.
    #[serde(default)]
    pub input_files: Vec<CapabilityInputFile>,
    /// Capability-specific arguments (JSON).
    pub arguments: Value,
    /// Permission / network / secrets grants for this job.
    #[serde(default)]
    pub grants: CapabilityGrants,
    /// Hard execution timeout in seconds. 0 = none.
    #[serde(default)]
    pub timeout_secs: u64,
    /// Where staged artifacts land (workspace-relative scratch subdir).
    #[serde(default)]
    pub artifact_staging_dir: Option<String>,
}

impl CapabilityJob {
    pub fn new(capability_id: &str, job_id: &str, arguments: Value) -> Self {
        Self {
            abi_version: Self::version(),
            capability_id: capability_id.to_string(),
            job_id: job_id.to_string(),
            run_id: None,
            session_id: None,
            workspace: None,
            input_files: Vec::new(),
            arguments,
            grants: CapabilityGrants::default(),
            timeout_secs: 0,
            artifact_staging_dir: None,
        }
    }

    pub fn version() -> u32 {
        CAPABILITY_ABI_VERSION
    }

    pub fn workspace(mut self, path: &str) -> Self {
        self.workspace = Some(path.to_string());
        self
    }

    pub fn grants(mut self, grants: CapabilityGrants) -> Self {
        self.grants = grants;
        self
    }

    pub fn running_f_in(mut self, file: CapabilityInputFile) -> Self {
        self.input_files.push(file);
        self
    }

    pub fn with_timeout(mut self, secs: u64) -> Self {
        self.timeout_secs = secs;
        self
    }
}

/// Progress frame a worker emits during execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityProgress {
    pub job_id: String,
    /// 0.0..=1.0 coarse progress, or -1 for indeterminate.
    pub fraction: f64,
    #[serde(default)]
    pub stage: Option<String>,
    #[serde(default)]
    pub message: Option<String>,
}

/// A staged artifact produced by the job (to be formalized by the Runtime).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityArtifact {
    /// Worker-owned candidate path under `artifact_staging_dir`.
    pub staging_path: String,
    /// Requested destination relative to the workspace. The Runtime validates
    /// this path before promoting the candidate.
    pub relative_path: String,
    /// Artifact kind (e.g. "file", "csv", "sheet", "image", "report").
    #[serde(default)]
    pub kind: Option<String>,
    #[serde(default)]
    pub sha256: Option<String>,
    #[serde(default)]
    pub size: Option<u64>,
    #[serde(default)]
    pub incomplete: bool,
}

/// Structured diagnostics (stderr lines + a typed error, when any).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityDiagnostics {
    #[serde(default)]
    pub stderr_lines: Vec<String>,
    #[serde(default)]
    pub error_code: Option<String>,
    #[serde(default)]
    pub error_message: Option<String>,
}

/// The terminal state machine of a capability job.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum CapabilityExitState {
    /// Completed successfully with a typed result.
    #[serde(rename = "completed")]
    Completed,
    /// Failed with an error/diagnostics.
    #[serde(rename = "failed")]
    Failed,
    /// Cancelled (either by the runtime or a worker-side abort).
    #[serde(rename = "cancelled")]
    Cancelled,
    /// Exceeded the job timeout; runtime forced termination.
    #[serde(rename = "timed_out")]
    TimedOut,
}

/// The typed result a Worker Runner returns after a job terminates.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityResult {
    pub abi_version: u32,
    pub job_id: String,
    pub state: CapabilityExitState,
    /// Typed output (parsed), or the raw text output when untyped.
    pub result: Option<Value>,
    /// Text output (stdout) when the capability emits one.
    #[serde(default)]
    pub output: Option<String>,
    #[serde(default)]
    pub artifacts: Vec<CapabilityArtifact>,
    #[serde(default)]
    pub diagnostics: Option<CapabilityDiagnostics>,
    pub finished_at: f64,
}

impl CapabilityResult {
    pub fn completed(job_id: &str, result: Value) -> Self {
        Self {
            abi_version: CAPABILITY_ABI_VERSION,
            job_id: job_id.to_string(),
            state: CapabilityExitState::Completed,
            result: Some(result),
            output: None,
            artifacts: Vec::new(),
            diagnostics: None,
            finished_at: unix_secs(),
        }
    }

    pub fn failed(job_id: &str, message: &str, code: Option<&str>) -> Self {
        Self {
            abi_version: CAPABILITY_ABI_VERSION,
            job_id: job_id.to_string(),
            state: CapabilityExitState::Failed,
            result: None,
            output: None,
            artifacts: Vec::new(),
            diagnostics: Some(CapabilityDiagnostics {
                stderr_lines: Vec::new(),
                error_code: code.map(String::from),
                error_message: Some(message.to_string()),
            }),
            finished_at: unix_secs(),
        }
    }

    pub fn cancelled(job_id: &str) -> Self {
        Self {
            abi_version: CAPABILITY_ABI_VERSION,
            job_id: job_id.to_string(),
            state: CapabilityExitState::Cancelled,
            result: None,
            output: None,
            artifacts: Vec::new(),
            diagnostics: None,
            finished_at: unix_secs(),
        }
    }

    pub fn timed_out(job_id: &str) -> Self {
        Self {
            abi_version: CAPABILITY_ABI_VERSION,
            job_id: job_id.to_string(),
            state: CapabilityExitState::TimedOut,
            result: None,
            output: None,
            artifacts: Vec::new(),
            diagnostics: None,
            finished_at: unix_secs(),
        }
    }

    pub fn with_output(mut self, output: String) -> Self {
        self.output = Some(output);
        self
    }

    pub fn with_artifact(mut self, artifact: CapabilityArtifact) -> Self {
        self.artifacts.push(artifact);
        self
    }

    pub fn with_stderr(mut self, lines: impl IntoIterator<Item = String>) -> Self {
        let diag = self
            .diagnostics
            .get_or_insert_with(|| CapabilityDiagnostics {
                stderr_lines: Vec::new(),
                error_code: None,
                error_message: None,
            });
        diag.stderr_lines.extend(lines);
        self
    }
}

fn unix_secs() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// A capability runner: executes a job and returns its typed result.
///
/// Implementations live in the Worker Runner (subprocess, MCP stdio, or a
/// Rust-native capability). The Runtime Host drives this trait and formalizes
/// the artifacts / ledger / idempotency around it.
pub trait CapabilityRunner: Send + Sync {
    fn run(&self, job: &CapabilityJob, control: &CapabilityControl) -> CapabilityResult;
}

/// Runtime-owned controls visible to a runner. Workers may observe cancellation
/// and emit progress; they cannot mutate Runtime state or grant themselves more
/// access.
#[derive(Clone)]
pub struct CapabilityControl {
    cancel: Arc<AtomicBool>,
    progress: Arc<dyn Fn(CapabilityProgress) + Send + Sync>,
}

impl CapabilityControl {
    pub fn new(
        cancel: Arc<AtomicBool>,
        progress: Arc<dyn Fn(CapabilityProgress) + Send + Sync>,
    ) -> Self {
        Self { cancel, progress }
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::Acquire)
    }

    pub fn emit_progress(&self, progress: CapabilityProgress) {
        (self.progress)(progress);
    }
}

#[derive(Clone)]
pub struct CapabilityRegistration {
    pub capability_id: String,
    pub tool_name: String,
    pub description: String,
    pub parameters: Value,
    pub metadata: Value,
    pub grants: CapabilityGrants,
    pub workspace_write: bool,
    pub runner: Arc<dyn CapabilityRunner>,
}

/// Rust-authoritative capability catalogue. Registration owns both the model
/// schema and the runner, preventing a model-visible tool from bypassing its
/// controlled execution implementation.
#[derive(Default)]
pub struct CapabilityRegistry {
    by_tool: HashMap<String, CapabilityRegistration>,
}

impl CapabilityRegistry {
    pub fn register(&mut self, registration: CapabilityRegistration) -> Result<(), String> {
        if registration.capability_id.trim().is_empty() || registration.tool_name.trim().is_empty()
        {
            return Err("capability id and tool name are required".to_string());
        }
        if self.by_tool.contains_key(&registration.tool_name) {
            return Err(format!(
                "capability tool is already registered: {}",
                registration.tool_name
            ));
        }
        self.by_tool
            .insert(registration.tool_name.clone(), registration);
        Ok(())
    }

    pub fn get(&self, tool_name: &str) -> Option<CapabilityRegistration> {
        self.by_tool.get(tool_name).cloned()
    }

    pub fn tool_schemas(&self) -> Value {
        let mut registrations = self.by_tool.values().collect::<Vec<_>>();
        registrations.sort_by(|left, right| left.tool_name.cmp(&right.tool_name));
        Value::Array(
            registrations
                .into_iter()
                .map(|registration| {
                    serde_json::json!({
                        "type": "function",
                        "function": {
                            "name": registration.tool_name,
                            "description": registration.description,
                            "parameters": registration.parameters,
                            "metadata": registration.metadata,
                        }
                    })
                })
                .collect(),
        )
    }
}

/// Capability supervisor used by the Runtime as its mandatory ToolExecutor.
pub struct CapabilityHost {
    registry: RwLock<CapabilityRegistry>,
}

impl Default for CapabilityHost {
    fn default() -> Self {
        Self {
            registry: RwLock::new(CapabilityRegistry::default()),
        }
    }
}

impl CapabilityHost {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn product_defaults() -> Result<Self, String> {
        let host = Self::new();
        host.register(native_read_registration())?;
        host.register(native_write_registration())?;
        Ok(host)
    }

    pub fn register(&self, registration: CapabilityRegistration) -> Result<(), String> {
        self.registry.write().unwrap().register(registration)
    }

    pub fn tool_schemas(&self) -> Value {
        self.registry.read().unwrap().tool_schemas()
    }

    fn build_job(
        registration: &CapabilityRegistration,
        call: &ToolCall,
        context: &ToolExecutionContext,
    ) -> Result<CapabilityJob, String> {
        let job_id = uuid::Uuid::new_v4().to_string();
        let mut grants = registration.grants.clone();
        let staging_dir = context.workspace.as_ref().map(|workspace| {
            PathBuf::from(workspace)
                .join(".delta")
                .join("staging")
                .join(&context.run_id)
                .join(&job_id)
        });
        if let Some(workspace) = &context.workspace {
            let workspace = PathBuf::from(workspace)
                .canonicalize()
                .map_err(|error| format!("workspace is unavailable: {error}"))?;
            let workspace = workspace.to_string_lossy().to_string();
            if !grants.read_roots.contains(&workspace) {
                grants.read_roots.push(workspace.clone());
            }
            if registration.workspace_write && !grants.write_roots.contains(&workspace) {
                grants.write_roots.push(workspace);
            }
        }
        if let Some(staging_dir) = &staging_dir {
            std::fs::create_dir_all(staging_dir).map_err(|error| error.to_string())?;
        }
        let mut job =
            CapabilityJob::new(&registration.capability_id, &job_id, call.arguments.clone());
        job.run_id = Some(context.run_id.clone());
        job.session_id = Some(context.session_id.clone());
        job.workspace = context.workspace.clone();
        job.grants = grants;
        job.timeout_secs = context.timeout.as_secs().max(1);
        job.artifact_staging_dir = staging_dir.map(|path| path.to_string_lossy().to_string());
        job.input_files = collect_input_files(&job, registration.workspace_write)?;
        validate_input_files(&job)?;
        Ok(job)
    }
}

impl ToolExecutor for CapabilityHost {
    fn execute(&self, call: &ToolCall, context: &ToolExecutionContext) -> ToolResult {
        let Some(registration) = self.registry.read().unwrap().get(&call.name) else {
            return ToolResult::failure(
                &call.id,
                format!("capability is not registered: {}", call.name),
            );
        };
        let job = match Self::build_job(&registration, call, context) {
            Ok(job) => job,
            Err(error) => return ToolResult::failure(&call.id, error),
        };
        let control = CapabilityControl::new(context.cancel.clone(), context.progress.clone());
        let capability_result = registration.runner.run(&job, &control);
        let output = capability_result
            .result
            .clone()
            .or_else(|| capability_result.output.clone().map(Value::String))
            .unwrap_or_else(|| serde_json::json!({}));
        let error = capability_result
            .diagnostics
            .as_ref()
            .and_then(|diagnostics| {
                diagnostics
                    .error_message
                    .clone()
                    .or_else(|| diagnostics.error_code.clone())
            });
        let state = match capability_result.state {
            CapabilityExitState::Completed => crate::runtime::ToolExitState::Completed,
            CapabilityExitState::Failed => crate::runtime::ToolExitState::Failed,
            CapabilityExitState::Cancelled => crate::runtime::ToolExitState::Cancelled,
            CapabilityExitState::TimedOut => crate::runtime::ToolExitState::TimedOut,
        };
        let default_error = match capability_result.state {
            CapabilityExitState::Completed => None,
            CapabilityExitState::Failed => Some("capability failed".to_string()),
            CapabilityExitState::Cancelled => Some("capability cancelled".to_string()),
            CapabilityExitState::TimedOut => Some("capability timed out".to_string()),
        };
        ToolResult {
            tool_call_id: call.id.clone(),
            output,
            error: error.or(default_error),
            staged_artifacts: capability_result
                .artifacts
                .into_iter()
                .map(|artifact| StagedArtifact {
                    staging_path: PathBuf::from(artifact.staging_path),
                    relative_path: artifact.relative_path,
                    kind: artifact.kind.unwrap_or_else(|| "file".to_string()),
                    incomplete: artifact.incomplete,
                })
                .collect(),
            validation_criteria: None,
            state,
        }
    }
}

type NativeHandler =
    dyn Fn(&CapabilityJob, &CapabilityControl) -> CapabilityResult + Send + Sync + 'static;

/// In-process capability runner for small, dependency-free trusted operations.
pub struct NativeCapabilityRunner {
    handler: Arc<NativeHandler>,
}

impl NativeCapabilityRunner {
    pub fn new<F>(handler: F) -> Self
    where
        F: Fn(&CapabilityJob, &CapabilityControl) -> CapabilityResult + Send + Sync + 'static,
    {
        Self {
            handler: Arc::new(handler),
        }
    }
}

impl CapabilityRunner for NativeCapabilityRunner {
    fn run(&self, job: &CapabilityJob, control: &CapabilityControl) -> CapabilityResult {
        if control.is_cancelled() {
            return CapabilityResult::cancelled(&job.job_id);
        }
        (self.handler)(job, control)
    }
}

/// Process-backed runner for Python, PowerShell, shell, and other controlled
/// workers. One ABI job is written to stdin. Stdout may contain progress frames
/// followed by a typed CapabilityResult. The Runtime owns timeout/cancellation
/// and force-terminates the child when either fires.
pub struct WorkerProcessRunner {
    program: PathBuf,
    arguments: Vec<String>,
    environment: BTreeMap<String, String>,
}

impl WorkerProcessRunner {
    pub fn new(program: impl Into<PathBuf>, arguments: Vec<String>) -> Self {
        Self {
            program: program.into(),
            arguments,
            environment: BTreeMap::new(),
        }
    }

    pub fn with_environment(mut self, environment: BTreeMap<String, String>) -> Self {
        self.environment = environment;
        self
    }

    pub fn python(program: impl Into<PathBuf>, script: impl Into<String>) -> Self {
        Self::new(program, vec![script.into()])
    }

    pub fn powershell(program: impl Into<PathBuf>, script: impl Into<String>) -> Self {
        Self::new(
            program,
            vec![
                "-NoLogo".to_string(),
                "-NoProfile".to_string(),
                "-NonInteractive".to_string(),
                "-File".to_string(),
                script.into(),
            ],
        )
    }

    pub fn shell(program: impl Into<PathBuf>, script: impl Into<String>) -> Self {
        Self::new(program, vec![script.into()])
    }
}

enum WorkerLine {
    Stdout(String),
    Stderr(String),
}

impl CapabilityRunner for WorkerProcessRunner {
    fn run(&self, job: &CapabilityJob, control: &CapabilityControl) -> CapabilityResult {
        if control.is_cancelled() {
            return CapabilityResult::cancelled(&job.job_id);
        }
        if job.abi_version != CAPABILITY_ABI_VERSION {
            return CapabilityResult::failed(
                &job.job_id,
                "worker ABI version mismatch",
                Some("abi_mismatch"),
            );
        }
        let mut command = Command::new(&self.program);
        command
            .args(&self.arguments)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env_clear()
            .envs(&self.environment)
            .env("DELTA_CAPABILITY_ABI", CAPABILITY_ABI_VERSION.to_string());
        // A clean environment is the secret boundary. Only operating-system
        // bootstrap variables are inherited; provider credentials and the
        // parent process environment are never exposed implicitly.
        for name in ["SystemRoot", "WINDIR", "TEMP", "TMP"] {
            if let Some(value) = std::env::var_os(name) {
                command.env(name, value);
            }
        }
        if let Some(workspace) = &job.workspace {
            command.current_dir(workspace);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                return CapabilityResult::failed(
                    &job.job_id,
                    &format!("worker start failed: {error}"),
                    Some("worker_start"),
                )
            }
        };
        if let Some(mut stdin) = child.stdin.take() {
            match serde_json::to_vec(job) {
                Ok(payload) => {
                    if stdin.write_all(&payload).is_err() || stdin.write_all(b"\n").is_err() {
                        let _ = child.kill();
                        return CapabilityResult::failed(
                            &job.job_id,
                            "worker input failed",
                            Some("worker_stdin"),
                        );
                    }
                }
                Err(error) => {
                    let _ = child.kill();
                    return CapabilityResult::failed(
                        &job.job_id,
                        &error.to_string(),
                        Some("job_serialize"),
                    );
                }
            }
        }
        let (lines_tx, lines_rx) = mpsc::channel();
        let stdout_worker = child.stdout.take().map(|stdout| {
            let sender = lines_tx.clone();
            std::thread::spawn(move || {
                for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                    let _ = sender.send(WorkerLine::Stdout(line));
                }
            })
        });
        let stderr_worker = child.stderr.take().map(|stderr| {
            let sender = lines_tx;
            std::thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    let _ = sender.send(WorkerLine::Stderr(line));
                }
            })
        });
        let started = Instant::now();
        let timeout = (job.timeout_secs > 0).then(|| Duration::from_secs(job.timeout_secs));
        let mut stdout_lines = Vec::new();
        let mut stderr_lines = Vec::new();
        let mut result = None;
        let forced_state = loop {
            drain_worker_lines(
                &lines_rx,
                control,
                &mut stdout_lines,
                &mut stderr_lines,
                &mut result,
            );
            if control.is_cancelled() {
                let _ = child.kill();
                let _ = child.wait();
                break Some(CapabilityExitState::Cancelled);
            }
            if timeout.is_some_and(|timeout| started.elapsed() >= timeout) {
                let _ = child.kill();
                let _ = child.wait();
                break Some(CapabilityExitState::TimedOut);
            }
            match child.try_wait() {
                Ok(Some(status)) => {
                    if !status.success() && result.is_none() {
                        result = Some(CapabilityResult::failed(
                            &job.job_id,
                            &format!("worker exited with {status}"),
                            Some("worker_exit"),
                        ));
                    }
                    break None;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(10)),
                Err(error) => {
                    let _ = child.kill();
                    result = Some(CapabilityResult::failed(
                        &job.job_id,
                        &format!("worker wait failed: {error}"),
                        Some("worker_wait"),
                    ));
                    break None;
                }
            }
        };
        if let Some(worker) = stdout_worker {
            let _ = worker.join();
        }
        if let Some(worker) = stderr_worker {
            let _ = worker.join();
        }
        drain_worker_lines(
            &lines_rx,
            control,
            &mut stdout_lines,
            &mut stderr_lines,
            &mut result,
        );
        let mut result = match forced_state {
            Some(CapabilityExitState::Cancelled) => CapabilityResult::cancelled(&job.job_id),
            Some(CapabilityExitState::TimedOut) => CapabilityResult::timed_out(&job.job_id),
            _ => result.unwrap_or_else(|| {
                if stdout_lines.is_empty() {
                    CapabilityResult::failed(
                        &job.job_id,
                        "worker returned no typed result",
                        Some("worker_protocol"),
                    )
                } else {
                    CapabilityResult::completed(&job.job_id, Value::String(stdout_lines.join("\n")))
                }
            }),
        };
        if !stderr_lines.is_empty() {
            result = result.with_stderr(stderr_lines);
        }
        if result.abi_version != CAPABILITY_ABI_VERSION || result.job_id != job.job_id {
            return CapabilityResult::failed(
                &job.job_id,
                "worker result did not match the requested ABI job",
                Some("worker_protocol"),
            )
            .with_stderr(
                result
                    .diagnostics
                    .map(|diagnostics| diagnostics.stderr_lines)
                    .unwrap_or_default(),
            );
        }
        result
    }
}

fn drain_worker_lines(
    receiver: &mpsc::Receiver<WorkerLine>,
    control: &CapabilityControl,
    stdout_lines: &mut Vec<String>,
    stderr_lines: &mut Vec<String>,
    result: &mut Option<CapabilityResult>,
) {
    while let Ok(line) = receiver.try_recv() {
        match line {
            WorkerLine::Stderr(line) => stderr_lines.push(line),
            WorkerLine::Stdout(line) => {
                let parsed = serde_json::from_str::<Value>(&line).ok();
                if let Some(frame) = parsed.as_ref().filter(|value| value["type"] == "progress") {
                    if let Ok(progress) = serde_json::from_value::<CapabilityProgress>(
                        frame.get("data").cloned().unwrap_or_else(|| frame.clone()),
                    ) {
                        control.emit_progress(progress);
                    }
                } else if let Some(frame) = parsed {
                    let payload = if frame["type"] == "result" {
                        frame.get("data").cloned().unwrap_or(Value::Null)
                    } else {
                        frame
                    };
                    if let Ok(typed) = serde_json::from_value::<CapabilityResult>(payload) {
                        *result = Some(typed);
                    } else {
                        stdout_lines.push(line);
                    }
                } else {
                    stdout_lines.push(line);
                }
            }
        }
    }
}

/// MCP runner adapter. MCP servers remain workers; the adapter wraps a model
/// tool call in a typed `mcp.call` job consumed by a configured MCP bridge.
pub struct McpCapabilityRunner {
    server_id: String,
    worker: WorkerProcessRunner,
}

impl McpCapabilityRunner {
    pub fn new(server_id: impl Into<String>, worker: WorkerProcessRunner) -> Self {
        Self {
            server_id: server_id.into(),
            worker,
        }
    }
}

impl CapabilityRunner for McpCapabilityRunner {
    fn run(&self, job: &CapabilityJob, control: &CapabilityControl) -> CapabilityResult {
        let mut bridge_job = job.clone();
        bridge_job.capability_id = "mcp.call".to_string();
        bridge_job.arguments = serde_json::json!({
            "server_id": self.server_id,
            "tool": job.capability_id,
            "arguments": job.arguments,
        });
        self.worker.run(&bridge_job, control)
    }
}

fn collect_input_files(
    job: &CapabilityJob,
    workspace_write: bool,
) -> Result<Vec<CapabilityInputFile>, String> {
    let mut paths = Vec::new();
    if let Some(values) = job.arguments.get("input_files").and_then(Value::as_array) {
        paths.extend(values.iter().filter_map(Value::as_str).map(str::to_string));
    }
    if !workspace_write {
        if let Some(path) = job.arguments.get("path").and_then(Value::as_str) {
            paths.push(path.to_string());
        }
    }
    paths
        .into_iter()
        .map(|path| {
            let absolute = resolve_workspace_path(job.workspace.as_deref(), &path)?;
            let bytes = std::fs::read(&absolute)
                .map_err(|error| format!("input file is unavailable: {path}: {error}"))?;
            Ok(CapabilityInputFile {
                path: absolute.to_string_lossy().to_string(),
                sha256: Some(format!("{:x}", sha2::Sha256::digest(&bytes))),
                size: Some(bytes.len() as u64),
            })
        })
        .collect()
}

fn validate_input_files(job: &CapabilityJob) -> Result<(), String> {
    for input in &job.input_files {
        let path = PathBuf::from(&input.path)
            .canonicalize()
            .map_err(|error| format!("input path is unavailable: {error}"))?;
        if !is_under_roots(&path, &job.grants.read_roots) {
            return Err(format!("input path is outside read grants: {}", input.path));
        }
        let bytes = std::fs::read(&path).map_err(|error| error.to_string())?;
        if input
            .sha256
            .as_deref()
            .is_some_and(|hash| hash != format!("{:x}", sha2::Sha256::digest(&bytes)))
        {
            return Err(format!(
                "input hash changed before execution: {}",
                input.path
            ));
        }
    }
    Ok(())
}

fn resolve_workspace_path(workspace: Option<&str>, path: &str) -> Result<PathBuf, String> {
    let candidate = PathBuf::from(path);
    let candidate = if candidate.is_absolute() {
        candidate
    } else {
        PathBuf::from(workspace.ok_or_else(|| "workspace is required".to_string())?).join(candidate)
    };
    candidate
        .canonicalize()
        .map_err(|error| format!("path is unavailable: {error}"))
}

fn is_under_roots(path: &Path, roots: &[String]) -> bool {
    roots.iter().any(|root| {
        PathBuf::from(root)
            .canonicalize()
            .is_ok_and(|root| path.starts_with(root))
    })
}

fn native_read_registration() -> CapabilityRegistration {
    CapabilityRegistration {
        capability_id: "file.read".to_string(),
        tool_name: "read_file".to_string(),
        description: "Read a UTF-8 text file from the trusted workspace.".to_string(),
        parameters: serde_json::json!({
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}}
        }),
        metadata: serde_json::json!({
            "risk_level": "low", "requires_approval": false,
            "category": "read", "capabilities": ["file.read"]
        }),
        grants: CapabilityGrants::read_only(),
        workspace_write: false,
        runner: Arc::new(NativeCapabilityRunner::new(|job, control| {
            control.emit_progress(CapabilityProgress {
                job_id: job.job_id.clone(),
                fraction: 0.0,
                stage: Some("reading".to_string()),
                message: None,
            });
            let Some(path) = job.arguments.get("path").and_then(Value::as_str) else {
                return CapabilityResult::failed(
                    &job.job_id,
                    "path is required",
                    Some("arguments"),
                );
            };
            let path = match resolve_workspace_path(job.workspace.as_deref(), path) {
                Ok(path) if is_under_roots(&path, &job.grants.read_roots) => path,
                Ok(_) => {
                    return CapabilityResult::failed(
                        &job.job_id,
                        "path is outside read grants",
                        Some("grant_denied"),
                    )
                }
                Err(error) => {
                    return CapabilityResult::failed(&job.job_id, &error, Some("read_failed"))
                }
            };
            match std::fs::read_to_string(path) {
                Ok(text) => CapabilityResult::completed(
                    &job.job_id,
                    serde_json::json!({"ok": true, "text": text}),
                ),
                Err(error) => {
                    CapabilityResult::failed(&job.job_id, &error.to_string(), Some("read_failed"))
                }
            }
        })),
    }
}

fn native_write_registration() -> CapabilityRegistration {
    CapabilityRegistration {
        capability_id: "file.write".to_string(),
        tool_name: "write_file".to_string(),
        description: "Create a UTF-8 file artifact in the trusted workspace.".to_string(),
        parameters: serde_json::json!({
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"}
            }
        }),
        metadata: serde_json::json!({
            "risk_level": "medium", "requires_approval": true,
            "category": "filesystem", "capabilities": ["file.write"]
        }),
        grants: CapabilityGrants::default(),
        workspace_write: true,
        runner: Arc::new(NativeCapabilityRunner::new(|job, control| {
            if control.is_cancelled() {
                return CapabilityResult::cancelled(&job.job_id);
            }
            let Some(relative_path) = job.arguments.get("path").and_then(Value::as_str) else {
                return CapabilityResult::failed(
                    &job.job_id,
                    "path is required",
                    Some("arguments"),
                );
            };
            let Some(content) = job.arguments.get("content").and_then(Value::as_str) else {
                return CapabilityResult::failed(
                    &job.job_id,
                    "content is required",
                    Some("arguments"),
                );
            };
            let Some(staging_dir) = job.artifact_staging_dir.as_deref() else {
                return CapabilityResult::failed(
                    &job.job_id,
                    "artifact staging is unavailable",
                    Some("staging"),
                );
            };
            let staged = PathBuf::from(staging_dir).join("candidate");
            if let Err(error) = std::fs::write(&staged, content.as_bytes()) {
                return CapabilityResult::failed(
                    &job.job_id,
                    &error.to_string(),
                    Some("write_failed"),
                );
            }
            control.emit_progress(CapabilityProgress {
                job_id: job.job_id.clone(),
                fraction: 1.0,
                stage: Some("staged".to_string()),
                message: None,
            });
            CapabilityResult::completed(&job.job_id, serde_json::json!({"ok": true})).with_artifact(
                CapabilityArtifact {
                    staging_path: staged.to_string_lossy().to_string(),
                    relative_path: relative_path.to_string(),
                    kind: Some("file".to_string()),
                    sha256: None,
                    size: None,
                    incomplete: false,
                },
            )
        })),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_version_constant() {
        assert_eq!(CapabilityJob::version(), CAPABILITY_ABI_VERSION);
        assert_eq!(CAPABILITY_ABI_VERSION, 2);
    }

    #[test]
    fn job_auto_versions_and_ids() {
        let job = CapabilityJob::new("shell.exec", "job-1", serde_json::json!({"cmd": "ls"}));
        assert_eq!(job.abi_version, CAPABILITY_ABI_VERSION);
        assert_eq!(job.capability_id, "shell.exec");
        assert_eq!(job.job_id, "job-1");
        assert!(job.workspace.is_none());
        assert!(job.input_files.is_empty());
    }

    #[test]
    fn builder_sets_bounds() {
        let job = CapabilityJob::new("file.read", "job-2", serde_json::json!({}))
            .workspace("/ws")
            .running_f_in(CapabilityInputFile {
                path: "/ws/a.txt".to_string(),
                sha256: Some("abc".to_string()),
                size: Some(3),
            })
            .grants(CapabilityGrants {
                read_roots: vec!["/ws".to_string()],
                write_roots: Vec::new(),
                network: Vec::new(),
                secrets: Vec::new(),
                exec: false,
            })
            .with_timeout(30);
        assert_eq!(job.workspace.as_deref(), Some("/ws"));
        assert_eq!(job.input_files.len(), 1);
        assert_eq!(job.grants.read_roots, vec!["/ws"]);
        assert_eq!(job.timeout_secs, 30);
        assert!(!job.grants.exec);
    }

    #[test]
    fn read_only_grants_have_no_write_roots() {
        let g = CapabilityGrants::read_only();
        assert!(g.write_roots.is_empty());
        assert!(!g.exec);
    }

    #[test]
    fn result_terminates_with_state() {
        let c = CapabilityResult::completed("j", serde_json::json!({"ok": true}));
        assert_eq!(c.state, CapabilityExitState::Completed);
        assert_eq!(c.result.as_ref().unwrap()["ok"], serde_json::json!(true));

        let f = CapabilityResult::failed("j", "boom", Some("E_RUN"));
        assert_eq!(f.state, CapabilityExitState::Failed);
        assert_eq!(
            f.diagnostics.as_ref().unwrap().error_code.as_deref(),
            Some("E_RUN")
        );

        let t = CapabilityResult::timed_out("j");
        assert_eq!(t.state, CapabilityExitState::TimedOut);
    }

    #[test]
    fn artifacts_stream_into_result() {
        let r = CapabilityResult::completed("j", serde_json::json!("ok"))
            .with_artifact(CapabilityArtifact {
                staging_path: "/ws/.delta/staging/run/job/out.csv".to_string(),
                relative_path: "out.csv".to_string(),
                kind: Some("csv".to_string()),
                sha256: Some("d41d8".to_string()),
                size: Some(5),
                incomplete: false,
            })
            .with_stderr(["warn: deprecation".to_string()]);
        assert_eq!(r.artifacts.len(), 1);
        assert_eq!(r.artifacts[0].kind.as_deref(), Some("csv"));
        let diag = r.diagnostics.unwrap();
        assert_eq!(diag.stderr_lines, vec!["warn: deprecation"]);
    }

    #[test]
    fn serde_round_trip_preserves_fields() {
        let mut job = CapabilityJob::new(
            "web.fetch",
            "job-3",
            serde_json::json!({"url": "https://e"}),
        );
        job.workspace = Some("/ws".to_string());
        job.timeout_secs = 10;
        let json = serde_json::to_value(&job).unwrap();
        let back: CapabilityJob = serde_json::from_value(json).unwrap();
        assert_eq!(back.capability_id, "web.fetch");
        assert_eq!(back.workspace.as_deref(), Some("/ws"));
        assert_eq!(back.timeout_secs, 10);
        assert_eq!(back.abi_version, CAPABILITY_ABI_VERSION);
    }

    fn tool_context(workspace: &Path) -> ToolExecutionContext {
        ToolExecutionContext {
            session_id: "session-1".to_string(),
            run_id: "run-1".to_string(),
            workspace: Some(workspace.to_string_lossy().to_string()),
            timeout: Duration::from_secs(2),
            cancel: Arc::new(AtomicBool::new(false)),
            progress: Arc::new(|_| {}),
        }
    }

    #[test]
    fn product_registry_owns_stable_model_tool_schemas() {
        let host = CapabilityHost::product_defaults().unwrap();
        let schemas = host.tool_schemas();
        let names = schemas
            .as_array()
            .unwrap()
            .iter()
            .map(|schema| schema["function"]["name"].as_str().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(names, vec!["read_file", "write_file"]);
    }

    #[test]
    fn native_read_hashes_and_reads_only_from_workspace() {
        let temp = tempfile::tempdir().unwrap();
        std::fs::write(temp.path().join("input.txt"), "hello capability").unwrap();
        let host = CapabilityHost::product_defaults().unwrap();
        let result = host.execute(
            &ToolCall {
                id: "call-read".to_string(),
                name: "read_file".to_string(),
                arguments: serde_json::json!({"path": "input.txt"}),
            },
            &tool_context(temp.path()),
        );
        assert_eq!(result.state, crate::runtime::ToolExitState::Completed);
        assert_eq!(result.output["text"], "hello capability");
    }

    #[test]
    fn native_write_only_stages_an_artifact_for_runtime_promotion() {
        let temp = tempfile::tempdir().unwrap();
        let host = CapabilityHost::product_defaults().unwrap();
        let result = host.execute(
            &ToolCall {
                id: "call-write".to_string(),
                name: "write_file".to_string(),
                arguments: serde_json::json!({"path": "reports/result.txt", "content": "ready"}),
            },
            &tool_context(temp.path()),
        );
        assert_eq!(result.state, crate::runtime::ToolExitState::Completed);
        assert_eq!(result.staged_artifacts.len(), 1);
        assert_eq!(
            result.staged_artifacts[0].relative_path,
            "reports/result.txt"
        );
        assert_eq!(
            std::fs::read_to_string(&result.staged_artifacts[0].staging_path).unwrap(),
            "ready"
        );
        assert!(!temp.path().join("reports/result.txt").exists());
    }

    #[test]
    fn native_runner_observes_runtime_cancellation() {
        let cancel = Arc::new(AtomicBool::new(true));
        let control = CapabilityControl::new(cancel, Arc::new(|_| {}));
        let runner = NativeCapabilityRunner::new(|job, _| {
            CapabilityResult::completed(&job.job_id, serde_json::json!({"ok": true}))
        });
        let result = runner.run(
            &CapabilityJob::new("native.test", "job-cancel", serde_json::json!({})),
            &control,
        );
        assert_eq!(result.state, CapabilityExitState::Cancelled);
    }

    #[test]
    fn worker_process_consumes_abi_job_and_returns_stdout() {
        let temp = tempfile::tempdir().unwrap();
        #[cfg(windows)]
        let runner = {
            let system_root = std::env::var_os("SystemRoot").unwrap();
            WorkerProcessRunner::new(
                PathBuf::from(system_root).join("System32").join("cmd.exe"),
                vec![
                    "/D".to_string(),
                    "/S".to_string(),
                    "/C".to_string(),
                    "set /p DELTA_JOB= & echo worker-ok".to_string(),
                ],
            )
        };
        #[cfg(unix)]
        let runner = WorkerProcessRunner::new(
            "/bin/sh",
            vec![
                "-c".to_string(),
                "IFS= read -r DELTA_JOB; printf '%s\\n' worker-ok".to_string(),
            ],
        );
        let mut job = CapabilityJob::new("shell.test", "job-worker", serde_json::json!({}));
        job.workspace = Some(temp.path().to_string_lossy().to_string());
        job.timeout_secs = 2;
        let control = CapabilityControl::new(Arc::new(AtomicBool::new(false)), Arc::new(|_| {}));
        let result = runner.run(&job, &control);
        assert_eq!(result.state, CapabilityExitState::Completed);
        assert_eq!(result.result, Some(Value::String("worker-ok".to_string())));
    }

    #[test]
    fn worker_process_is_force_terminated_at_runtime_timeout() {
        let temp = tempfile::tempdir().unwrap();
        #[cfg(windows)]
        let runner = {
            let system_root = PathBuf::from(std::env::var_os("SystemRoot").unwrap());
            WorkerProcessRunner::new(
                system_root
                    .join("System32")
                    .join("WindowsPowerShell")
                    .join("v1.0")
                    .join("powershell.exe"),
                vec![
                    "-NoLogo".to_string(),
                    "-NoProfile".to_string(),
                    "-NonInteractive".to_string(),
                    "-Command".to_string(),
                    "$null = [Console]::In.ReadLine(); Start-Sleep -Seconds 5".to_string(),
                ],
            )
        };
        #[cfg(unix)]
        let runner = WorkerProcessRunner::new(
            "/bin/sh",
            vec![
                "-c".to_string(),
                "IFS= read -r DELTA_JOB; exec /bin/sleep 5".to_string(),
            ],
        );
        let mut job = CapabilityJob::new("shell.test", "job-timeout", serde_json::json!({}));
        job.workspace = Some(temp.path().to_string_lossy().to_string());
        job.timeout_secs = 1;
        let control = CapabilityControl::new(Arc::new(AtomicBool::new(false)), Arc::new(|_| {}));
        let started = Instant::now();
        let result = runner.run(&job, &control);
        assert_eq!(result.state, CapabilityExitState::TimedOut);
        assert!(started.elapsed() < Duration::from_secs(4));
    }
}
