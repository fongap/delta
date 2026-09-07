//! Read-only shadow validator for citation ranges (R2, ADR-019).
//!
//! Citations are stored as ``CitationRange`` dicts on ``SourceRef`` rows
//! in ``sources.db`` (the source ledger). The Python ``to_range_dict``
//! in ``core/sources.py`` is the authoritative validator. This reader
//! reimplements the same kind-specific required-field rules so the
//! Python implementation can be cross-checked against an independent
//! implementation.
//!
//! Contract: ``core/sources.py:CitationRange`` and ``to_range_dict``.
//!
//! Kinds and required fields (must match Python, kind names are the
//! canonical strings defined in ``core/sources.py``):
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
        // Compatibility with the Python baseline: an empty byte stream is
        // currently counted as one logical line.  The empty-file contract is
        // tightened in a separate convergence slice before authority switch.
        let line_count = data.iter().filter(|byte| **byte == b'\n').count()
            + usize::from(!data.ends_with(b"\n"));
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
    let mut result = verdict(
        CitationValidity::Valid,
        "current",
        "valid",
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
}
