// Hide the extra console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{AppHandle, Manager, RunEvent, State};

mod managed_backend;
mod managed_tools;
use managed_tools::{install_9router, start_9router};

/// Holds the FastAPI sidecar process so it can be terminated on exit.
struct BackendProcess(Mutex<Option<Child>>);

/// Locate the repository root by walking up from the current dir and the
/// executable dir until `apps/api/app/main.py` is found. Used only for the dev
/// fallback (running the backend from source via `python -m uvicorn`).
fn find_repo_root() -> Option<PathBuf> {
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            starts.push(dir.to_path_buf());
        }
    }

    for start in starts {
        let mut dir: Option<&Path> = Some(start.as_path());
        while let Some(current) = dir {
            if current.join("apps/api/app/main.py").exists() {
                return Some(current.to_path_buf());
            }
            dir = current.parent();
        }
    }
    None
}

/// Path to the bundled backend sidecar that Tauri places next to the main
/// executable (externalBin copies it there without the target-triple suffix).
fn bundled_sidecar() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    let name = if cfg!(windows) {
        "videodubbing-api.exe"
    } else {
        "videodubbing-api"
    };
    let candidate = dir.join(name);
    if candidate.exists() {
        Some(candidate)
    } else {
        None
    }
}

/// Start the FastAPI backend as a child process. Returns `None` when skipped or
/// when the backend cannot be located/launched (the window still opens).
fn spawn_backend(app: &AppHandle) -> Option<Child> {
    if std::env::var("AETHER_DESKTOP_SKIP_BACKEND")
        .map(|v| v == "1")
        .unwrap_or(false)
    {
        eprintln!("[desktop] AETHER_DESKTOP_SKIP_BACKEND=1, not starting the backend.");
        return None;
    }

    // Keep packaged VietDub isolated from the source-development API. Otherwise
    // a locally running VideoDubbing backend on 8386 can serve its database and
    // secrets to the packaged WebView before VietDub's own sidecar starts.
    let default_port = if cfg!(debug_assertions) {
        "8386"
    } else {
        "18386"
    };
    let port = std::env::var("AETHER_API_PORT").unwrap_or_else(|_| default_port.to_string());

    // Resolve Python — honour AETHER_PYTHON, then probe common Conda/system
    // paths so the spawn works even when PATH is stripped (Windows services,
    // Tauri release builds, etc.).
    let python = std::env::var("AETHER_PYTHON").unwrap_or_else(|_| {
        let home = std::env::var("USERPROFILE")
            .or_else(|_| std::env::var("HOME"))
            .unwrap_or_default();
        let candidates = [
            format!("{home}\\anaconda3\\python.exe"),
            format!("{home}\\miniconda3\\python.exe"),
            format!("{home}\\AppData\\Local\\Programs\\Python\\Python312\\python.exe"),
            format!("{home}\\AppData\\Local\\Programs\\Python\\Python311\\python.exe"),
            format!("{home}\\AppData\\Local\\Programs\\Python\\Python310\\python.exe"),
            "python".to_string(),
            "python3".to_string(),
        ];
        for c in &candidates {
            if std::path::Path::new(c).exists() {
                eprintln!("[desktop] Found Python at: {c}");
                return c.clone();
            }
        }
        "python".to_string()
    });

    // Per-user writable directory for the SQLite db + media storage. Keeps all
    // mutable state out of a potentially read-only install location.
    let data_dir = app
        .path()
        .app_local_data_dir()
        .ok()
        .unwrap_or_else(|| std::env::temp_dir().join("VideoDubbing"));
    let _ = std::fs::create_dir_all(&data_dir);
    let runtime_dir = data_dir.join("runtime");
    let _ = std::fs::create_dir_all(&runtime_dir);

    let mut command = if let Some(managed) = managed_backend::managed_backend_executable(&data_dir)
    {
        // Opt-in backend delta track: a verified, newer backend downloaded into
        // the per-user data dir takes precedence over the bundled sidecar.
        eprintln!("[desktop] Using managed backend: {}", managed.display());
        let mut c = Command::new(&managed);
        if let Some(dir) = managed.parent() {
            c.current_dir(dir);
        }
        c
    } else if let Some(sidecar) = bundled_sidecar() {
        // Packaged build: run the frozen single-file backend.
        eprintln!("[desktop] Using bundled backend: {}", sidecar.display());
        let mut c = Command::new(&sidecar);
        if let Some(dir) = sidecar.parent() {
            c.current_dir(dir);
        }
        c
    } else {
        // Development: run the backend from source with uvicorn.
        let repo_root = match find_repo_root() {
            Some(root) => root,
            None => {
                eprintln!(
                    "[desktop] No bundled backend and could not locate apps/api; \
                     start the backend manually."
                );
                return None;
            }
        };
        let mut c = Command::new(&python);
        c.current_dir(&repo_root).args([
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            &port,
            "--app-dir",
            "apps/api",
        ]);
        c
    };

    command
        .env("AETHER_API_HOST", "127.0.0.1")
        .env("AETHER_API_PORT", &port)
        .env("AETHER_DATA_DIR", &data_dir)
        .env("AETHER_RUNTIME_DIR", &runtime_dir);

    if let Some(media_bin) = bundled_media_bin(app) {
        let mut paths = vec![media_bin];
        if let Some(current) = std::env::var_os("PATH") {
            paths.extend(std::env::split_paths(&current));
        }
        if let Ok(path) = std::env::join_paths(paths) {
            command.env("PATH", path);
        }
    }

    match command.spawn() {
        Ok(child) => {
            eprintln!("[desktop] FastAPI backend started on 127.0.0.1:{port}.");
            Some(child)
        }
        Err(err) => {
            eprintln!("[desktop] Failed to start FastAPI backend: {err}");
            None
        }
    }
}

fn bundled_media_bin(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(resource_dir) = app.path().resource_dir() {
        // NSIS preserves the configured `resources/bin/*` prefix, while other
        // Tauri targets may expose the resource directory itself as that
        // `resources` folder. Probe both layouts and require both executables.
        for candidate in [resource_dir.join("bin"), resource_dir.join("resources/bin")] {
            if has_media_tools(&candidate) {
                return Some(candidate);
            }
        }
    }
    if let Ok(executable) = std::env::current_exe() {
        if let Some(executable_dir) = executable.parent() {
            let candidate = executable_dir.join("resources/bin");
            if has_media_tools(&candidate) {
                return Some(candidate);
            }
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("resources")
        .join("bin");
    has_media_tools(&dev).then_some(dev)
}

fn has_media_tools(directory: &Path) -> bool {
    let extension = if cfg!(windows) { ".exe" } else { "" };
    directory.join(format!("ffmpeg{extension}")).is_file()
        && directory.join(format!("ffprobe{extension}")).is_file()
}

/// Spawn the 9router process (assumes already installed globally via npm).
#[allow(dead_code)]
async fn start_system_9router() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| {
        #[cfg(target_os = "windows")]
        {
            // Use cmd /c so Windows can resolve the .cmd shim in PATH
            std::process::Command::new("cmd")
                .args(["/c", "9router"])
                .spawn()
                .map_err(|e| format!("Không tìm thấy lệnh 9router: {e}"))?;
        }
        #[cfg(not(target_os = "windows"))]
        {
            std::process::Command::new("9router")
                .spawn()
                .map_err(|e| format!("9router not found: {e}"))?;
        }
        Ok::<(), String>(())
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Run `npm install -g 9router` (blocking, may take 1-2 minutes).
#[allow(dead_code)]
async fn install_system_9router() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| {
        #[cfg(target_os = "windows")]
        let output = std::process::Command::new("cmd")
            .args(["/c", "npm install -g 9router"])
            .output()
            .map_err(|_| "Node.js / npm chưa được cài. Tải tại https://nodejs.org".to_string())?;
        #[cfg(not(target_os = "windows"))]
        let output = std::process::Command::new("npm")
            .args(["install", "-g", "9router"])
            .output()
            .map_err(|_| "Node.js / npm not found — install from https://nodejs.org".to_string())?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!("npm install -g 9router thất bại:\n{stderr}"));
        }
        Ok::<(), String>(())
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Open a URL in the system default browser.
/// https:// URLs and http://localhost / http://127.0.0.1 are accepted.
#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    let allowed = url.starts_with("https://")
        || url.starts_with("http://localhost")
        || url.starts_with("http://127.0.0.1");
    if !allowed {
        return Err("Only https:// or http://localhost URLs are allowed".to_string());
    }
    #[cfg(target_os = "windows")]
    {
        // rundll32 url.dll,FileProtocolHandler opens URLs in the default browser
        // without going through cmd.exe, which would parse '&' as a command separator.
        std::process::Command::new("rundll32.exe")
            .args(["url.dll,FileProtocolHandler", &url])
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&url)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&url)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
async fn install_wsl_ubuntu() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(install_wsl_ubuntu_blocking)
        .await
        .map_err(|error| error.to_string())?
}

#[cfg(target_os = "windows")]
fn install_wsl_ubuntu_blocking() -> Result<(), String> {
    // Keep this command fixed: the UI cannot inject arbitrary elevated shell
    // arguments. Start-Process is used solely to request the Windows UAC prompt.
    let script = concat!(
        "$process = Start-Process -FilePath 'wsl.exe' ",
        "-ArgumentList @('--install','-d','Ubuntu-24.04','--no-launch') ",
        "-Verb RunAs -Wait -PassThru; ",
        "if ($process.ExitCode -ne 0) { exit $process.ExitCode }"
    );
    let status = Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ])
        .status()
        .map_err(|error| format!("Unable to start the WSL installer: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "WSL installation was cancelled or failed (exit code {}).",
            status.code().unwrap_or(-1)
        ))
    }
}

#[cfg(not(target_os = "windows"))]
fn install_wsl_ubuntu_blocking() -> Result<(), String> {
    Err("Automatic WSL installation is only available on Windows.".to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(BackendProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            open_external_url,
            install_wsl_ubuntu,
            start_9router,
            install_9router,
            managed_backend::update_backend,
        ])
        .setup(|app| {
            let child = spawn_backend(app.handle());
            let state: State<BackendProcess> = app.state();
            *state.0.lock().unwrap() = child;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Video Dubbing desktop app")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                let state: State<BackendProcess> = app_handle.state();
                let child = state.0.lock().unwrap().take();
                if let Some(mut child) = child {
                    let _ = child.kill();
                }
            }
        });
}
