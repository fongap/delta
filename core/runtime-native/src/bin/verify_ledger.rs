use std::env;
use std::process::ExitCode;

use delta_runtime_native::LedgerReader;

fn main() -> ExitCode {
    let mut db: Option<String> = None;
    let mut run_id: Option<String> = None;

    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--db" => db = args.next(),
            "--run-id" => run_id = args.next(),
            _ => {}
        }
    }

    let db = match db {
        Some(p) => p,
        None => {
            eprintln!("usage: verify_ledger --db <path> --run-id <id>");
            return ExitCode::from(2);
        }
    };
    let run_id = run_id.unwrap_or_default();

    let reader = match LedgerReader::open(&db) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("error: cannot open {db}: {e}");
            return ExitCode::from(3);
        }
    };

    match reader.verify(&run_id) {
        Ok(true) => {
            println!("OK");
            ExitCode::from(0)
        }
        Ok(false) => {
            println!("FAIL");
            ExitCode::from(1)
        }
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::from(3)
        }
    }
}
