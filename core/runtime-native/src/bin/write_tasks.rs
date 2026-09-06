use std::env;
use std::process::ExitCode;

use delta_runtime_native::TaskStoreWriter;

fn main() -> ExitCode {
    let mut db: Option<String> = None;
    let mut action = String::new();
    let mut task_id: Option<String> = None;
    let mut run_id: Option<String> = None;
    let mut enabled: bool = true;
    let mut next_run: Option<f64> = None;
    let mut started_at: Option<f64> = None;
    let mut data: Option<String> = None;
    let mut workspace: Option<String> = None;

    let mut args_iter = env::args().skip(1);
    while let Some(arg) = args_iter.next() {
        match arg.as_str() {
            "--db" => db = args_iter.next(),
            "--action" => action = args_iter.next().unwrap_or_default(),
            "--task-id" => task_id = args_iter.next(),
            "--run-id" => run_id = args_iter.next(),
            "--enabled" => {
                let v = args_iter.next().unwrap_or_default();
                enabled = v == "1" || v.eq_ignore_ascii_case("true");
            }
            "--next-run" => next_run = args_iter.next().and_then(|s| s.parse().ok()),
            "--started-at" => started_at = args_iter.next().and_then(|s| s.parse().ok()),
            "--data" => data = args_iter.next(),
            "--workspace" => workspace = args_iter.next(),
            _ => {}
        }
    }

    let db = match db {
        Some(p) => p,
        None => {
            eprintln!(
                "usage: write_tasks --db <path> --action <save_task|delete_task|add_run> [...]"
            );
            return ExitCode::from(2);
        }
    };

    let writer = match TaskStoreWriter::open(&db) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("error: cannot open {db}: {e}");
            return ExitCode::from(3);
        }
    };

    let result: Result<(), String> = match action.as_str() {
        "save_task" => {
            let id = match &task_id {
                Some(t) => t.clone(),
                None => {
                    return ExitCode::from(2);
                }
            };
            let d = data.unwrap_or_default();
            writer
                .save_task(&id, enabled, next_run, &d)
                .map_err(|e| e.to_string())
        }
        "delete_task" => {
            let id = match &task_id {
                Some(t) => t.clone(),
                None => {
                    return ExitCode::from(2);
                }
            };
            writer
                .delete_task(&id)
                .map(|_| ())
                .map_err(|e| e.to_string())
        }
        "add_run" => {
            let rid = match &run_id {
                Some(r) => r.clone(),
                None => {
                    return ExitCode::from(2);
                }
            };
            let tid = task_id.unwrap_or_default();
            let sa = started_at.unwrap_or(0.0);
            let d = data.unwrap_or_default();
            let ws = workspace.unwrap_or_default();
            writer
                .add_run(&rid, &tid, sa, &d, &ws)
                .map_err(|e| e.to_string())
        }
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
