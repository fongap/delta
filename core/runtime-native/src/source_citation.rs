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
//! - ``"lines"``     : ``start``, ``end`` (int; 1-based, inclusive)
//! - ``"page"``      : ``page`` (int; >= 1)
//! - ``"cells"``     : ``sheet`` (str), ``cell_start`` / ``cell_end`` (str)
//! - ``"row"``       : ``sheet`` (str), ``row_start`` / ``row_end`` (int)
//! - ``"column"``    : ``sheet`` (str), ``col_start`` / ``col_end`` (int)
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

    let mut out = serde_json::Map::new();
    out.insert("kind".to_string(), Value::String(kind.clone()));
    match kind.as_str() {
        "lines" => {
            let start = get_int("start").ok_or("lines citation missing 'start'")?;
            let end = get_int("end").ok_or("lines citation missing 'end'")?;
            if end < start {
                return Err(format!("lines citation end ({end}) < start ({start})"));
            }
            out.insert("start".to_string(), Value::from(start));
            out.insert("end".to_string(), Value::from(end));
        }
        "page" => {
            let page = get_int("page").ok_or("page citation missing 'page'")?;
            if page < 1 {
                return Err(format!("page citation page must be >= 1, got {page}"));
            }
            out.insert("page".to_string(), Value::from(page));
            if let Some(pe) = get_int("page_end") {
                if pe < page {
                    return Err(format!("page citation page_end ({pe}) < page ({page})"));
                }
                out.insert("page_end".to_string(), Value::from(pe));
            }
        }
        "cells" => {
            let sheet = get_str("sheet").ok_or("cells citation missing 'sheet'")?;
            let cell_start = get_str("cell_start").ok_or("cells citation missing 'cell_start'")?;
            let cell_end = get_str("cell_end").ok_or("cells citation missing 'cell_end'")?;
            out.insert("sheet".to_string(), Value::String(sheet));
            out.insert("cell_start".to_string(), Value::String(cell_start));
            out.insert("cell_end".to_string(), Value::String(cell_end));
        }
        "row" => {
            let sheet = get_str("sheet").ok_or("row citation missing 'sheet'")?;
            let row_start = get_int("row_start").ok_or("row citation missing 'row_start'")?;
            let row_end = get_int("row_end").ok_or("row citation missing 'row_end'")?;
            if row_end < row_start {
                return Err(format!(
                    "row citation row_end ({row_end}) < row_start ({row_start})"
                ));
            }
            out.insert("sheet".to_string(), Value::String(sheet));
            out.insert("row_start".to_string(), Value::from(row_start));
            out.insert("row_end".to_string(), Value::from(row_end));
        }
        "column" => {
            let sheet = get_str("sheet").ok_or("column citation missing 'sheet'")?;
            let col_start = get_int("col_start").ok_or("column citation missing 'col_start'")?;
            let col_end = get_int("col_end").ok_or("column citation missing 'col_end'")?;
            if col_end < col_start {
                return Err(format!(
                    "column citation col_end ({col_end}) < col_start ({col_start})"
                ));
            }
            out.insert("sheet".to_string(), Value::String(sheet));
            out.insert("col_start".to_string(), Value::from(col_start));
            out.insert("col_end".to_string(), Value::from(col_end));
        }
        "sheet" => {
            let sheet = get_str("sheet").ok_or("sheet citation missing 'sheet'")?;
            out.insert("sheet".to_string(), Value::String(sheet));
        }
        "message_id" => {
            let message_id =
                get_str("message_id").ok_or("message_id citation missing 'message_id'")?;
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
        assert!(validate_citation(&v).is_err());
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
