use std::env;
use std::process::ExitCode;

use delta_runtime_native::TaskStoreReader;
use serde_json::json;

fn main() -> ExitCode {
    let mut db: Option<String> = None;
    let mut task_id: Option<String> = None;
    let mut mode = String::from("tasks");

    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--db" => db = args.next(),
            "--task-id" => task_id = args.next(),
            "--mode" => mode = args.next().unwrap_or_else(|| "tasks".to_string()),
            _ => {}
        }
    }

    let db = match db {
        Some(p) => p,
        None => {
            eprintln!("usage: inspect_tasks --db <path> [--task-id <id>] [--mode tasks|runs|run]");
            return ExitCode::from(2);
        }
    };

    let reader = match TaskStoreReader::open(&db) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("error: cannot open {db}: {e}");
            return ExitCode::from(3);
        }
    };

    let result = match mode.as_str() {
        "tasks" => {
            let tasks = match reader.tasks() {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("error: {e}");
                    return ExitCode::from(3);
                }
            };
            let out: Vec<_> = tasks
                .iter()
                .map(|t| {
                    json!({
                        "id": t.id,
                        "enabled": t.enabled,
                        "next_run": t.next_run,
                    })
                })
                .collect();
            Ok(out)
        }
        "runs" => {
            let tid = match task_id {
                Some(t) => t,
                None => {
                    eprintln!("--task-id is required for mode=runs");
                    return ExitCode::from(2);
                }
            };
            reader.runs(&tid).map(|r| {
                r.iter()
                    .map(|r| {
                        json!({
                            "run_id": r.run_id,
                            "task_id": r.task_id,
                            "started_at": r.started_at,
                            "workspace": r.workspace,
                        })
                    })
                    .collect::<Vec<_>>()
            })
        }
        "run" => {
            let rid = match task_id {
                Some(t) => t,
                None => {
                    eprintln!("--task-id (as run_id) is required for mode=run");
                    return ExitCode::from(2);
                }
            };
            reader.find_run(&rid).map(|opt| {
                opt.map(|r| {
                    json!({
                        "run_id": r.run_id,
                        "task_id": r.task_id,
                        "started_at": r.started_at,
                        "workspace": r.workspace,
                    })
                })
                .into_iter()
                .collect::<Vec<_>>()
            })
        }
        _ => {
            eprintln!("unknown mode: {mode}");
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

    println!(
        "{}",
        serde_json::to_string(&entries).unwrap_or_else(|_| "[]".to_string())
    );
    ExitCode::from(0)
}
