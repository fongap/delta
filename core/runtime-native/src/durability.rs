#[cfg(unix)]
use std::fs::File;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use crate::ShadowReadError;

pub fn atomic_write(path: impl AsRef<Path>, bytes: &[u8]) -> Result<(), ShadowReadError> {
    atomic_write_with_privacy(path.as_ref(), bytes, false)
}

pub fn atomic_write_private(path: impl AsRef<Path>, bytes: &[u8]) -> Result<(), ShadowReadError> {
    atomic_write_with_privacy(path.as_ref(), bytes, true)
}

fn atomic_write_with_privacy(
    path: &Path,
    bytes: &[u8],
    private: bool,
) -> Result<(), ShadowReadError> {
    let parent = path.parent().ok_or_else(|| {
        ShadowReadError::Parse(format!("authority path has no parent: {}", path.display()))
    })?;
    fs::create_dir_all(parent)?;

    let temp_path = sibling_temp_path(path)?;
    let result = write_and_replace(path, &temp_path, bytes, private);
    if result.is_err() {
        let _ = fs::remove_file(&temp_path);
    }
    result
}

fn sibling_temp_path(path: &Path) -> Result<PathBuf, ShadowReadError> {
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| {
            ShadowReadError::Parse(format!(
                "authority path has no UTF-8 file name: {}",
                path.display()
            ))
        })?;
    Ok(path.with_file_name(format!(".{file_name}.tmp-{}", uuid::Uuid::new_v4())))
}

fn write_and_replace(
    path: &Path,
    temp_path: &Path,
    bytes: &[u8],
    private: bool,
) -> Result<(), ShadowReadError> {
    let mut file = open_temp_file(temp_path, private)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);

    replace_file(temp_path, path)?;
    sync_parent(path)?;
    Ok(())
}

#[cfg(unix)]
fn open_temp_file(path: &Path, private: bool) -> Result<std::fs::File, ShadowReadError> {
    use std::os::unix::fs::OpenOptionsExt;

    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    if private {
        options.mode(0o600);
    }
    Ok(options.open(path)?)
}

#[cfg(not(unix))]
fn open_temp_file(path: &Path, _private: bool) -> Result<std::fs::File, ShadowReadError> {
    Ok(OpenOptions::new().write(true).create_new(true).open(path)?)
}

#[cfg(not(windows))]
fn replace_file(temp_path: &Path, path: &Path) -> Result<(), ShadowReadError> {
    fs::rename(temp_path, path)?;
    Ok(())
}

#[cfg(windows)]
fn replace_file(temp_path: &Path, path: &Path) -> Result<(), ShadowReadError> {
    if !path.exists() {
        fs::rename(temp_path, path)?;
        return Ok(());
    }

    use std::os::windows::ffi::OsStrExt;
    use std::ptr;

    #[link(name = "Kernel32")]
    extern "system" {
        fn ReplaceFileW(
            lp_replaced_file_name: *const u16,
            lp_replacement_file_name: *const u16,
            lp_backup_file_name: *const u16,
            dw_replace_flags: u32,
            lp_exclude: *mut std::ffi::c_void,
            lp_reserved: *mut std::ffi::c_void,
        ) -> i32;
    }

    const REPLACEFILE_WRITE_THROUGH: u32 = 0x0000_0001;

    let target: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let replacement: Vec<u16> = temp_path.as_os_str().encode_wide().chain(Some(0)).collect();
    let replaced = unsafe {
        ReplaceFileW(
            target.as_ptr(),
            replacement.as_ptr(),
            ptr::null(),
            REPLACEFILE_WRITE_THROUGH,
            ptr::null_mut(),
            ptr::null_mut(),
        )
    };
    if replaced == 0 {
        return Err(ShadowReadError::Io(std::io::Error::last_os_error()));
    }
    Ok(())
}

#[cfg(unix)]
fn sync_parent(path: &Path) -> Result<(), ShadowReadError> {
    let parent = path.parent().ok_or_else(|| {
        ShadowReadError::Parse(format!("authority path has no parent: {}", path.display()))
    })?;
    File::open(parent)?.sync_all()?;
    Ok(())
}

#[cfg(not(unix))]
fn sync_parent(_: &Path) -> Result<(), ShadowReadError> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn atomic_write_creates_and_replaces_file() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("authority.json");

        atomic_write(&path, br#"{"version":1}"#).unwrap();
        assert_eq!(fs::read(&path).unwrap(), br#"{"version":1}"#);

        atomic_write(&path, br#"{"version":2}"#).unwrap();
        assert_eq!(fs::read(&path).unwrap(), br#"{"version":2}"#);
        assert_eq!(
            fs::read_dir(temp.path())
                .unwrap()
                .filter_map(Result::ok)
                .count(),
            1
        );
    }

    #[test]
    fn atomic_write_preserves_existing_file_when_temp_creation_fails() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("authority.json");
        fs::write(&path, b"old").unwrap();

        let missing_parent_path = temp.path().join("blocked").join("authority.json");
        fs::write(temp.path().join("blocked"), b"not a directory").unwrap();

        assert!(atomic_write(&missing_parent_path, b"new").is_err());
        assert_eq!(fs::read(&path).unwrap(), b"old");
    }

    #[cfg(unix)]
    #[test]
    fn private_atomic_write_is_private_before_and_after_replacement() {
        use std::os::unix::fs::PermissionsExt;

        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("secrets.json");

        atomic_write_private(&path, b"first").unwrap();
        let first_mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(first_mode, 0o600);

        atomic_write_private(&path, b"second").unwrap();
        let second_mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(second_mode, 0o600);
        assert_eq!(fs::read(&path).unwrap(), b"second");
    }
}
