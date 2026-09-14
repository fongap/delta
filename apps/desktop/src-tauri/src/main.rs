// Prevent a console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if std::env::args_os().any(|arg| arg == "--runtime-self-test") {
        if let Err(error) = delta_desktop_lib::portable_self_test() {
            eprintln!("Delta Runtime self-test failed: {error}");
            std::process::exit(1);
        }
        return;
    }
    delta_desktop_lib::run();
}
