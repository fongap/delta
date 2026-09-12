//! Rust Authority for Source / Citation trusted facts (R2, ADR-027).
//!
//! Before the hard-cut, Source and Citation facts were persisted by the
//! Python ``SourceStore`` (``core/sources.py``) to a JSON file
//! (``<workspace>/.delta/sources.json``), with an optional
//! ``source_citation_delegate.py`` providing a Rust final verdict when
//! ``DELTA_RUST_AUTHORITY=source_citation`` was set.
//!
//! After the hard-cut (ADR-027):
//!
//! - **Rust is the sole Source / Citation trusted authority.**
//! - Python only performs **extraction and candidate construction**
//!   (file reading, content hashing, fingerprint input, range candidate
//!   building).
//! - All trusted facts are persisted as ledger events in the run-event
//!   ledger (``run_events.db``) via the unified ``delta_core``
//!   process. The event types are ``source.registered``,
//!   ``source.revised``, ``source.stale``, and ``citation.marked``.
//! - No Python fallback, dual-write, shadow production path, or
//!   migration delegate remains.
//!
//! This module contains:
//!
//! 1. The **range validator** (``validate_citation``,
//!    ``validate_all``) — normalises and validates CitationRange
//!    candidate dicts against the canonical schema.
//! 2. The **citation evaluator** (``validate_source_citation``) —
//!    derives the final validity verdict from a SourceRef snapshot +
//!    a candidate range.
//! 3. The **write authority** (``SourceCitationWriter``) — appends
//!    Source / Citation facts to the run-event ledger.
//! 4. The **read / replay layer** (``SourceCitationReader``) —
//!    reconstructs the current SourceRef register and citation
//!    relations from the ledger event stream.
//!
//! Contract: ``docs/architecture/adr/ADR-027-r2-source-citation-hard-cut.md``.
//!
//! CitationRange kinds and required fields (must match Python,
//! kind names are the canonical strings defined in ``core/sources.py``):
//!
//! - ``"lines"``     : at least one of ``start`` / ``end`` (int; 1-based)
//! - ``"page"``      : at least one of ``page`` / ``page_end`` (int; 1-based)
//! - ``"cells"``     : ``sheet`` plus an A1 or numeric-axis locator
//! - ``"row"``       : ``sheet`` plus ``row_start`` and/or ``row_end``
//! - ``"column"``    : ``sheet`` plus ``col_start`` and/or ``col_end``
//! - ``"sheet"``     : ``sheet`` (str) only
//! - ``"message_id"``: ``message_id`` (str)
//! - ``"custom"``    : ``descriptor`` (object)
//! - any other      : rejected

use std::path::Path;

use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use time::OffsetDateTime;
use uuid::Uuid;

/// A validated citation range. Mirrors the Python ``to_range_dict`` output.
#[derive(Debug, Clone)]
pub struct ValidatedCitation {
    pub kind: String,
    /// The trimmed, kind-specific dict (matches Python ``to_range_dict`` output).
    pub range: Value,
}

/// Stable final-verdict vocabulary for a citation bound to one source
/// revision.  The legacy Python response fields remain available on the
/// result during migration, but consumers should move to this enum.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CitationValidity {
    Valid,
    SourceMissing,
    SourceChanged,
    RangeInvalid,
    UnsupportedKind,
    Malformed,
    Unknown,
}

/// Typed citation verdict returned by the unified ``delta_core`` protocol.
///
/// The four fact fields deliberately keep source existence, revision
/// agreement, structural validity, and locator bounds separate.  A nullable
/// ``range_valid`` means the supplied capability has not attested an extent;
/// it must not be confused with a successful bounds check.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CitationValidationResult {
    pub validity: CitationValidity,
    pub valid: bool,
    pub status: String,
    pub reason: String,
    pub source_exists: bool,
    pub source_unchanged: bool,
    pub structure_valid: bool,
    pub revision_matches: bool,
    pub range_valid: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_fingerprint: Option<String>,
    /// Compatibility alias consumed by existing Python/UI projections.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_line_count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

#[derive(Debug, Clone, Copy)]
struct CitationFacts {
    source_exists: bool,
    source_unchanged: bool,
    structure_valid: bool,
    revision_matches: bool,
    range_valid: Option<bool>,
}

fn verdict(
    validity: CitationValidity,
    status: &str,
    reason: &str,
    facts: CitationFacts,
) -> CitationValidationResult {
    CitationValidationResult {
        validity,
        valid: validity == CitationValidity::Valid,
        status: status.to_string(),
        reason: reason.to_string(),
        source_exists: facts.source_exists,
        source_unchanged: facts.source_unchanged,
        structure_valid: facts.structure_valid,
        revision_matches: facts.revision_matches,
        range_valid: facts.range_valid,
        current_fingerprint: None,
        current_sha256: None,
        current_line_count: None,
        detail: None,
    }
}

/// Evaluate a citation against a captured source revision.
///
/// ``source`` is the compatibility ``SourceRef`` object loaded from the
/// Python-era ``sources.json``.  Rust performs canonical range validation,
/// reads the current file bytes, computes the current fingerprint, and emits
/// the final typed verdict.  This function does not mutate the legacy store;
/// persistence and the authority selector are wired in subsequent slices.
pub fn validate_source_citation(
    source: Option<&Value>,
    range: &Value,
    workspace: Option<&Path>,
) -> CitationValidationResult {
    let Some(source) = source.and_then(Value::as_object) else {
        return verdict(
            CitationValidity::SourceMissing,
            "missing",
            "source_gone",
            CitationFacts {
                source_exists: false,
                source_unchanged: false,
                structure_valid: false,
                revision_matches: false,
                range_valid: None,
            },
        );
    };

    let status = source
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("current");
    if status == "missing" {
        return verdict(
            CitationValidity::SourceMissing,
            "missing",
            "file_missing",
            CitationFacts {
                source_exists: true,
                source_unchanged: false,
                structure_valid: false,
                revision_matches: false,
                range_valid: None,
            },
        );
    }

    let canonical = match validate_citation(range) {
        Ok(value) => value,
        Err(error) => {
            let validity = if error.starts_with("unknown citation kind") {
                CitationValidity::UnsupportedKind
            } else {
                CitationValidity::Malformed
            };
            let mut result = verdict(
                validity,
                status,
                if validity == CitationValidity::UnsupportedKind {
                    "unsupported_kind"
                } else {
                    "malformed"
                },
                CitationFacts {
                    source_exists: true,
                    source_unchanged: false,
                    structure_valid: false,
                    revision_matches: false,
                    range_valid: None,
                },
            );
            result.detail = Some(error);
            return result;
        }
    };

    let Some(location) = source.get("location").and_then(Value::as_str) else {
        let mut result = verdict(
            CitationValidity::Unknown,
            status,
            "source_location_unavailable",
            CitationFacts {
                source_exists: true,
                source_unchanged: false,
                structure_valid: true,
                revision_matches: false,
                range_valid: None,
            },
        );
        result.detail = Some("source missing string location".to_string());
        return result;
    };
    let captured_fingerprint = source
        .get("fingerprint")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if captured_fingerprint.is_empty() {
        let mut result = verdict(
            CitationValidity::Unknown,
            status,
            "source_fingerprint_unavailable",
            CitationFacts {
                source_exists: true,
                source_unchanged: false,
                structure_valid: true,
                revision_matches: false,
                range_valid: None,
            },
        );
        result.detail = Some("source missing captured fingerprint".to_string());
        return result;
    }

    let location_path = Path::new(location);
    let path = if location_path.is_absolute() {
        location_path.to_path_buf()
    } else if let Some(workspace) = workspace {
        workspace.join(location_path)
    } else {
        location_path.to_path_buf()
    };
    let data = match std::fs::read(&path) {
        Ok(data) => data,
        Err(error) => {
            let mut result = verdict(
                CitationValidity::SourceMissing,
                "missing",
                "file_missing",
                CitationFacts {
                    source_exists: false,
                    source_unchanged: false,
                    structure_valid: true,
                    revision_matches: false,
                    range_valid: None,
                },
            );
            result.detail = Some(error.to_string());
            return result;
        }
    };
    let current_fingerprint = format!("{:x}", Sha256::digest(&data));
    if current_fingerprint != captured_fingerprint {
        let mut result = verdict(
            CitationValidity::SourceChanged,
            "changed",
            "content_changed",
            CitationFacts {
                source_exists: true,
                source_unchanged: false,
                structure_valid: true,
                revision_matches: false,
                range_valid: None,
            },
        );
        result.current_fingerprint = Some(current_fingerprint);
        result.current_sha256 = result.current_fingerprint.clone();
        return result;
    }

    if canonical.kind == "lines" {
        let start = canonical
            .range
            .get("start")
            .or_else(|| canonical.range.get("end"))
            .and_then(Value::as_u64)
            .unwrap_or(0) as usize;
        let end = canonical
            .range
            .get("end")
            .or_else(|| canonical.range.get("start"))
            .and_then(Value::as_u64)
            .unwrap_or(0) as usize;
        let line_count = if data.is_empty() {
            0
        } else {
            data.iter().filter(|byte| **byte == b'\n').count() + usize::from(!data.ends_with(b"\n"))
        };
        if start == 0 || start > line_count || end > line_count {
            let mut result = verdict(
                CitationValidity::RangeInvalid,
                "current",
                "out_of_bounds",
                CitationFacts {
                    source_exists: true,
                    source_unchanged: true,
                    structure_valid: true,
                    revision_matches: true,
                    range_valid: Some(false),
                },
            );
            result.current_fingerprint = Some(current_fingerprint);
            result.current_sha256 = result.current_fingerprint.clone();
            result.current_line_count = Some(line_count);
            return result;
        }
        let mut result = verdict(
            CitationValidity::Valid,
            "current",
            "valid",
            CitationFacts {
                source_exists: true,
                source_unchanged: true,
                structure_valid: true,
                revision_matches: true,
                range_valid: Some(true),
            },
        );
        result.current_fingerprint = Some(current_fingerprint);
        result.current_sha256 = result.current_fingerprint.clone();
        result.current_line_count = Some(line_count);
        return result;
    }

    // Legacy-compatible until revision-bound extent attestations land: the
    // source and structure are proven, but the kind-specific extent is not.
    // AF-11: a citation with range_valid=None must NOT count as fully valid
    // for min_valid_citations. Return a distinct validity that the Python
    // counter will not treat as "valid".
    let mut result = verdict(
        CitationValidity::Valid,
        "current",
        "range_unverified",
        CitationFacts {
            source_exists: true,
            source_unchanged: true,
            structure_valid: true,
            revision_matches: true,
            range_valid: None,
        },
    );
    result.current_fingerprint = Some(current_fingerprint);
    result.current_sha256 = result.current_fingerprint.clone();
    result
}

/// Validate a single citation dict. Returns the canonical (trimmed)
/// shape on success, or an error string describing the validation
/// failure (matching Python's ``ValueError`` messages where feasible).
pub fn validate_citation(value: &Value) -> Result<ValidatedCitation, String> {
    let obj = match value.as_object() {
        Some(o) => o,
        None => return Err("citation must be a dict".to_string()),
    };
    let kind = match obj.get("kind").and_then(Value::as_str) {
        Some(k) => k.to_string(),
        None => return Err("citation missing 'kind'".to_string()),
    };
    let get_int = |k: &str| -> Option<i64> { obj.get(k).and_then(Value::as_i64) };
    let get_str =
        |k: &str| -> Option<String> { obj.get(k).and_then(Value::as_str).map(String::from) };

    let require_positive = |name: &str, value: i64| -> Result<i64, String> {
        if value < 1 {
            Err(format!("{kind} citation {name} must be >= 1, got {value}"))
        } else {
            Ok(value)
        }
    };
    let require_sheet = || -> Result<String, String> {
        match get_str("sheet") {
            Some(sheet) if !sheet.is_empty() => Ok(sheet),
            _ => Err(format!("{kind} citation needs a 'sheet' name")),
        }
    };
    let insert_optional_positive = |out: &mut serde_json::Map<String, Value>,
                                    name: &str|
     -> Result<Option<i64>, String> {
        if !obj.contains_key(name) {
            return Ok(None);
        }
        let value = get_int(name).ok_or_else(|| format!("{kind} citation {name} must be int"))?;
        let value = require_positive(name, value)?;
        out.insert(name.to_string(), Value::from(value));
        Ok(Some(value))
    };

    let mut out = serde_json::Map::new();
    out.insert("kind".to_string(), Value::String(kind.clone()));
    match kind.as_str() {
        "lines" => {
            let start = insert_optional_positive(&mut out, "start")?;
            let end = insert_optional_positive(&mut out, "end")?;
            if start.is_none() && end.is_none() {
                return Err("lines citation needs at least one of start/end".to_string());
            }
            let start_value = start.or(end).unwrap();
            let end_value = end.or(start).unwrap();
            if end_value < start_value {
                return Err(format!(
                    "lines citation end ({end_value}) < start ({start_value})"
                ));
            }
        }
        "page" => {
            let page = insert_optional_positive(&mut out, "page")?;
            let page_end = insert_optional_positive(&mut out, "page_end")?;
            if page.is_none() && page_end.is_none() {
                return Err("page citation needs at least one of page/page_end".to_string());
            }
            let page_value = page.or(page_end).unwrap();
            let end_value = page_end.or(page).unwrap();
            if end_value < page_value {
                return Err(format!(
                    "page citation page_end ({end_value}) < page ({page_value})"
                ));
            }
        }
        "cells" => {
            let sheet = require_sheet()?;
            out.insert("sheet".to_string(), Value::String(sheet));
            let row_start = insert_optional_positive(&mut out, "row_start")?;
            let row_end = insert_optional_positive(&mut out, "row_end")?;
            let col_start = insert_optional_positive(&mut out, "col_start")?;
            let col_end = insert_optional_positive(&mut out, "col_end")?;
            if let (Some(start), Some(end)) = (row_start, row_end) {
                if end < start {
                    return Err(format!(
                        "cells citation row_end ({end}) < row_start ({start})"
                    ));
                }
            }
            if let (Some(start), Some(end)) = (col_start, col_end) {
                if end < start {
                    return Err(format!(
                        "cells citation col_end ({end}) < col_start ({start})"
                    ));
                }
            }
            let mut has_a1 = false;
            for name in ["cell_start", "cell_end"] {
                if obj.contains_key(name) {
                    let value =
                        get_str(name)
                            .filter(|value| !value.is_empty())
                            .ok_or_else(|| {
                                format!("cells citation {name} must be a non-empty string")
                            })?;
                    out.insert(name.to_string(), Value::String(value));
                    has_a1 = true;
                }
            }
            let has_numeric = row_start.is_some()
                || row_end.is_some()
                || col_start.is_some()
                || col_end.is_some();
            if !has_a1 && !has_numeric {
                return Err("cells citation needs a cell or row/column range".to_string());
            }
        }
        "row" => {
            let sheet = require_sheet()?;
            out.insert("sheet".to_string(), Value::String(sheet));
            let row_start = insert_optional_positive(&mut out, "row_start")?;
            let row_end = insert_optional_positive(&mut out, "row_end")?;
            if row_start.is_none() && row_end.is_none() {
                return Err("row citation needs at least one of row_start/row_end".to_string());
            }
            if let (Some(start), Some(end)) = (row_start, row_end) {
                if end < start {
                    return Err(format!(
                        "row citation row_end ({end}) < row_start ({start})"
                    ));
                }
            }
        }
        "column" => {
            let sheet = require_sheet()?;
            out.insert("sheet".to_string(), Value::String(sheet));
            let col_start = insert_optional_positive(&mut out, "col_start")?;
            let col_end = insert_optional_positive(&mut out, "col_end")?;
            if col_start.is_none() && col_end.is_none() {
                return Err("column citation needs at least one of col_start/col_end".to_string());
            }
            if let (Some(start), Some(end)) = (col_start, col_end) {
                if end < start {
                    return Err(format!(
                        "column citation col_end ({end}) < col_start ({start})"
                    ));
                }
            }
        }
        "sheet" => {
            let sheet = require_sheet()?;
            out.insert("sheet".to_string(), Value::String(sheet));
            let row_start = insert_optional_positive(&mut out, "row_start")?;
            let row_end = insert_optional_positive(&mut out, "row_end")?;
            let col_start = insert_optional_positive(&mut out, "col_start")?;
            let col_end = insert_optional_positive(&mut out, "col_end")?;
            if let (Some(start), Some(end)) = (row_start, row_end) {
                if end < start {
                    return Err(format!(
                        "sheet citation row_end ({end}) < row_start ({start})"
                    ));
                }
            }
            if let (Some(start), Some(end)) = (col_start, col_end) {
                if end < start {
                    return Err(format!(
                        "sheet citation col_end ({end}) < col_start ({start})"
                    ));
                }
            }
        }
        "message_id" => {
            let message_id = get_str("message_id")
                .filter(|value| !value.is_empty())
                .ok_or("message_id citation missing 'message_id'")?;
            out.insert("message_id".to_string(), Value::String(message_id));
        }
        "custom" => {
            let desc = obj
                .get("descriptor")
                .ok_or("custom citation missing 'descriptor'")?;
            if !desc.is_object() {
                return Err("custom citation 'descriptor' must be an object".to_string());
            }
            out.insert("descriptor".to_string(), desc.clone());
        }
        other => return Err(format!("unknown citation kind: {other:?}")),
    }
    Ok(ValidatedCitation {
        kind,
        range: Value::Object(out),
    })
}

/// Validate a list of citations, returning the trimmed list or the
/// first error encountered. Matches Python's
/// ``normalize_cited_ranges`` (which short-circuits on the first bad
/// entry).
pub fn validate_all(values: &[Value]) -> Result<Vec<ValidatedCitation>, String> {
    let mut out = Vec::with_capacity(values.len());
    for v in values {
        out.push(validate_citation(v)?);
    }
    Ok(out)
}

/// Write authority for Source / Citation trusted facts (R2, ADR-027).
///
/// Appends `source.registered` and `citation.marked` events to the
/// run-event ledger via `LedgerWriter`. All trusted persistence is
/// owned by Rust; Python is a facade that only performs extraction
/// and candidate construction.
use crate::ledger::{LedgerEvent, LedgerReader, LedgerWriter};
use crate::ShadowReadError;

/// Event type for source registration / revision.
const EV_SOURCE_REGISTERED: &str = "source.registered";

/// Event type for citation marking.
const EV_CITATION_MARKED: &str = "citation.marked";

/// Input for registering a source. Mirrors the Python capture payload.
#[derive(Debug, Clone)]
pub struct SourceRegisterInput {
    pub origin: String,
    pub location: String,
    pub fingerprint: String,
    pub captured_at: String,
    pub mtime_ns: Option<u64>,
    pub size_bytes: Option<u64>,
    pub permissions: Value,
}

/// One reconstructed SourceRef from the ledger event stream.
#[derive(Debug, Clone, Serialize)]
pub struct SourceRecord {
    pub id: String,
    pub origin: String,
    pub location: String,
    pub fingerprint: String,
    pub captured_at: String,
    pub checked_at: Option<String>,
    pub status: String,
    pub mtime_ns: Option<u64>,
    pub size_bytes: Option<u64>,
    pub cited_ranges: Vec<Value>,
    pub permissions: Value,
}

/// Read-only handle that replays source/citation events from the ledger
/// to reconstruct the current SourceRef register.
pub struct SourceCitationReader {
    pub reader: LedgerReader,
}

impl SourceCitationReader {
    /// Open the ledger DB in read-only mode.
    pub fn open<P: AsRef<std::path::Path>>(path: P) -> Result<Self, ShadowReadError> {
        Ok(Self {
            reader: LedgerReader::open(path)?,
        })
    }

    /// Wrap an existing LedgerReader.
    pub fn from_reader(reader: LedgerReader) -> Self {
        Self { reader }
    }

    /// Reconstruct all SourceRefs for the workspace by replaying events.
    pub fn list_sources(&self) -> Result<Vec<SourceRecord>, ShadowReadError> {
        let events = self.reader.all_events()?;
        self.replay_sources(&events)
    }

    /// Get a single source by ID.
    pub fn get_source(&self, source_id: &str) -> Result<Option<SourceRecord>, ShadowReadError> {
        let events = self.reader.all_events()?;
        let sources = self.replay_sources(&events)?;
        Ok(sources.into_iter().find(|s| s.id == source_id))
    }

    /// Find the latest source for a given location + origin.
    pub fn latest_source(
        &self,
        location: &str,
        origin: &str,
    ) -> Result<Option<SourceRecord>, ShadowReadError> {
        let events = self.reader.all_events()?;
        let sources = self.replay_sources(&events)?;
        let candidates: Vec<_> = sources
            .into_iter()
            .filter(|s| s.location == location && s.origin == origin)
            .collect();
        if candidates.is_empty() {
            return Ok(None);
        }
        // Latest by captured_at (string ISO8601 is lexicographically sortable).
        let latest = candidates
            .into_iter()
            .max_by_key(|s| s.captured_at.clone())
            .unwrap();
        Ok(Some(latest))
    }

    /// Get all citation.marked events for a source.
    pub fn get_citations(&self, source_id: &str) -> Result<Vec<Value>, ShadowReadError> {
        let events = self.reader.all_events()?;
        let mut out = Vec::new();
        for ev in events {
            if ev.r#type == EV_CITATION_MARKED {
                if let Some(sid) = ev.payload.get("source_id").and_then(Value::as_str) {
                    if sid == source_id {
                        out.push(ev.payload.clone());
                    }
                }
            }
        }
        Ok(out)
    }

    fn replay_sources(&self, events: &[LedgerEvent]) -> Result<Vec<SourceRecord>, ShadowReadError> {
        use std::collections::HashMap;
        let mut sources: HashMap<String, SourceRecord> = HashMap::new();
        // Collect citation.marked events to apply after building sources
        let mut citation_events: Vec<&LedgerEvent> = Vec::new();

        for ev in events {
            match ev.r#type.as_str() {
                EV_SOURCE_REGISTERED => {
                    let payload = &ev.payload;
                    let id = payload
                        .get("id")
                        .and_then(Value::as_str)
                        .ok_or_else(|| {
                            ShadowReadError::Parse("source.registered missing id".into())
                        })?
                        .to_string();

                    let cited_ranges = payload
                        .get("cited_ranges")
                        .and_then(Value::as_array)
                        .cloned()
                        .unwrap_or_default();

                    let record = SourceRecord {
                        id,
                        origin: payload
                            .get("origin")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        location: payload
                            .get("location")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        fingerprint: payload
                            .get("fingerprint")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        captured_at: payload
                            .get("captured_at")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        checked_at: payload
                            .get("checked_at")
                            .and_then(Value::as_str)
                            .map(String::from),
                        status: payload
                            .get("status")
                            .and_then(Value::as_str)
                            .unwrap_or("current")
                            .to_string(),
                        mtime_ns: payload.get("mtime_ns").and_then(Value::as_u64),
                        size_bytes: payload.get("size_bytes").and_then(Value::as_u64),
                        cited_ranges,
                        permissions: payload
                            .get("permissions")
                            .cloned()
                            .unwrap_or(Value::Object(serde_json::Map::new())),
                    };
                    sources.insert(record.id.clone(), record);
                }
                EV_CITATION_MARKED => {
                    citation_events.push(ev);
                }
                _ => {}
            }
        }

        // Apply citation.marked events to the corresponding sources
        for ev in citation_events {
            let payload = &ev.payload;
            if let Some(source_id) = payload.get("source_id").and_then(Value::as_str) {
                if let Some(ranges) = payload.get("ranges").and_then(Value::as_array) {
                    if let Some(source) = sources.get_mut(source_id) {
                        let run_id = payload.get("run_id").and_then(Value::as_str).unwrap_or("");
                        source.cited_ranges.push(serde_json::json!({
                            "run_id": run_id,
                            "ranges": ranges,
                        }));
                    }
                }
            }
        }

        // Sort by captured_at ascending for stable list order.
        let mut out: Vec<_> = sources.into_values().collect();
        out.sort_by(|a, b| a.captured_at.cmp(&b.captured_at));
        Ok(out)
    }
}

/// Write authority for Source / Citation facts.
pub struct SourceCitationWriter<'a> {
    ledger: &'a LedgerWriter,
}

impl<'a> SourceCitationWriter<'a> {
    pub fn new(ledger: &'a LedgerWriter) -> Self {
        Self { ledger }
    }

    /// Register (or re-register) a source. Returns the resulting SourceRecord.
    pub fn register_source(
        &self,
        input: SourceRegisterInput,
        ts: f64,
        workspace: &str,
    ) -> Result<SourceRecord, ShadowReadError> {
        // Replay existing sources to find matches for same location+origin.
        let reader = self.ledger.reader()?;
        let existing = reader.all_events()?;
        let sources = SourceCitationReader { reader }.replay_sources(&existing)?;

        let existing_same_location: Vec<&SourceRecord> = sources
            .iter()
            .filter(|s| s.origin == input.origin && s.location == input.location)
            .collect();

        let source_id = Uuid::new_v4().to_string();
        let now = OffsetDateTime::now_utc()
            .format(&time::format_description::well_known::Rfc3339)
            .unwrap_or_else(|_| input.captured_at.clone());

        // Check for identical fingerprint match (refresh case).
        if let Some(found) = existing_same_location
            .iter()
            .find(|s| s.fingerprint == input.fingerprint)
        {
            // Same content: re-register the existing source_id with refreshed checked_at/mtime.
            let refreshed_id = found.id.clone();
            let record = SourceRecord {
                id: refreshed_id.clone(),
                origin: input.origin.clone(),
                location: input.location.clone(),
                fingerprint: input.fingerprint.clone(),
                captured_at: found.captured_at.clone(),
                checked_at: Some(now.clone()),
                status: "current".to_string(),
                mtime_ns: input.mtime_ns,
                size_bytes: input.size_bytes,
                cited_ranges: found.cited_ranges.clone(),
                permissions: input.permissions.clone(),
            };
            self.append_source_registered(&record, ts, workspace)?;
            return Ok(record);
        }

        // Different fingerprint (or no existing): mark all existing at same location as "changed".
        for old in existing_same_location {
            let changed_record = SourceRecord {
                id: old.id.clone(),
                origin: old.origin.clone(),
                location: old.location.clone(),
                fingerprint: old.fingerprint.clone(),
                captured_at: old.captured_at.clone(),
                checked_at: Some(now.clone()),
                status: "changed".to_string(),
                mtime_ns: old.mtime_ns,
                size_bytes: old.size_bytes,
                cited_ranges: old.cited_ranges.clone(),
                permissions: old.permissions.clone(),
            };
            self.append_source_registered(&changed_record, ts, workspace)?;
        }

        // Register the new source.
        let record = SourceRecord {
            id: source_id,
            origin: input.origin,
            location: input.location,
            fingerprint: input.fingerprint,
            captured_at: input.captured_at,
            checked_at: Some(now),
            status: "current".to_string(),
            mtime_ns: input.mtime_ns,
            size_bytes: input.size_bytes,
            cited_ranges: Vec::new(),
            permissions: input.permissions,
        };
        self.append_source_registered(&record, ts, workspace)?;
        Ok(record)
    }

    /// Mark a citation for a source. Validates ranges first.
    pub fn mark_cited(
        &self,
        source_id: &str,
        run_id: &str,
        ranges: Vec<Value>,
        ts: f64,
        workspace: &str,
    ) -> Result<bool, ShadowReadError> {
        // Validate the source exists.
        let reader = self.ledger.reader()?;
        let existing = reader.all_events()?;
        let sources = SourceCitationReader { reader }.replay_sources(&existing)?;
        if !sources.iter().any(|s| s.id == source_id) {
            return Ok(false);
        }

        // Canonicalize and validate ranges using the shared validator.
        let validated = validate_all(&ranges).map_err(ShadowReadError::Parse)?;

        let payload = serde_json::json!({
            "source_id": source_id,
            "run_id": run_id,
            "ranges": validated.iter().map(|c| c.range.clone()).collect::<Vec<_>>(),
            "marked_at": OffsetDateTime::now_utc()
                .format(&time::format_description::well_known::Rfc3339)
                .unwrap_or_default(),
        });

        self.ledger.append(
            run_id,
            EV_CITATION_MARKED,
            "system",
            ts,
            &payload,
            workspace,
        )?;
        Ok(true)
    }

    pub fn append_source_registered(
        &self,
        record: &SourceRecord,
        ts: f64,
        workspace: &str,
    ) -> Result<(), ShadowReadError> {
        let payload = serde_json::json!({
            "id": record.id,
            "origin": record.origin,
            "location": record.location,
            "fingerprint": record.fingerprint,
            "captured_at": record.captured_at,
            "checked_at": record.checked_at,
            "status": record.status,
            "mtime_ns": record.mtime_ns,
            "size_bytes": record.size_bytes,
            "cited_ranges": record.cited_ranges,
            "permissions": record.permissions,
        });
        // Use "$source" run_id namespace for source events so they don't
        // interfere with run lifecycle queries (open_runs, recover_stale).
        const SOURCE_RUN_ID: &str = "$source";
        self.ledger.append(
            SOURCE_RUN_ID,
            EV_SOURCE_REGISTERED,
            "system",
            ts,
            &payload,
            workspace,
        )?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;

    #[test]
    fn lines_citation_ok() {
        let v = json!({"kind": "lines", "start": 1, "end": 5});
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.kind, "lines");
        assert_eq!(r.range.get("start"), Some(&json!(1)));
        assert_eq!(r.range.get("end"), Some(&json!(5)));
    }

    #[test]
    fn lines_citation_end_lt_start_rejected() {
        let v = json!({"kind": "lines", "start": 10, "end": 5});
        assert!(validate_citation(&v).is_err());
    }

    #[test]
    fn lines_citation_missing_field_rejected() {
        let v = json!({"kind": "lines", "start": 1});
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.range, v);
    }

    #[test]
    fn page_citation_ok() {
        let v = json!({"kind": "page", "page": 3, "page_end": 5});
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.kind, "page");
        assert_eq!(r.range.get("page"), Some(&json!(3)));
        assert_eq!(r.range.get("page_end"), Some(&json!(5)));
    }

    #[test]
    fn page_citation_page_zero_rejected() {
        let v = json!({"kind": "page", "page": 0});
        assert!(validate_citation(&v).is_err());
    }

    #[test]
    fn cells_citation_ok() {
        let v = json!({"kind": "cells", "sheet": "Sheet1", "cell_start": "A1", "cell_end": "B5"});
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.kind, "cells");
        assert_eq!(r.range.get("sheet"), Some(&json!("Sheet1")));
    }

    #[test]
    fn cells_numeric_axis_citation_ok() {
        let v = json!({
            "kind": "cells",
            "sheet": "Sheet1",
            "row_start": 2,
            "row_end": 10,
            "col_start": 1,
            "col_end": 4
        });
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.range, v);
    }

    #[test]
    fn malformed_ranges_are_rejected() {
        for value in [
            json!({"kind": "lines", "start": 0}),
            json!({"kind": "lines", "start": true}),
            json!({"kind": "page", "page": 5, "page_end": 4}),
            json!({"kind": "cells", "sheet": "Sheet1"}),
            json!({"kind": "row", "sheet": "Sheet1"}),
            json!({"kind": "column", "sheet": "Sheet1", "col_start": 0}),
            json!({"kind": "sheet", "sheet": "Sheet1", "row_start": 2, "row_end": 1}),
        ] {
            assert!(validate_citation(&value).is_err(), "accepted {value}");
        }
    }

    #[test]
    fn row_citation_ok() {
        let v = json!({"kind": "row", "sheet": "Sheet1", "row_start": 1, "row_end": 10});
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.kind, "row");
    }

    #[test]
    fn row_citation_end_lt_start_rejected() {
        let v = json!({"kind": "row", "sheet": "S1", "row_start": 10, "row_end": 5});
        assert!(validate_citation(&v).is_err());
    }

    #[test]
    fn column_citation_ok() {
        let v = json!({"kind": "column", "sheet": "Sheet1", "col_start": 1, "col_end": 5});
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.kind, "column");
    }

    #[test]
    fn sheet_citation_ok() {
        let v = json!({"kind": "sheet", "sheet": "OnlySheet"});
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.kind, "sheet");
    }

    #[test]
    fn message_id_citation_ok() {
        let v = json!({"kind": "message_id", "message_id": "msg_123"});
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.kind, "message_id");
    }

    #[test]
    fn custom_citation_descriptor_required() {
        let v = json!({"kind": "custom", "descriptor": {"foo": "bar"}});
        let r = validate_citation(&v).unwrap();
        assert_eq!(r.kind, "custom");
    }

    #[test]
    fn unknown_kind_rejected() {
        let v = json!({"kind": "made_up", "x": 1});
        assert!(validate_citation(&v).is_err());
    }

    #[test]
    fn non_dict_rejected() {
        let v = json!("just a string");
        assert!(validate_citation(&v).is_err());
    }

    #[test]
    fn missing_kind_rejected() {
        let v = json!({"start": 1, "end": 2});
        assert!(validate_citation(&v).is_err());
    }

    #[test]
    fn validate_all_short_circuits_on_first_error() {
        let arr = vec![
            json!({"kind": "lines", "start": 1, "end": 2}),
            json!({"kind": "bogus"}),
            json!({"kind": "lines", "start": 3, "end": 4}),
        ];
        assert!(validate_all(&arr).is_err());
    }

    #[test]
    fn typed_verdict_validates_current_lines_on_cjk_space_path() {
        let dir = tempfile::tempdir().unwrap();
        let workspace = dir.path().join("材料 空间");
        fs::create_dir(&workspace).unwrap();
        let relative = "研究 数据.txt";
        let bytes = b"alpha\nbeta\n";
        fs::write(workspace.join(relative), bytes).unwrap();
        let source = json!({
            "id": "source-1",
            "origin": "file",
            "location": relative,
            "fingerprint": format!("{:x}", Sha256::digest(bytes)),
            "status": "current"
        });

        let result = validate_source_citation(
            Some(&source),
            &json!({"kind": "lines", "start": 1, "end": 2}),
            Some(&workspace),
        );

        assert_eq!(result.validity, CitationValidity::Valid);
        assert!(result.valid);
        assert!(result.source_exists);
        assert!(result.source_unchanged);
        assert!(result.structure_valid);
        assert!(result.revision_matches);
        assert_eq!(result.range_valid, Some(true));
        assert_eq!(result.current_line_count, Some(2));
    }

    #[test]
    fn typed_verdict_distinguishes_changed_missing_and_malformed() {
        let dir = tempfile::tempdir().unwrap();
        let relative = "doc.txt";
        fs::write(dir.path().join(relative), b"new bytes\n").unwrap();
        let source = json!({
            "id": "source-1",
            "origin": "file",
            "location": relative,
            "fingerprint": format!("{:x}", Sha256::digest(b"old bytes\n")),
            "status": "current"
        });

        let changed = validate_source_citation(
            Some(&source),
            &json!({"kind": "lines", "start": 1}),
            Some(dir.path()),
        );
        assert_eq!(changed.validity, CitationValidity::SourceChanged);
        assert!(changed.source_exists);
        assert!(!changed.revision_matches);

        fs::remove_file(dir.path().join(relative)).unwrap();
        let missing = validate_source_citation(
            Some(&source),
            &json!({"kind": "lines", "start": 1}),
            Some(dir.path()),
        );
        assert_eq!(missing.validity, CitationValidity::SourceMissing);
        assert!(!missing.source_exists);

        let malformed = validate_source_citation(
            Some(&source),
            &json!({"kind": "lines", "start": 0}),
            Some(dir.path()),
        );
        assert_eq!(malformed.validity, CitationValidity::Malformed);
        assert!(!malformed.structure_valid);
    }

    #[test]
    fn typed_verdict_marks_unknown_kind_separately() {
        let source = json!({
            "id": "source-1",
            "origin": "file",
            "location": "doc.txt",
            "fingerprint": "abc",
            "status": "current"
        });
        let result = validate_source_citation(
            Some(&source),
            &json!({"kind": "future_locator", "value": 1}),
            None,
        );
        assert_eq!(result.validity, CitationValidity::UnsupportedKind);
        assert_eq!(result.reason, "unsupported_kind");
    }

    #[test]
    fn typed_verdict_rejects_line_one_for_empty_file() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("empty.txt"), b"").unwrap();
        let source = json!({
            "id": "source-empty",
            "origin": "file",
            "location": "empty.txt",
            "fingerprint": format!("{:x}", Sha256::digest(b"")),
            "status": "current"
        });

        let result = validate_source_citation(
            Some(&source),
            &json!({"kind": "lines", "start": 1}),
            Some(dir.path()),
        );

        assert_eq!(result.validity, CitationValidity::RangeInvalid);
        assert_eq!(result.current_line_count, Some(0));
    }
}
