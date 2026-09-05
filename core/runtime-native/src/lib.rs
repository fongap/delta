//! Delta Core Rust runtime — state foundation (R1).
//!
//! This crate provides read-only shadow access to the Python Runtime's
//! SQLite stores (run_events.db, side-effects.db). It does NOT write.
//! Authority remains with the Legacy Python Runtime until R1 switch.
//!
//! See:
//!   - docs/architecture/adr/ADR-009-delta-core-architecture.md
//!   - docs/architecture/runtime-public-contract.md
//!   - docs/governance/rust-core-migration.md §5 (R1 — State Foundation)

pub mod idemlog;
pub mod ledger;

pub use idemlog::{IdempotencyReader, SideEffectEntry, SideEffectState};
pub use ledger::{LedgerEvent, LedgerReader};

pub use thiserror::Error;

#[derive(Debug, Error)]
pub enum ShadowReadError {
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),

    #[error("json decode error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}
