//! Delta Windows Portable root launcher.
//!
//! Ships as `<ROOT>\Delta.exe`. On launch it resolves the portable ROOT from its own
//! location (`current_exe().parent()` — never CWD, never a hardcoded drive), validates the
//! real app, initializes `Data` on first run, sets the portable-mode environment, and
//! spawns `<ROOT>\App\Delta\Delta.exe` with the caller's arguments passed through intact,
//! then exits. The GUI owns single-instance handling and the sidecar lifecycle.
//!
//! Everything in the whole portable is derived from ROOT at runtime, so the folder can be
//! copied / moved / renamed / carried to another drive or machine and keeps working — no
//! absolute path is persisted anywhere by this launcher.

#![cfg_attr(windows, windows_subsystem = "windows")]

use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

/// CREATE_NO_WINDOW — the app is a GUI binary; never let a console flash.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn main() {
    if let Err(msg) = run() {
        show_error("Delta Portable 启动失败", &msg);
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let root = portable_root()?;
    let app_exe = root.join("App").join("Delta").join("Delta.exe");
    if !app_exe.is_file() {
        return Err(format!(
            "未找到主程序：\n{}\n\n便携版目录结构已损坏，请重新解压整个文件夹。",
            app_exe.display()
        ));
    }

    let data_dir = root.join("Data");
    init_data(&root, &data_dir)?;

    // All child env inherits ours; the portable overrides are layered on top.
    let mut cmd = Command::new(&app_exe);
    cmd.env("DELTA_PORTABLE", "1")
        .env("DELTA_PORTABLE_ROOT", &root)
        .env("DELTA_DATA_DIR", &data_dir)
        // The single source of truth override for BOTH the Rust shell (lib.rs) and the
        // Python server (coworker/secrets.state_dir()) → every store, pref, log, DB and
        // secret lands under Data, never %APPDATA%.
        .env("COWORKER_STATE_DIR", &data_dir)
        // Best-effort WebView2 profile redirect. wry may pin its own data folder (in which
        // case this is ignored and WebView2 stays in its default location — reported as a
        // known limitation); when honored, the browser profile moves with the portable.
        .env(
            "WEBVIEW2_USER_DATA_FOLDER",
            data_dir.join("runtime").join("webview2"),
        )
        // Anchor the child's CWD at ROOT so no logic depends on where the user launched from.
        .current_dir(&root)
        // Pass every argument through unchanged (Unicode-safe on Windows).
        .args(env::args_os().skip(1))
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    cmd.spawn()
        .map_err(|e| format!("无法启动主程序：\n{}\n\n{e}", app_exe.display()))?;
    // Bootstrap only: the child inherits the complete environment and current directory at
    // spawn time. Staying resident adds no cleanup or signalling guarantee — the Tauri GUI
    // owns its sidecar and single-instance lifecycle — so return as soon as launch succeeds.
    Ok(())
}

/// ROOT is always the launcher's own parent directory — location-independent by construction.
fn portable_root() -> Result<PathBuf, String> {
    let exe = env::current_exe().map_err(|e| format!("无法定位启动器自身：{e}"))?;
    exe.parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| "无法解析便携版根目录（启动器没有父目录）".to_string())
}

/// Initialize `Data`: writability probe, first-run `DefaultData` seed, and the standard
/// subdirectories. Never auto-elevates — a read-only ROOT is a hard, explicit error.
fn init_data(root: &Path, data_dir: &Path) -> Result<(), String> {
    // First run is the Data dir not existing yet. DefaultData seeds only on that first run —
    // existing user data is never touched, overwritten, or merged back into.
    let first_run = !data_dir.exists();

    std::fs::create_dir_all(data_dir)
        .map_err(|e| format!("无法创建数据目录：\n{}\n\n{e}", data_dir.display()))?;

    // Writable probe: if we can't write a marker file, the ROOT is not portable-safe
    // (e.g. Program Files or a read-only share). Show it clearly instead of failing later
    // deep inside the app, and never attempt elevation.
    let probe = data_dir.join(".write-probe");
    if let Err(e) = std::fs::write(&probe, b"ok") {
        return Err(format!(
            "便携版目录不可写：\n{}\n\n{e}\n\n请将整个文件夹移动到可写位置（不要放在 Program Files 等系统目录，本程序不会请求管理员权限）。",
            data_dir.display()
        ));
    }
    let _ = std::fs::remove_file(&probe);

    if first_run {
        let default = root.join("App").join("DefaultData");
        if default.is_dir() {
            copy_tree_missing(&default, data_dir)?;
        }
    }

    for sub in ["workspace", "logs", "scratch", "cache"] {
        std::fs::create_dir_all(data_dir.join(sub))
            .map_err(|e| format!("无法创建数据子目录 {sub}：{e}"))?;
    }
    Ok(())
}

/// Recursively copy `src` into `dst`, only creating directories and copying files that do
/// not yet exist at the destination. Missing-file-only semantics: idempotent and non-destructive.
fn copy_tree_missing(src: &Path, dst: &Path) -> Result<(), String> {
    for entry in std::fs::read_dir(src).map_err(|e| format!("读取 DefaultData 失败：{e}"))? {
        let entry = entry.map_err(|e| format!("读取 DefaultData 条目失败：{e}"))?;
        let s = entry.path();
        let d = dst.join(entry.file_name());
        let meta = entry
            .file_type()
            .map_err(|e| format!("读取 DefaultData 条目类型失败：{e}"))?;
        if meta.is_dir() {
            std::fs::create_dir_all(&d)
                .map_err(|e| format!("创建目录失败：{} {e}", d.display()))?;
            copy_tree_missing(&s, &d)?;
        } else if meta.is_file() && !d.exists() {
            std::fs::copy(&s, &d).map_err(|e| format!("复制 {} 失败：{e}", d.display()))?;
        }
    }
    Ok(())
}

// Show a clear error box. The launcher is a GUI-subsystem binary, so stderr is invisible —
// a MessageBox is the only reliable channel. FFI to user32 directly (no dependency).
#[cfg(windows)]
#[link(name = "user32")]
extern "system" {
    fn MessageBoxW(
        hwnd: *const std::ffi::c_void,
        text: *const u16,
        caption: *const u16,
        kind: u32,
    ) -> i32;
}

#[cfg(windows)]
fn show_error(caption: &str, message: &str) {
    // MB_OK | MB_ICONERROR
    const MB_OK_ICONERROR: u32 = 0x0000_0010;
    let mut text: Vec<u16> = message.encode_utf16().collect();
    let mut cap: Vec<u16> = caption.encode_utf16().collect();
    text.push(0);
    cap.push(0);
    unsafe {
        MessageBoxW(
            std::ptr::null(),
            text.as_ptr(),
            cap.as_ptr(),
            MB_OK_ICONERROR,
        );
    }
}

#[cfg(not(windows))]
fn show_error(caption: &str, message: &str) {
    eprintln!("{caption}: {message}");
}
