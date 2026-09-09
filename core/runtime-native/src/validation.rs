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
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// One check in the validation verdict. Mirrors Python `ValidationCheck.to_dict()`.
#[derive(Debug, Clone, Serialize)]
pub struct ValidationCheck {
    pub name: String,
    pub ok: bool,
    pub detail: String,
}

/// Aggregated verdict. Mirrors Python `ValidationResult.to_dict()`.
#[derive(Debug, Clone, Serialize)]
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
            let text = match std::fs::read(ws.join(path)) {
                Ok(bytes) => String::from_utf8_lossy(&bytes).into_owned(),
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
                        name: format!("substring:{path}:'{}'", needle.replace('\'', "\\'")),
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

/// Read the first CSV record using RFC-compatible quoting. This mirrors
/// Python's ``csv.reader`` path, including quoted headers with commas.
fn read_csv_first_row(path: &Path) -> std::io::Result<Option<Vec<String>>> {
    let bytes = std::fs::read(path)?;
    let mut reader = csv::ReaderBuilder::new()
        .has_headers(false)
        .from_reader(bytes.as_slice());
    match reader.records().next() {
        Some(record) => record
            .map(|row| row.iter().map(String::from).collect::<Vec<_>>())
            .map(Some)
            .map_err(std::io::Error::other),
        None => Ok(None),
    }
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

/// Write authority for Validation trusted facts (R2, ADR-028).
///
/// Appends `validation.registered` and `validation.evaluated` events to the
/// run-event ledger via `LedgerWriter`. All trusted persistence is
/// owned by Rust; Python is a facade that only performs extraction
/// and candidate construction.
use crate::ledger::{LedgerEvent, LedgerReader, LedgerWriter};
use crate::ShadowReadError;

/// Event type for validation criteria registration / evaluation.
const EV_VALIDATION_REGISTERED: &str = "validation.registered";
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationRegisterInput {
    pub run_id: String,
    pub criteria: Value,
    pub evaluated_at: String,
    pub result: Value,
}

/// One reconstructed Validation record from the ledger event stream.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationRecord {
    pub id: String,
    pub run_id: String,
    pub criteria: Value,
    pub evaluated_at: String,
    pub result: Value,
}

/// Read-only handle that replays validation events from the ledger
/// to reconstruct the current Validation register.
pub struct ValidationReader {
    pub reader: LedgerReader,
}

impl ValidationReader {
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

    /// Reconstruct all Validation records for the workspace by replaying events.
    pub fn list_validations(&self) -> Result<Vec<ValidationRecord>, ShadowReadError> {
        let events = self.reader.all_events()?;
        self.replay_validations(&events)
    }

    /// Get a single validation by ID.
    pub fn get_validation(
        &self,
        validation_id: &str,
    ) -> Result<Option<ValidationRecord>, ShadowReadError> {
        let events = self.reader.all_events()?;
        let validations = self.replay_validations(&events)?;
        Ok(validations.into_iter().find(|v| v.id == validation_id))
    }

    /// Find the latest validation for a given run_id.
    pub fn latest_validation(
        &self,
        run_id: &str,
    ) -> Result<Option<ValidationRecord>, ShadowReadError> {
        let events = self.reader.all_events()?;
        let validations = self.replay_validations(&events)?;
        let candidates: Vec<_> = validations
            .into_iter()
            .filter(|v| v.run_id == run_id)
            .collect();
        if candidates.is_empty() {
            return Ok(None);
        }
        // Latest by evaluated_at (string ISO8601 is lexicographically sortable).
        let latest = candidates
            .into_iter()
            .max_by_key(|v| v.evaluated_at.clone())
            .unwrap();
        Ok(Some(latest))
    }

    fn replay_validations(
        &self,
        events: &[LedgerEvent],
    ) -> Result<Vec<ValidationRecord>, ShadowReadError> {
        use std::collections::HashMap;
        let mut validations: HashMap<String, ValidationRecord> = HashMap::new();

        for ev in events {
            if ev.r#type == EV_VALIDATION_REGISTERED {
                let payload = &ev.payload;
                let id = payload
                    .get("id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        ShadowReadError::Parse("validation.registered missing id".into())
                    })?
                    .to_string();

                let record = ValidationRecord {
                    id,
                    run_id: payload
                        .get("run_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    criteria: payload.get("criteria").cloned().unwrap_or(Value::Null),
                    evaluated_at: payload
                        .get("evaluated_at")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    result: payload.get("result").cloned().unwrap_or(Value::Null),
                };
                validations.insert(record.id.clone(), record);
            }
        }

        // Sort by evaluated_at ascending for stable list order.
        let mut out: Vec<_> = validations.into_values().collect();
        out.sort_by(|a, b| a.evaluated_at.cmp(&b.evaluated_at));
        Ok(out)
    }
}

/// Write authority for Validation facts.
pub struct ValidationWriter<'a> {
    ledger: &'a LedgerWriter,
}

impl<'a> ValidationWriter<'a> {
    pub fn new(ledger: &'a LedgerWriter) -> Self {
        Self { ledger }
    }

    /// Register (or re-register) a validation criteria and its result. Returns the resulting ValidationRecord.
    pub fn register_validation(
        &self,
        input: ValidationRegisterInput,
        ts: f64,
        workspace: &str,
    ) -> Result<ValidationRecord, ShadowReadError> {
        let validation_id = uuid::Uuid::new_v4().to_string();
        let record = ValidationRecord {
            id: validation_id,
            run_id: input.run_id,
            criteria: input.criteria,
            evaluated_at: input.evaluated_at,
            result: input.result,
        };
        self.append_validation_registered(&record, ts, workspace)?;
        Ok(record)
    }

    /// Evaluate validation criteria against artifacts and persist the result.
    /// Returns the evaluation result.
    pub fn evaluate_and_register(
        &self,
        run_id: &str,
        criteria: &Value,
        artifacts: &[Value],
        workspace: &str,
        valid_citation_count: Option<usize>,
        ts: f64,
    ) -> Result<(ValidationRecord, ValidationResult), ShadowReadError> {
        // Run the validation
        let result = run_validation(
            artifacts,
            criteria,
            Some(std::path::Path::new(workspace)),
            valid_citation_count,
        )
        .map_err(ShadowReadError::Parse)?;

        let evaluated_at = time::OffsetDateTime::now_utc()
            .format(&time::format_description::well_known::Rfc3339)
            .unwrap_or_else(|_| time::OffsetDateTime::now_utc().to_string());

        let input = ValidationRegisterInput {
            run_id: run_id.to_string(),
            criteria: criteria.clone(),
            evaluated_at: evaluated_at.clone(),
            result: serde_json::to_value(&result).unwrap(),
        };

        let record = self.register_validation(input, ts, workspace)?;
        Ok((record, result))
    }

    pub fn append_validation_registered(
        &self,
        record: &ValidationRecord,
        ts: f64,
        workspace: &str,
    ) -> Result<(), ShadowReadError> {
        let payload = serde_json::json!({
            "id": record.id,
            "run_id": record.run_id,
            "criteria": record.criteria,
            "evaluated_at": record.evaluated_at,
            "result": record.result,
        });
        self.ledger.append(
            record.run_id.as_str(),
            EV_VALIDATION_REGISTERED,
            "system",
            ts,
            &payload,
            workspace,
        )?;
        Ok(())
    }
}
