use std::fs::OpenOptions;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::Duration;

use base64::Engine;
#[cfg(target_os = "macos")]
use flate2::read::GzDecoder;
use sha2::{Digest, Sha256, Sha512};
use tauri::{AppHandle, Manager};

const NODE_VERSION: &str = "v24.17.0";
#[cfg(target_os = "windows")]
const NODE_WINDOWS_SHA256: &str =
    "f2aa33b35b75aca5f3f7b85675a6f6423201053e9381911e64961f3bda2528ab";
#[cfg(target_os = "macos")]
const NODE_MACOS_ARM64_SHA256: &str =
    "4fc3266a3702eebc39cc37661cf4eeceeade307e242ab64e4d7ce7949197e11f";
const ROUTER_VERSION: &str = "0.5.4";
const ROUTER_INTEGRITY: &str =
    "ua55qQ3PQMtxypLHpBezvtYodJE315eeZ53AMIjkVMCwZTHlSHVtKJPMuaHs1dDrAqnZ3chrCMubTotCZr8J7g==";
const ROUTER_PORT: &str = "20128";
const ROUTER_ENDPOINT: &str = "http://127.0.0.1:20128/v1/models";

#[tauri::command]
pub async fn start_9router(app: AppHandle) -> Result<(), String> {
    let data_dir = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    tauri::async_runtime::spawn_blocking(move || start_managed_9router(&data_dir))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn install_9router(app: AppHandle) -> Result<(), String> {
    let data_dir = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    tauri::async_runtime::spawn_blocking(move || install_managed_9router(&data_dir))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn status_9router() -> Result<bool, String> {
    tauri::async_runtime::spawn_blocking(managed_9router_ready)
        .await
        .map_err(|e| e.to_string())
}

fn install_managed_9router(data_dir: &Path) -> Result<(), String> {
    let tools_dir = data_dir.join("tools");
    let downloads_dir = tools_dir.join("downloads");
    std::fs::create_dir_all(&downloads_dir).map_err(|e| e.to_string())?;
    let node = ensure_managed_node(&tools_dir, &downloads_dir)?;
    let npm_cli = managed_npm_cli(&node)?;
    if !npm_cli.exists() {
        return Err("Managed Node archive does not contain npm-cli.js".to_string());
    }

    // Avoid running npm over a complete installation. On Windows that can leave
    // files locked by a previously launched router and turn npm cleanup into an
    // EPERM failure.
    if router_server(&tools_dir).exists() {
        return Ok(());
    }

    let archive = downloads_dir.join(format!("9router-{ROUTER_VERSION}.tgz"));
    download_file(
        &format!("https://registry.npmjs.org/9router/-/9router-{ROUTER_VERSION}.tgz"),
        &archive,
    )?;
    verify_sha512_base64(&archive, ROUTER_INTEGRITY)?;

    let install_dir = tools_dir.join("9router");
    stop_managed_9router(&tools_dir)?;
    remove_incomplete_install(&install_dir)?;
    std::fs::create_dir_all(&install_dir).map_err(|e| e.to_string())?;
    let mut command = Command::new(&node);
    command
        .arg(&npm_cli)
        .args(["install", "--no-audit", "--no-fund", "--prefix"])
        .arg(&install_dir)
        .arg(&archive)
        .current_dir(&install_dir)
        .env("HOME", data_dir)
        .env("USERPROFILE", data_dir);
    prepend_managed_node_to_path(&mut command, &node)?;
    let output = command
        .output()
        .map_err(|e| format!("Unable to run managed npm: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "9router installation failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    if !router_server(&tools_dir).exists() {
        return Err("9router installed without its server entrypoint".to_string());
    }
    Ok(())
}

fn managed_npm_cli(node: &Path) -> Result<PathBuf, String> {
    let bin_dir = node
        .parent()
        .ok_or_else(|| "Invalid managed Node path".to_string())?;
    #[cfg(target_os = "windows")]
    let npm_cli = bin_dir.join("node_modules/npm/bin/npm-cli.js");
    #[cfg(not(target_os = "windows"))]
    let npm_cli = bin_dir.join("../lib/node_modules/npm/bin/npm-cli.js");
    Ok(npm_cli)
}

fn prepend_managed_node_to_path(command: &mut Command, node: &Path) -> Result<(), String> {
    let node_bin = node
        .parent()
        .ok_or_else(|| "Invalid managed Node path".to_string())?;
    let mut paths = vec![node_bin.to_path_buf()];
    if let Some(current) = std::env::var_os("PATH") {
        paths.extend(std::env::split_paths(&current));
    }
    let path = std::env::join_paths(paths).map_err(|e| e.to_string())?;
    command.env("PATH", path);
    Ok(())
}

fn remove_incomplete_install(install_dir: &Path) -> Result<(), String> {
    if !install_dir.exists() {
        return Ok(());
    }
    let mut last_error = None;
    for _ in 0..5 {
        match std::fs::remove_dir_all(install_dir) {
            Ok(()) => return Ok(()),
            Err(error) => {
                last_error = Some(error);
                thread::sleep(Duration::from_millis(300));
            }
        }
    }
    Err(format!(
        "Unable to clean the incomplete 9router installation at {}: {}. Close VietDub and retry.",
        install_dir.display(),
        last_error.expect("cleanup attempted")
    ))
}

#[cfg(target_os = "windows")]
fn stop_managed_9router(tools_dir: &Path) -> Result<(), String> {
    let node = managed_node_executable(tools_dir);
    if !node.exists() {
        return Ok(());
    }
    let script = r#"
$managedNode = $env:VIETDUB_MANAGED_NODE
Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq 'node.exe' -and
    $_.ExecutablePath -and
    [string]::Equals($_.ExecutablePath, $managedNode, [StringComparison]::OrdinalIgnoreCase)
  } |
  ForEach-Object {
    & "$env:SystemRoot\System32\taskkill.exe" /F /T /PID $_.ProcessId | Out-Null
  }
"#;
    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .env("VIETDUB_MANAGED_NODE", &node)
        .output()
        .map_err(|e| format!("Unable to inspect managed 9router processes: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "Unable to stop the previous managed 9router process: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    thread::sleep(Duration::from_millis(500));
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn stop_managed_9router(_tools_dir: &Path) -> Result<(), String> {
    Ok(())
}

fn start_managed_9router(data_dir: &Path) -> Result<(), String> {
    if managed_9router_ready() {
        return Ok(());
    }
    let tools_dir = data_dir.join("tools");
    let node = managed_node_executable(&tools_dir);
    let server = router_server(&tools_dir);
    if !node.exists() || !server.exists() {
        return Err("9router is not installed. Run the in-app setup first.".to_string());
    }
    let server_dir = server
        .parent()
        .ok_or_else(|| "Invalid 9router server path".to_string())?;
    let runtime_dir = data_dir.join("runtime");
    std::fs::create_dir_all(&runtime_dir).map_err(|e| e.to_string())?;
    let log_path = runtime_dir.join("9router.log");
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| format!("Unable to open 9router log: {e}"))?;
    let stderr = log
        .try_clone()
        .map_err(|e| format!("Unable to prepare 9router log: {e}"))?;

    let mut command = Command::new(&node);
    command
        .arg("--max-old-space-size=6144")
        .arg(&server)
        .current_dir(server_dir)
        .env("HOME", data_dir)
        .env("USERPROFILE", data_dir)
        .env("HOSTNAME", "127.0.0.1")
        .env("PORT", ROUTER_PORT)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(stderr));
    prepend_managed_node_to_path(&mut command, &node)?;
    let mut child = command
        .spawn()
        .map_err(|e| format!("Unable to start managed 9router: {e}"))?;

    // The upstream CLI prints "running" before its detached server is healthy.
    // Start the packaged server directly and only report success after it answers.
    for _ in 0..120 {
        if managed_9router_ready() {
            return Ok(());
        }
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            return Err(format!(
                "9router server exited with status {status}. {}",
                router_log_summary(&log_path)
            ));
        }
        thread::sleep(Duration::from_millis(500));
    }

    let _ = child.kill();
    let _ = child.wait();
    Err(format!(
        "9router did not respond on port {ROUTER_PORT} after 60 seconds. {}",
        router_log_summary(&log_path)
    ))
}

fn router_log_summary(log_path: &Path) -> String {
    let location = format!("Log: {}", log_path.display());
    let Ok(contents) = std::fs::read_to_string(log_path) else {
        return location;
    };
    let tail: String = contents
        .chars()
        .rev()
        .take(2000)
        .collect::<String>()
        .chars()
        .rev()
        .collect();
    let tail = tail.trim();
    if tail.is_empty() {
        location
    } else {
        format!("{location}\nLast output:\n{tail}")
    }
}

fn managed_9router_ready() -> bool {
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(2500))
        .build()
    {
        Ok(client) => client,
        Err(_) => return false,
    };
    match client.get(ROUTER_ENDPOINT).send() {
        Ok(response) => {
            response.status().is_success()
                || response.status() == reqwest::StatusCode::UNAUTHORIZED
                || response.status() == reqwest::StatusCode::FORBIDDEN
        }
        Err(_) => false,
    }
}

fn router_cli(tools_dir: &Path) -> PathBuf {
    tools_dir
        .join("9router")
        .join("node_modules")
        .join("9router")
        .join("cli.js")
}

fn router_server(tools_dir: &Path) -> PathBuf {
    let app_dir = router_cli(tools_dir)
        .parent()
        .expect("router cli path has a parent")
        .join("app");
    let custom_server = app_dir.join("custom-server.js");
    if custom_server.exists() {
        custom_server
    } else {
        app_dir.join("server.js")
    }
}

fn managed_node_executable(tools_dir: &Path) -> PathBuf {
    let filename = if cfg!(target_os = "windows") {
        "node.exe"
    } else {
        "bin/node"
    };
    tools_dir.join("node").join(filename)
}

fn ensure_managed_node(tools_dir: &Path, downloads_dir: &Path) -> Result<PathBuf, String> {
    let executable = managed_node_executable(tools_dir);
    if executable.exists() {
        return Ok(executable);
    }

    #[cfg(target_os = "windows")]
    let (archive_name, url, checksum) = (
        format!("node-{NODE_VERSION}-win-x64.zip"),
        format!("https://nodejs.org/dist/{NODE_VERSION}/node-{NODE_VERSION}-win-x64.zip"),
        NODE_WINDOWS_SHA256,
    );
    #[cfg(target_os = "macos")]
    let (archive_name, url, checksum) = (
        format!("node-{NODE_VERSION}-darwin-arm64.tar.gz"),
        format!("https://nodejs.org/dist/{NODE_VERSION}/node-{NODE_VERSION}-darwin-arm64.tar.gz"),
        NODE_MACOS_ARM64_SHA256,
    );
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    return Err("Managed 9router currently supports Windows and macOS only.".to_string());

    let archive = downloads_dir.join(archive_name);
    download_file(&url, &archive)?;
    verify_sha256(&archive, checksum)?;
    let staging = tools_dir.join("node-staging");
    if staging.exists() {
        std::fs::remove_dir_all(&staging).map_err(|e| e.to_string())?;
    }
    std::fs::create_dir_all(&staging).map_err(|e| e.to_string())?;

    #[cfg(target_os = "windows")]
    extract_zip(&archive, &staging)?;
    #[cfg(target_os = "macos")]
    extract_tar_gz(&archive, &staging)?;

    let extracted = std::fs::read_dir(&staging)
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .find(|entry| entry.path().is_dir())
        .map(|entry| entry.path())
        .ok_or_else(|| "Node archive did not contain a root directory".to_string())?;
    let destination = tools_dir.join("node");
    if destination.exists() {
        std::fs::remove_dir_all(&destination).map_err(|e| e.to_string())?;
    }
    std::fs::rename(extracted, &destination).map_err(|e| e.to_string())?;
    let _ = std::fs::remove_dir_all(&staging);
    if !executable.exists() {
        return Err("Node extraction completed but executable is missing".to_string());
    }
    Ok(executable)
}

pub(crate) fn download_file(url: &str, destination: &Path) -> Result<(), String> {
    let response = reqwest::blocking::get(url)
        .map_err(|e| format!("Download failed for {url}: {e}"))?
        .error_for_status()
        .map_err(|e| format!("Download failed for {url}: {e}"))?;
    let bytes = response.bytes().map_err(|e| e.to_string())?;
    let temporary = destination.with_extension("download");
    std::fs::write(&temporary, bytes).map_err(|e| e.to_string())?;
    if destination.exists() {
        std::fs::remove_file(destination).map_err(|e| e.to_string())?;
    }
    std::fs::rename(&temporary, destination).map_err(|e| e.to_string())
}

pub(crate) fn verify_sha256(path: &Path, expected: &str) -> Result<(), String> {
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let actual = format!("{:x}", Sha256::digest(bytes));
    if actual != expected {
        return Err(format!("SHA-256 mismatch for {}", path.display()));
    }
    Ok(())
}

fn verify_sha512_base64(path: &Path, expected: &str) -> Result<(), String> {
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let actual = Sha512::digest(bytes);
    let expected = base64::engine::general_purpose::STANDARD
        .decode(expected)
        .map_err(|e| e.to_string())?;
    if actual[..] != expected[..] {
        return Err(format!("SHA-512 mismatch for {}", path.display()));
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn extract_zip(archive: &Path, destination: &Path) -> Result<(), String> {
    let file = std::fs::File::open(archive).map_err(|e| e.to_string())?;
    let mut zip = zip::ZipArchive::new(file).map_err(|e| e.to_string())?;
    for index in 0..zip.len() {
        let mut entry = zip.by_index(index).map_err(|e| e.to_string())?;
        let relative = entry
            .enclosed_name()
            .ok_or_else(|| "Unsafe path in Node zip archive".to_string())?;
        let output = destination.join(relative);
        if entry.is_dir() {
            std::fs::create_dir_all(&output).map_err(|e| e.to_string())?;
        } else {
            if let Some(parent) = output.parent() {
                std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
            let mut target = std::fs::File::create(output).map_err(|e| e.to_string())?;
            std::io::copy(&mut entry, &mut target).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn extract_tar_gz(archive: &Path, destination: &Path) -> Result<(), String> {
    let file = std::fs::File::open(archive).map_err(|e| e.to_string())?;
    let decoder = GzDecoder::new(file);
    let mut archive = tar::Archive::new(decoder);
    archive.unpack(destination).map_err(|e| e.to_string())
}
