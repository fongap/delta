// Embed the Delta icon (icon.ico next to this crate) into the launcher .exe so Windows
// Explorer / taskbar shows the real Delta icon instead of the generic app placeholder.
// Also stamp the user-facing Delta product identity into the short-lived root entrypoint.
// Process-tree ownership and sidecar cleanup remain the Tauri GUI's responsibility; PE display
// metadata is not used as a Task Manager grouping mechanism.
// Build-only: winres invokes the Windows resource compiler (rc.exe) to stamp the icon
// and version info into the PE resources. The launcher stays runtime-dependency-free —
// nothing here links into the shipped binary.
fn main() {
    if cfg!(windows) {
        let mut res = winres::WindowsResource::new();
        res.set_icon("icon.ico");
        res.set("FileDescription", "Delta");
        res.set("ProductName", "Delta");
        res.set("FileVersion", "0.2.0");
        res.set("ProductVersion", "0.2.0");
        res.compile()
            .expect("winres: failed to embed Delta icon (is rc.exe / Windows SDK available?)");
    }
}
