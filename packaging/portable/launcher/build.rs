// Embed the Delta icon (icon.ico next to this crate) into the launcher .exe so Windows
// Explorer / taskbar shows the real Delta icon instead of the generic app placeholder.
// Build-only: winres invokes the Windows resource compiler (rc.exe) to stamp the icon into
// the PE resources. The launcher stays runtime-dependency-free — nothing here links into
// the shipped binary.
fn main() {
    if cfg!(windows) {
        let mut res = winres::WindowsResource::new();
        res.set_icon("icon.ico");
        res.compile().expect("winres: failed to embed Delta icon (is rc.exe / Windows SDK available?)");
    }
}
