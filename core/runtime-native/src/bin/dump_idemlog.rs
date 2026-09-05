use std::env;
use std::process::ExitCode;

use delta_runtime_native::IdempotencyReader;
use serde_json::json;

fn main() -> ExitCode {
    let mut db: Option<String> = None;
    let mut run_id: Option<String> = None;
    let mut filter = String::new();

    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--db" => db = args.next(),
            "--run-id" => run_id = args.next(),
            "--filter" => filter = args.next().unwrap_or_default(),
            _ => {}
        }
    }

    let db = match db {
        Some(p) => p,
        None => {
            eprintln!("usage: dump_idemlog --db <path> --run-id <id> [--filter all|uncommitted|uncertain|committed]");
            return ExitCode::from(2);
        }
    };
    let run_id = run_id.unwrap_or_default();

    let reader = match IdempotencyReader::open(&db) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("error: cannot open {db}: {e}");
            return ExitCode::from(3);
        }
    };

    let result = match filter.as_str() {
        "uncommitted" => reader.uncommitted_for_run(&run_id),
        "uncertain" => reader.uncertain_for_run(&run_id),
        "committed" => reader.committed_for_run(&run_id),
        "all" | "" => reader.for_run(&run_id),
        _ => {
            eprintln!("unknown filter: {filter}");
            return ExitCode::from(2);
        }
    };

    let entries = match result {
        Ok(e) => e,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::from(3);
        }
    };

    let output: Vec<_> = entries
        .iter()
        .map(|e| {
            json!({
                "tool_call_id": e.tool_call_id,
                "tool_name": e.tool_name,
                "state": e.state.as_str(),
                "operation_id": e.operation_id,
                "args_sha256": e.args_sha256,
            })
        })
        .collect();

    println!(
        "{}",
        serde_json::to_string(&output).unwrap_or_else(|_| "[]".to_string())
    );
    ExitCode::from(0)
}
