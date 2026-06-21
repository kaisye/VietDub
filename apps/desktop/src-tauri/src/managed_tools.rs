use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

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

fn install_managed_9router(data_dir: &Path) -> Result<(), String> {
    let tools_dir = data_dir.join("tools");
    let downloads_dir = tools_dir.join("downloads");
    std::fs::create_dir_all(&downloads_dir).map_err(|e| e.to_string())?;
    let node = ensure_managed_node(&tools_dir, &downloads_dir)?;
    let npm_cli = node
        .parent()
        .ok_or_else(|| "Invalid managed Node path".to_string())?
        .join("node_modules")
        .join("npm")
        .join("bin")
        .join("npm-cli.js");
    if !npm_cli.exists() {
        return Err("Managed Node archive does not contain npm-cli.js".to_string());
    }

    let archive = downloads_dir.join(format!("9router-{ROUTER_VERSION}.tgz"));
    download_file(
        &format!("https://registry.npmjs.org/9router/-/9router-{ROUTER_VERSION}.tgz"),
        &archive,
    )?;
    verify_sha512_base64(&archive, ROUTER_INTEGRITY)?;

    let install_dir = tools_dir.join("9router");
    std::fs::create_dir_all(&install_dir).map_err(|e| e.to_string())?;
    let output = Command::new(&node)
        .arg(&npm_cli)
        .args(["install", "--no-audit", "--no-fund", "--prefix"])
        .arg(&install_dir)
        .arg(&archive)
        .output()
        .map_err(|e| format!("Unable to run managed npm: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "9router installation failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    if !router_cli(&tools_dir).exists() {
        return Err("9router installed without its cli.js entrypoint".to_string());
    }
    Ok(())
}

fn start_managed_9router(data_dir: &Path) -> Result<(), String> {
    let tools_dir = data_dir.join("tools");
    let node = managed_node_executable(&tools_dir);
    let cli = router_cli(&tools_dir);
    if !node.exists() || !cli.exists() {
        return Err("9router is not installed. Run the in-app setup first.".to_string());
    }
    Command::new(node)
        .arg(cli)
        .current_dir(data_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Unable to start managed 9router: {e}"))?;
    Ok(())
}

fn router_cli(tools_dir: &Path) -> PathBuf {
    tools_dir
        .join("9router")
        .join("node_modules")
        .join("9router")
        .join("cli.js")
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
