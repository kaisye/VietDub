[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "VietDub Windows release requires a 64-bit Windows host."
}

if (-not $Version) {
    $config = Get-Content "apps/desktop/src-tauri/tauri.conf.json" -Raw | ConvertFrom-Json
    $Version = $config.version
}

if (-not $Python) {
    $python312 = Get-Command python3.12 -ErrorAction SilentlyContinue
    $Python = if ($python312) { $python312.Source } else { (Get-Command python).Source }
}

$venvDir = Join-Path $RepoRoot ".build/venv-windows"
$venvPython = Join-Path $venvDir "Scripts/python.exe"
if (-not (Test-Path $venvPython)) {
    & $Python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the release Python environment." }
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r apps/api/requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }

npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
npm.cmd --workspace apps/desktop run prepare:media
if ($LASTEXITCODE -ne 0) { throw "FFmpeg resource preparation failed." }

& "apps/desktop/scripts/build-backend.ps1" -Python $venvPython
if ($LASTEXITCODE -ne 0) { throw "Backend build failed." }

npm.cmd --workspace apps/desktop run tauri build -- --bundles nsis
if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS build failed." }

$bundle = Get-ChildItem "apps/desktop/src-tauri/target/release/bundle/nsis" -Filter "*.exe" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $bundle) { throw "NSIS bundle was not produced." }

$releaseDir = Join-Path $RepoRoot "release"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$artifact = Join-Path $releaseDir "VietDub-$Version-windows-x64-setup.exe"
Copy-Item -LiteralPath $bundle.FullName -Destination $artifact -Force
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact).Hash.ToLowerInvariant()
"$hash  $(Split-Path $artifact -Leaf)" | Set-Content "$artifact.sha256" -Encoding ascii
Write-Host "Release ready: $artifact"
