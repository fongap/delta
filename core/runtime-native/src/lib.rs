//! Delta Core Rust runtime — state foundation (R1) + R2 Trusted Execution (ADR-029).
//!
//! This crate provides the authoritative Rust implementations for R1/R2 domains.
//! Checkpoint authority (ADR-029) persists checkpoints as `checkpoint.registered`
//! events in the run-event ledger (`run_events.db`).
//!
//! See:
//!   - docs/architecture/adr/ADR-009-delta-core-architecture.md
//!   - docs/architecture/adr/ADR-029-r2-checkpoint-hard-cut.md
//!   - docs/architecture/runtime-public-contract.md
//!   - docs/governance/rust-core-migration.md

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
pub use checkpoint::{
    CheckpointReader, CheckpointRegisterInput, CheckpointValidationResult, CheckpointWriter,
    CHECKPOINT_SCHEMA_VERSION,
};
pub use idemlog::{
    args_sha256, operation_id, IdempotencyReader, IdempotencyWriter, SideEffectEntry,
    SideEffectState,
};
pub use ledger::{LedgerEvent, LedgerReader, LedgerWriter};
pub use source_citation::{
    validate_all, validate_citation, validate_source_citation, CitationValidationResult,
    CitationValidity, SourceCitationReader, SourceCitationWriter, SourceRecord,
    SourceRegisterInput, ValidatedCitation,
};
pub use taskstore::{ScheduledTaskEntry, TaskRunEntry, TaskStore};
pub use validation::{
    run_validation, ValidationCheck, ValidationReader, ValidationRecord, ValidationRegisterInput,
    ValidationResult, ValidationWriter,
};

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
