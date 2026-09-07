//! inspect_citation — validate a JSON array of citation ranges.
//!
//! Reads a JSON file containing a list of citation dicts (the
//! canonical shape used by ``core/sources.py:CitationRange`` /
//! ``to_range_dict``) and validates each entry against the same
//! kind-specific rules as the Python implementation. The first
//! invalid entry aborts with a non-zero exit code (matching Python's
//! short-circuit behavior in ``normalize_cited_ranges``).
//!
//! Usage:
//!   inspect_citation <citations.json>
//!   echo '[{"kind":"text","start":1,"end":2}]' | inspect_citation -
//!
//! Exit codes:
//!   0 — success (all entries valid)
//!   1 — validation failure (first error is printed to stderr)
//!   2 — usage / read error

use std::env;
use std::io::{self, Read};
use std::process::ExitCode;

use delta_runtime_native::validate_all;
use serde_json::json;

fn main() -> ExitCode {
    let path = match env::args().nth(1) {
        Some(p) => p,
        None => {
            eprintln!("usage: inspect_citation <citations.json> | -");
            return ExitCode::from(2);
        }
    };

    let text = if path == "-" {
        let mut s = String::new();
        if let Err(e) = io::stdin().read_to_string(&mut s) {
            eprintln!("error: stdin read: {e}");
            return ExitCode::from(2);
        }
        s
    } else {
        match std::fs::read_to_string(&path) {
            Ok(t) => t,
            Err(e) => {
                eprintln!("error: read {path}: {e}");
                return ExitCode::from(2);
            }
        }
    };

    let parsed: serde_json::Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("error: json parse: {e}");
            return ExitCode::from(1);
        }
    };
    let arr = match parsed.as_array() {
        Some(a) => a,
        None => {
            eprintln!("error: top-level must be a JSON array");
            return ExitCode::from(1);
        }
    };

    match validate_all(arr) {
        Ok(validated) => {
            let report = json!({
                "count": validated.len(),
                "citations": validated.iter().map(|v| &v.range).collect::<Vec<_>>(),
            });
            println!("{}", serde_json::to_string_pretty(&report).unwrap());
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::from(1)
        }
    }
}
