<#
.SYNOPSIS
    Freeze the FastAPI backend with PyInstaller and place it where Tauri expects
    an `externalBin` sidecar.

.DESCRIPTION
    1. Ensures PyInstaller is installed in the active Python environment.
    2. Builds a single-file `videodubbing-api` executable from
       apps/api/videodubbing-api.spec.
    3. Copies it to apps/desktop/src-tauri/binaries/videodubbing-api-<triple>.exe
       (the target-triple suffix is required by Tauri's externalBin packaging).

    Run before `npm run desktop:build`.

.PARAMETER Python
    Python executable to use (default: "python").
#>
[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

# Repo root = two levels up from this script (apps/desktop/scripts -> repo).
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Write-Host "[build-backend] Repo root: $RepoRoot"

Push-Location $RepoRoot
try {
    # --- Resolve the Rust host target triple (e.g. x86_64-pc-windows-msvc) ---
    $hostLine = (& rustc -vV) | Where-Object { $_ -like "host:*" }
    if (-not $hostLine) { throw "Could not determine Rust host triple from 'rustc -vV'." }
    $triple = ($hostLine -replace "host:\s*", "").Trim()
    Write-Host "[build-backend] Target triple: $triple"

    # --- Ensure PyInstaller is available ---
    & $Python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[build-backend] Installing PyInstaller..."
        & $Python -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) { throw "Failed to install PyInstaller." }
    }

    # --- Build (clean) ---
    Write-Host "[build-backend] Running PyInstaller..."
    & $Python -m PyInstaller "apps/api/videodubbing-api.spec" --noconfirm --clean `
        --distpath "build/backend/dist" --workpath "build/backend/work"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    # --- Place the binary where Tauri externalBin looks for it ---
    $exeName = "videodubbing-api"
    $ext = ""
    if ($IsWindows -or $env:OS -eq "Windows_NT") { $ext = ".exe" }
    $built = Join-Path $RepoRoot "build/backend/dist/$exeName$ext"
    if (-not (Test-Path $built)) { throw "Expected built binary not found: $built" }

    $destDir = Join-Path $RepoRoot "apps/desktop/src-tauri/binaries"
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    $dest = Join-Path $destDir "$exeName-$triple$ext"
    Copy-Item -Force $built $dest
    Write-Host "[build-backend] Sidecar ready: $dest"
}
finally {
    Pop-Location
}
