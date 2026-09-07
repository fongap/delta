//! Read-only shadow validator (R2, ADR-019).
//!
//! The Python `core/validation.py` `run_validation` evaluates a
//! deterministic rule engine against the run's artifacts. This module
//! reimplements the same logic in Rust so the Python implementation
//! can be cross-checked against an independent implementation.
//!
//! Contract: the JSON shapes accepted here mirror the Python
//! `ValidationCriteria.to_dict()` and `Artifact.to_dict()`. The output
//! `ValidationResult` mirrors `ValidationResult.to_dict()`. Any byte
//! difference between the Python and Rust outputs is a regression in
//! the contract.
//!
//! Rules implemented (must match `core/validation.py:run_validation`):
//!
//! - `artifact_count`: min_artifacts <= count <= max_artifacts
//! - `all_artifacts_complete`: no artifact with incomplete=true
//! - `required_paths`: every path in criteria must be in artifacts
//! - `min_size` / `max_size`: per-path size gates
//! - `required_substrings`: each needle must appear in the file content
//! - `csv_required_headers`: first row of CSV must contain all headers
//! - `min_valid_citations`: external counter >= criteria.min_valid_citations
//!
//! See ``docs/architecture/adr/ADR-005-reliable-task-runtime.md``
//! (WS3: Validation) and ``docs/architecture/adr/ADR-019-r2-pre-plumbing.md``.

use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::path::Path;

use serde::Deserialize;
use serde_json::Value;

/// One check in the validation verdict. Mirrors Python `ValidationCheck.to_dict()`.
#[derive(Debug, Clone)]
pub struct ValidationCheck {
    pub name: String,
    pub ok: bool,
    pub detail: String,
}

/// Aggregated verdict. Mirrors Python `ValidationResult.to_dict()`.
#[derive(Debug, Clone)]
pub struct ValidationResult {
    pub ok: bool,
    pub checks: Vec<ValidationCheck>,
    pub evidence: Value,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
struct Criteria {
    #[serde(default = "default_min")]
    min_artifacts: usize,
    #[serde(default = "default_max")]
    max_artifacts: usize,
    #[serde(default)]
    required_paths: Vec<String>,
    #[serde(default)]
    required_substrings: HashMap<String, Vec<String>>,
    #[serde(default)]
    min_size: HashMap<String, i64>,
    #[serde(default)]
    max_size: HashMap<String, i64>,
    #[serde(default = "default_true")]
    require_complete: bool,
    #[serde(default)]
    csv_required_headers: HashMap<String, Vec<String>>,
    #[serde(default)]
    require_citations: bool,
    #[serde(default)]
    min_valid_citations: usize,
}

fn default_min() -> usize {
    1
}
fn default_max() -> usize {
    50
}
fn default_true() -> bool {
    true
}

/// Evaluate the criteria against the artifacts and the workspace.
/// `valid_citation_count` enables the P1-D Citation Completion Contract;
/// pass `None` to skip the citation check (matches Python default).
pub fn run_validation(
    artifacts: &[Value],
    criteria_json: &Value,
    workspace: Option<&Path>,
    valid_citation_count: Option<usize>,
) -> Result<ValidationResult, String> {
    let criteria: Criteria = serde_json::from_value(criteria_json.clone())
        .map_err(|e| format!("criteria parse: {e}"))?;

    let mut checks: Vec<ValidationCheck> = Vec::new();
    let mut evidence = serde_json::json!({"artifact_count": artifacts.len()});

    // Count gate
    let count_ok =
        criteria.min_artifacts <= artifacts.len() && artifacts.len() <= criteria.max_artifacts;
    checks.push(ValidationCheck {
        name: "artifact_count".to_string(),
        ok: count_ok,
        detail: format!(
            "{} artifacts (min={}, max={})",
            artifacts.len(),
            criteria.min_artifacts,
            criteria.max_artifacts
        ),
    });
    if !count_ok {
        return Ok(ValidationResult {
            ok: false,
            checks,
            evidence,
        });
    }

    // Completeness gate
    let incomplete: Vec<String> = artifacts
        .iter()
        .filter_map(|a| {
            a.get("incomplete")
                .and_then(Value::as_bool)
                .filter(|&b| b)
                .and_then(|_| a.get("path").and_then(Value::as_str).map(String::from))
        })
        .collect();
    if criteria.require_complete && !incomplete.is_empty() {
        checks.push(ValidationCheck {
            name: "all_artifacts_complete".to_string(),
            ok: false,
            detail: format!("incomplete writes: {incomplete:?}"),
        });
        return Ok(ValidationResult {
            ok: false,
            checks,
            evidence,
        });
    }
    checks.push(ValidationCheck {
        name: "all_artifacts_complete".to_string(),
        ok: true,
        detail: String::new(),
    });

    // Index by path
    let by_path: HashMap<&str, &Value> = artifacts
        .iter()
        .filter_map(|a| a.get("path").and_then(Value::as_str).map(|p| (p, a)))
        .collect();

    // Required paths
    let missing: Vec<&str> = criteria
        .required_paths
        .iter()
        .filter(|p| !by_path.contains_key(p.as_str()))
        .map(String::as_str)
        .collect();
    if !missing.is_empty() {
        checks.push(ValidationCheck {
            name: "required_paths".to_string(),
            ok: false,
            detail: format!("missing: {missing:?}"),
        });
        return Ok(ValidationResult {
            ok: false,
            checks,
            evidence,
        });
    }
    if !criteria.required_paths.is_empty() {
        checks.push(ValidationCheck {
            name: "required_paths".to_string(),
            ok: true,
            detail: String::new(),
        });
    }

    // Size gates
    for (path, lo) in &criteria.min_size {
        let size = by_path
            .get(path.as_str())
            .and_then(|a| a.get("size"))
            .and_then(Value::as_i64)
            .unwrap_or(0);
        if size < *lo {
            checks.push(ValidationCheck {
                name: format!("min_size:{path}"),
                ok: false,
                detail: format!("size={size} < {lo}"),
            });
            return Ok(ValidationResult {
                ok: false,
                checks,
                evidence,
            });
        }
    }
    for (path, hi) in &criteria.max_size {
        let size = by_path
            .get(path.as_str())
            .and_then(|a| a.get("size"))
            .and_then(Value::as_i64)
            .unwrap_or(0);
        if size > *hi {
            checks.push(ValidationCheck {
                name: format!("max_size:{path}"),
                ok: false,
                detail: format!("size={size} > {hi}"),
            });
            return Ok(ValidationResult {
                ok: false,
                checks,
                evidence,
            });
        }
    }
    if !criteria.min_size.is_empty() || !criteria.max_size.is_empty() {
        checks.push(ValidationCheck {
            name: "size_gates".to_string(),
            ok: true,
            detail: String::new(),
        });
    }

    // Substring + CSV checks need a workspace
    if let Some(ws) = workspace {
        for (path, needles) in &criteria.required_substrings {
            let a = match by_path.get(path.as_str()) {
                Some(a) => a,
                None => continue,
            };
            if a.get("incomplete")
                .and_then(Value::as_bool)
                .unwrap_or(false)
            {
                continue;
            }
            let text = match std::fs::read_to_string(ws.join(path)) {
                Ok(t) => t,
                Err(e) => {
                    checks.push(ValidationCheck {
                        name: format!("read:{path}"),
                        ok: false,
                        detail: format!("read failed: {e}"),
                    });
                    return Ok(ValidationResult {
                        ok: false,
                        checks,
                        evidence,
                    });
                }
            };
            for needle in needles {
                if !text.contains(needle.as_str()) {
                    checks.push(ValidationCheck {
                        name: format!("substring:{path}:{needle:?}"),
                        ok: false,
                        detail: "not found".to_string(),
                    });
                    return Ok(ValidationResult {
                        ok: false,
                        checks,
                        evidence,
                    });
                }
            }
            checks.push(ValidationCheck {
                name: format!("substrings:{path}"),
                ok: true,
                detail: String::new(),
            });
        }

        for (path, headers) in &criteria.csv_required_headers {
            let a = match by_path.get(path.as_str()) {
                Some(a) => a,
                None => continue,
            };
            if a.get("incomplete")
                .and_then(Value::as_bool)
                .unwrap_or(false)
            {
                continue;
            }
            let first = match read_csv_first_row(&ws.join(path)) {
                Ok(row) => row,
                Err(e) => {
                    checks.push(ValidationCheck {
                        name: format!("csv_read:{path}"),
                        ok: false,
                        detail: format!("read failed: {e}"),
                    });
                    return Ok(ValidationResult {
                        ok: false,
                        checks,
                        evidence,
                    });
                }
            };
            let first = match first {
                Some(r) => r,
                None => {
                    checks.push(ValidationCheck {
                        name: format!("csv_headers:{path}"),
                        ok: false,
                        detail: "empty CSV".to_string(),
                    });
                    return Ok(ValidationResult {
                        ok: false,
                        checks,
                        evidence,
                    });
                }
            };
            let missing_h: Vec<&String> = headers
                .iter()
                .filter(|h| !first.iter().any(|cell| cell == h.as_str()))
                .collect();
            if !missing_h.is_empty() {
                checks.push(ValidationCheck {
                    name: format!("csv_headers:{path}"),
                    ok: false,
                    detail: format!("missing headers: {missing_h:?}"),
                });
                return Ok(ValidationResult {
                    ok: false,
                    checks,
                    evidence,
                });
            }
            checks.push(ValidationCheck {
                name: format!("csv_headers:{path}"),
                ok: true,
                detail: String::new(),
            });
        }
    }

    // Citation completion contract
    if criteria.require_citations {
        let Some(count) = valid_citation_count else {
            evidence["valid_citation_count"] = Value::Null;
            checks.push(ValidationCheck {
                name: "min_valid_citations".to_string(),
                ok: false,
                detail: "valid citation count unavailable".to_string(),
            });
            return Ok(ValidationResult {
                ok: false,
                checks,
                evidence,
            });
        };
        if count < criteria.min_valid_citations {
            checks.push(ValidationCheck {
                name: "min_valid_citations".to_string(),
                ok: false,
                detail: format!(
                    "{count} valid citation(s) < min_valid_citations={}",
                    criteria.min_valid_citations
                ),
            });
            return Ok(ValidationResult {
                ok: false,
                checks,
                evidence,
            });
        }
        checks.push(ValidationCheck {
            name: "min_valid_citations".to_string(),
            ok: true,
            detail: format!("{count} valid citation(s)"),
        });
        evidence["valid_citation_count"] = serde_json::json!(count);
    }

    Ok(ValidationResult {
        ok: checks.iter().all(|c| c.ok),
        checks,
        evidence,
    })
}

/// Read the first non-empty line of a file and split by `,`. This is
/// the minimum needed for the CSV header check; quoted fields with
/// embedded commas are NOT supported (matches the limitations in
/// ``core/validation.py``'s use of ``csv.reader`` with default dialect
/// on simple CSVs).
fn read_csv_first_row(path: &Path) -> std::io::Result<Option<Vec<String>>> {
    let f = std::fs::File::open(path)?;
    let mut buf = BufReader::new(f);
    let mut first_line = String::new();
    buf.read_line(&mut first_line)?;
    if first_line.is_empty() {
        return Ok(None);
    }
    let parsed: Vec<String> = first_line
        .trim_end_matches(['\r', '\n'])
        .split(',')
        .map(String::from)
        .collect();
    Ok(Some(parsed))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn empty_artifacts_below_min_fails() {
        let criteria = json!({"min_artifacts": 1, "max_artifacts": 5});
        let r = run_validation(&[], &criteria, None, None).unwrap();
        assert!(!r.ok);
        assert_eq!(r.checks[0].name, "artifact_count");
    }

    #[test]
    fn empty_artifacts_min_zero_passes_count() {
        let criteria = json!({"min_artifacts": 0, "max_artifacts": 5, "require_complete": false});
        let r = run_validation(&[], &criteria, None, None).unwrap();
        assert!(r.ok);
    }

    #[test]
    fn artifacts_above_max_fails() {
        let criteria = json!({"min_artifacts": 1, "max_artifacts": 2});
        let artifacts = vec![
            json!({"path": "a.md", "size": 1, "incomplete": false}),
            json!({"path": "b.md", "size": 1, "incomplete": false}),
            json!({"path": "c.md", "size": 1, "incomplete": false}),
        ];
        let r = run_validation(&artifacts, &criteria, None, None).unwrap();
        assert!(!r.ok);
    }

    #[test]
    fn incomplete_artifact_fails_completeness() {
        let criteria = json!({"min_artifacts": 1, "max_artifacts": 5, "require_complete": true});
        let artifacts = vec![json!({"path": "a.md", "size": 1, "incomplete": true})];
        let r = run_validation(&artifacts, &criteria, None, None).unwrap();
        assert!(!r.ok);
        assert!(r
            .checks
            .iter()
            .any(|c| c.name == "all_artifacts_complete" && !c.ok));
    }

    #[test]
    fn require_complete_false_allows_incomplete() {
        let criteria = json!({"min_artifacts": 1, "max_artifacts": 5, "require_complete": false});
        let artifacts = vec![json!({"path": "a.md", "size": 1, "incomplete": true})];
        let r = run_validation(&artifacts, &criteria, None, None).unwrap();
        assert!(r.ok);
    }

    #[test]
    fn missing_required_path_fails() {
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "required_paths": ["missing.md"],
        });
        let artifacts = vec![json!({"path": "a.md", "size": 1, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, None, None).unwrap();
        assert!(!r.ok);
    }

    #[test]
    fn size_gate_min_fails() {
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "min_size": {"a.md": 100},
        });
        let artifacts = vec![json!({"path": "a.md", "size": 50, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, None, None).unwrap();
        assert!(!r.ok);
    }

    #[test]
    fn size_gate_max_fails() {
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "max_size": {"a.md": 10},
        });
        let artifacts = vec![json!({"path": "a.md", "size": 50, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, None, None).unwrap();
        assert!(!r.ok);
    }

    #[test]
    fn citation_floor_under_min_fails() {
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "require_citations": true, "min_valid_citations": 3,
        });
        let artifacts = vec![json!({"path": "a.md", "size": 1, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, None, Some(1)).unwrap();
        assert!(!r.ok);
    }

    #[test]
    fn citation_floor_at_min_passes() {
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "require_citations": true, "min_valid_citations": 2,
        });
        let artifacts = vec![json!({"path": "a.md", "size": 1, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, None, Some(2)).unwrap();
        assert!(r.ok);
    }

    #[test]
    fn citation_floor_fails_closed_when_count_is_none() {
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "require_citations": true, "min_valid_citations": 1,
        });
        let artifacts = vec![json!({"path": "a.md", "size": 1, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, None, None).unwrap();
        assert!(!r.ok);
        assert_eq!(
            r.checks.last().unwrap().detail,
            "valid citation count unavailable"
        );
        assert_eq!(r.evidence["valid_citation_count"], Value::Null);
    }

    #[test]
    fn substring_check_against_workspace() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let ws = dir.path();
        std::fs::write(ws.join("a.md"), "hello world\n").unwrap();
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "required_substrings": {"a.md": ["hello"]},
        });
        let artifacts = vec![json!({"path": "a.md", "size": 12, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, Some(ws), None).unwrap();
        assert!(r.ok);
    }

    #[test]
    fn substring_missing_fails() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let ws = dir.path();
        std::fs::write(ws.join("a.md"), "hello world\n").unwrap();
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "required_substrings": {"a.md": ["absent"]},
        });
        let artifacts = vec![json!({"path": "a.md", "size": 12, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, Some(ws), None).unwrap();
        assert!(!r.ok);
    }

    #[test]
    fn csv_header_check_passes() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let ws = dir.path();
        std::fs::write(ws.join("data.csv"), "name,age\nalice,30\n").unwrap();
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "csv_required_headers": {"data.csv": ["name", "age"]},
        });
        let artifacts = vec![json!({"path": "data.csv", "size": 20, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, Some(ws), None).unwrap();
        assert!(r.ok);
    }

    #[test]
    fn csv_header_missing_fails() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let ws = dir.path();
        std::fs::write(ws.join("data.csv"), "name,age\nalice,30\n").unwrap();
        let criteria = json!({
            "min_artifacts": 1, "max_artifacts": 5,
            "csv_required_headers": {"data.csv": ["name", "missing_col"]},
        });
        let artifacts = vec![json!({"path": "data.csv", "size": 20, "incomplete": false})];
        let r = run_validation(&artifacts, &criteria, Some(ws), None).unwrap();
        assert!(!r.ok);
    }
}
