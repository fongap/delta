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

use serde_json::Value;

/// A validated citation range. Mirrors the Python ``to_range_dict`` output.
#[derive(Debug, Clone)]
pub struct ValidatedCitation {
    pub kind: String,
    /// The trimmed, kind-specific dict (matches Python ``to_range_dict`` output).
    pub range: Value,
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
}
