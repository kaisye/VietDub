<#
.SYNOPSIS
    Produce backend-manifest.json for the optional backend delta-update track
    consumed by apps/desktop/src-tauri/src/managed_backend.rs.

.DESCRIPTION
    The Tauri updater handles full-app updates. This manifest enables an
    additional backend-only track: the app downloads a newer frozen
    `videodubbing-api` executable into the per-user data dir and verifies it by
    SHA-256. Run this after a release build, pointing at the uploaded binaries,
    then publish the resulting backend-manifest.json to the GitHub release named
    in apps/desktop/src-tauri/src/managed_backend.rs (manifest_url()).

.EXAMPLE
    ./scripts/make-backend-manifest.ps1 -Version 0.1.0 `
      -BaseUrl https://github.com/kaisye/VietDub/releases/download/v0.1.0 `
      -WindowsBackend build/backend/dist/videodubbing-api.exe
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [string]$WindowsBackend = "",
    [string]$MacBackend = "",
    [string]$OutFile = "release/backend-manifest.json"
)

$ErrorActionPreference = "Stop"
$platforms = [ordered]@{}

function Add-Platform([string]$key, [string]$path, [string]$fileName) {
    if (-not $path) { return }
    if (-not (Test-Path -LiteralPath $path)) { throw "Backend binary not found: $path" }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    $platforms[$key] = [ordered]@{ url = "$BaseUrl/$fileName"; sha256 = $hash }
}

Add-Platform "windows-x86_64" $WindowsBackend "videodubbing-api-x86_64-pc-windows-msvc.exe"
Add-Platform "macos-aarch64" $MacBackend "videodubbing-api-aarch64-apple-darwin"
if ($platforms.Count -eq 0) { throw "Provide at least one of -WindowsBackend / -MacBackend." }

$manifest = [ordered]@{ version = $Version; platforms = $platforms }
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile -Parent) | Out-Null
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutFile -Encoding utf8
Write-Host "Wrote $OutFile"
