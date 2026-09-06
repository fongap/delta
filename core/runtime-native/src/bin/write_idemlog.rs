use std::env;
use std::process::ExitCode;

use delta_runtime_native::IdempotencyWriter;
use serde_json::Value;

fn main() -> ExitCode {
    let mut db: Option<String> = None;
    let mut action = String::new();
    let mut run_id: Option<String> = None;
    let mut tool_call_id: Option<String> = None;
    let mut tool_name: Option<String> = None;
    let mut args_str: Option<String> = None;
    let mut result_str: Option<String> = None;
    let mut error_str: Option<String> = None;

    let mut args_iter = env::args().skip(1);
    while let Some(arg) = args_iter.next() {
        match arg.as_str() {
            "--db" => db = args_iter.next(),
            "--action" => action = args_iter.next().unwrap_or_default(),
            "--run-id" => run_id = args_iter.next(),
            "--tool-call-id" => tool_call_id = args_iter.next(),
            "--tool-name" => tool_name = args_iter.next(),
            "--args" => args_str = args_iter.next(),
            "--result" => result_str = args_iter.next(),
            "--error" => error_str = args_iter.next(),
            _ => {}
        }
    }

    let db = match db {
        Some(p) => p,
        None => {
            eprintln!("usage: write_idemlog --db <path> --action <record_planned|mark_executing|commit|mark_failed|mark_uncertain> --run-id <id> --tool-call-id <id> [...]");
            return ExitCode::from(2);
        }
    };
    let run_id = run_id.unwrap_or_default();
    let tool_call_id = tool_call_id.unwrap_or_default();

    let writer = match IdempotencyWriter::open(&db) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("error: cannot open {db}: {e}");
            return ExitCode::from(3);
        }
    };

    let args_val: Value = args_str
        .as_deref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or(Value::Null);
    let result_val: Value = result_str
        .as_deref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or(Value::Null);

    let result: Result<(), String> = match action.as_str() {
        "record_planned" => {
            let tn = tool_name.unwrap_or_default();
            writer
                .record_planned(&run_id, &tool_call_id, &tn, &args_val)
                .map(|_| ())
                .map_err(|e| e.to_string())
        }
        "mark_executing" => writer
            .mark_executing(&run_id, &tool_call_id)
            .map_err(|e| e.to_string()),
        "commit" => {
            let tn = tool_name.unwrap_or_default();
            writer
                .commit(&run_id, &tool_call_id, &tn, &args_val, &result_val)
                .map_err(|e| e.to_string())
        }
        "mark_failed" => {
            let err = error_str.unwrap_or_default();
            writer
                .mark_failed(&run_id, &tool_call_id, &err)
                .map_err(|e| e.to_string())
        }
        "mark_uncertain" => writer
            .mark_uncertain(&run_id, &tool_call_id)
            .map_err(|e| e.to_string()),
        _ => {
            eprintln!("unknown action: {action}");
            return ExitCode::from(2);
        }
    };

    match result {
        Ok(_) => {
            println!("OK");
            ExitCode::from(0)
        }
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::from(3)
        }
    }
}
