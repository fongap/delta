use std::env;
use std::process::ExitCode;

use delta_runtime_native::LedgerWriter;
use serde_json::Value;

fn main() -> ExitCode {
    let mut db: Option<String> = None;
    let mut run_id: Option<String> = None;
    let mut event_type: Option<String> = None;
    let mut actor: Option<String> = None;
    let mut ts_str: Option<String> = None;
    let mut payload_str: Option<String> = None;
    let mut workspace: Option<String> = None;

    let mut args_iter = env::args().skip(1);
    while let Some(arg) = args_iter.next() {
        match arg.as_str() {
            "--db" => db = args_iter.next(),
            "--run-id" => run_id = args_iter.next(),
            "--type" => event_type = args_iter.next(),
            "--actor" => actor = args_iter.next(),
            "--ts" => ts_str = args_iter.next(),
            "--payload" => payload_str = args_iter.next(),
            "--workspace" => workspace = args_iter.next(),
            _ => {}
        }
    }

    let db = match db {
        Some(p) => p,
        None => {
            eprintln!(
                "usage: write_ledger --db <path> --run-id <id> --type <type> \
                 --actor <actor> --ts <float> [--payload <json>] [--workspace <w>]"
            );
            return ExitCode::from(2);
        }
    };
    let run_id = match run_id {
        Some(r) => r,
        None => {
            eprintln!("error: --run-id required");
            return ExitCode::from(2);
        }
    };
    let event_type = match event_type {
        Some(t) => t,
        None => {
            eprintln!("error: --type required");
            return ExitCode::from(2);
        }
    };
    let actor = actor.unwrap_or_else(|| "system".to_string());
    let ts: f64 = match ts_str.as_deref() {
        Some(s) => match s.parse() {
            Ok(v) => v,
            Err(_) => {
                eprintln!("error: --ts not a float: {s}");
                return ExitCode::from(2);
            }
        },
        None => {
            eprintln!("error: --ts required");
            return ExitCode::from(2);
        }
    };
    let payload: Value = payload_str
        .as_deref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or(Value::Null);
    let workspace = workspace.unwrap_or_default();

    let writer = match LedgerWriter::open(&db) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("error: cannot open {db}: {e}");
            return ExitCode::from(3);
        }
    };

    match writer.append(&run_id, &event_type, &actor, ts, &payload, &workspace) {
        Ok(row) => {
            println!("{}", serde_json::to_string(&row).unwrap_or_default());
            ExitCode::from(0)
        }
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::from(3)
        }
    }
}
