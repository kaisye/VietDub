//! Optional "backend delta" update track.
//!
//! The Tauri updater (tauri-plugin-updater) replaces the whole app — shell,
//! frontend and the bundled FastAPI sidecar — atomically and is the primary,
//! signed update path. This module adds a lighter, opt-in track: it pulls a
//! newer *backend-only* build (the frozen `videodubbing-api` executable) from a
//! signed manifest into the per-user data directory, so a backend-only feature
//! update does not require re-running the full installer.
//!
//! It is intentionally non-breaking: packaged builds keep launching the bundled
//! sidecar unless a checksum-verified managed backend has already been
//! installed (see `managed_backend_executable`, consulted by `spawn_backend`).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use tauri::{AppHandle, Manager};

use crate::managed_tools::{download_file, verify_sha256};

/// Where the backend manifest is published. Defaults to the GitHub release the
/// installer is cut from; override at build/run time via VIETDUB_BACKEND_MANIFEST.
fn manifest_url() -> String {
    std::env::var("VIETDUB_BACKEND_MANIFEST").unwrap_or_else(|_| {
        "https://github.com/kaisye/VietDubPublic/releases/latest/download/backend-manifest.json"
            .to_string()
    })
}

#[derive(Debug, Deserialize)]
struct BackendManifest {
    version: String,
    /// Per-platform downloads, keyed by `platform_key()` (e.g. "windows-x86_64").
    platforms: HashMap<String, PlatformEntry>,
}

#[derive(Debug, Deserialize)]
struct PlatformEntry {
    url: String,
    sha256: String,
}

fn platform_key() -> &'static str {
    #[cfg(all(target_os = "windows", target_arch = "x86_64"))]
    {
        "windows-x86_64"
    }
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    {
        "macos-aarch64"
    }
    #[cfg(not(any(
        all(target_os = "windows", target_arch = "x86_64"),
        all(target_os = "macos", target_arch = "aarch64")
    )))]
    {
        "unsupported"
    }
}

fn backend_exe_name() -> &'static str {
    if cfg!(windows) {
        "videodubbing-api.exe"
    } else {
        "videodubbing-api"
    }
}

/// Path to the active managed backend executable, if one has been installed and
/// the `current` pointer references an existing build. Returns `None` for a
/// fresh install so the bundled sidecar is used instead.
pub fn managed_backend_executable(data_dir: &Path) -> Option<PathBuf> {
    let backend_root = data_dir.join("backend");
    let version = std::fs::read_to_string(backend_root.join("current.txt")).ok()?;
    let version = version.trim();
    if version.is_empty() {
        return None;
    }
    let exe = backend_root.join(version).join(backend_exe_name());
    exe.exists().then_some(exe)
}

/// Tauri command: check the manifest and, if a build for this platform is
/// published, download + verify it into the per-user data dir and mark it
/// current. Returns the installed version string.
#[tauri::command]
pub async fn update_backend(app: AppHandle) -> Result<String, String> {
    let data_dir = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    tauri::async_runtime::spawn_blocking(move || perform_backend_update(&data_dir))
        .await
        .map_err(|e| e.to_string())?
}

fn fetch_manifest() -> Result<BackendManifest, String> {
    let url = manifest_url();
    let body = reqwest::blocking::get(&url)
        .map_err(|e| format!("Backend manifest fetch failed: {e}"))?
        .error_for_status()
        .map_err(|e| format!("Backend manifest fetch failed: {e}"))?
        .text()
        .map_err(|e| e.to_string())?;
    serde_json::from_str(&body).map_err(|e| format!("Invalid backend manifest: {e}"))
}

fn write_current(backend_root: &Path, version: &str) -> Result<(), String> {
    std::fs::create_dir_all(backend_root).map_err(|e| e.to_string())?;
    std::fs::write(backend_root.join("current.txt"), version).map_err(|e| e.to_string())
}

fn perform_backend_update(data_dir: &Path) -> Result<String, String> {
    let manifest = fetch_manifest()?;
    let key = platform_key();
    let entry = manifest
        .platforms
        .get(key)
        .ok_or_else(|| format!("No backend build published for platform {key}"))?;

    let backend_root = data_dir.join("backend");
    let version_dir = backend_root.join(&manifest.version);
    let exe = version_dir.join(backend_exe_name());

    // Already downloaded and intact: just (re)point `current` at it.
    if exe.exists() && verify_sha256(&exe, &entry.sha256).is_ok() {
        write_current(&backend_root, &manifest.version)?;
        return Ok(manifest.version);
    }

    std::fs::create_dir_all(&version_dir).map_err(|e| e.to_string())?;
    download_file(&entry.url, &exe)?;
    if let Err(err) = verify_sha256(&exe, &entry.sha256) {
        let _ = std::fs::remove_file(&exe);
        return Err(err);
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = std::fs::metadata(&exe)
            .map_err(|e| e.to_string())?
            .permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(&exe, perms).map_err(|e| e.to_string())?;
    }

    write_current(&backend_root, &manifest.version)?;
    Ok(manifest.version)
}
