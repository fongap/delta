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

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The Capability ABI wire version. Bump on any breaking change to the contract.
pub const CAPABILITY_ABI_VERSION: u32 = 1;

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
    /// Path to the artifact (workspace-relative or absolute).
    pub path: String,
    /// Artifact kind (e.g. "file", "csv", "sheet", "image", "report").
    #[serde(default)]
    pub kind: Option<String>,
    #[serde(default)]
    pub sha256: Option<String>,
    #[serde(default)]
    pub size: Option<u64>,
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
    fn run(&self, job: &CapabilityJob) -> CapabilityResult;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_version_constant() {
        assert_eq!(CapabilityJob::version(), CAPABILITY_ABI_VERSION);
        assert_eq!(CAPABILITY_ABI_VERSION, 1);
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
                path: "/ws/out.csv".to_string(),
                kind: Some("csv".to_string()),
                sha256: Some("d41d8".to_string()),
                size: Some(5),
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
}
