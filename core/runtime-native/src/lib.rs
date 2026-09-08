//! Delta Core Rust runtime — state foundation (R1) + R2 shadow readers (ADR-019).
//!
//! This crate provides read-only shadow access to the Python Runtime's
//! SQLite stores (run_events.db, side-effects.db, sources.db) and
//! deterministic rule evaluators (validation, citation ranges, recovery
//! checkpoint schema). It does NOT write. Authority remains with the
//! Legacy Python Runtime until R1/R2 switches.
//!
//! See:
//!   - docs/architecture/adr/ADR-009-delta-core-architecture.md
//!   - docs/architecture/adr/ADR-019-r2-pre-plumbing.md
//!   - docs/architecture/runtime-public-contract.md
//!   - docs/governance/rust-core-migration.md §5 (R1 — State Foundation)
//!     and §5 (R2 — Trusted Execution, shadow-read phase)

pub mod artifact;
pub mod checkpoint;
pub mod idemlog;
pub mod ledger;
pub mod source_citation;
pub mod taskstore;
pub mod validation;

pub use artifact::{
    ArtifactInput, ArtifactMismatch, ArtifactReader, ArtifactRecord, ArtifactRegistrationResult,
    ArtifactRegistryWriter,
};
pub use checkpoint::{parse_snapshot, read_snapshot_file, ParsedSnapshot, SNAPSHOT_SCHEMA_VERSION};
pub use idemlog::{
    args_sha256, operation_id, IdempotencyReader, IdempotencyWriter, SideEffectEntry,
    SideEffectState,
};
pub use ledger::{LedgerEvent, LedgerReader, LedgerWriter};
pub use source_citation::{
    validate_all, validate_citation, validate_source_citation, CitationValidationResult,
    CitationValidity, ValidatedCitation,
};
pub use taskstore::{ScheduledTaskEntry, TaskEntry, TaskRunEntry, TaskStore, TaskStoreReader};
pub use validation::{run_validation, ValidationCheck, ValidationResult};

pub use thiserror::Error;

#[derive(Debug, Error)]
pub enum ShadowReadError {
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),

    #[error("json decode error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("parse error: {0}")]
    Parse(String),
}
