[CmdletBinding()]
param([string]$Path = ".")

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $Path).Path
$required = @(
    "README.md", ".env.example", "package.json", "package-lock.json",
    "apps/api/app/main.py", "apps/api/run_server.py",
    "apps/api/videodubbing-api.spec", "apps/desktop/src/App.tsx",
    "apps/desktop/src-tauri/tauri.conf.json", "scripts/build-release.ps1",
    "scripts/build-release.sh", ".github/workflows/release.yml"
)
$forbidden = @(
    ".env", "storage", "aether_studio.db", "node_modules", "apps/web",
    "apps/api/tests", "apps/desktop/src-tauri/target"
)

foreach ($relative in $required) {
    if (-not (Test-Path (Join-Path $root $relative))) {
        throw "Distribution is missing required path: $relative"
    }
}
foreach ($relative in $forbidden) {
    if (Test-Path (Join-Path $root $relative)) {
        throw "Distribution contains forbidden path: $relative"
    }
}

Get-Content (Join-Path $root "package.json") -Raw | ConvertFrom-Json | Out-Null
Get-Content (Join-Path $root "apps/desktop/package.json") -Raw | ConvertFrom-Json | Out-Null
Get-Content (Join-Path $root "apps/desktop/src-tauri/tauri.conf.json") -Raw | ConvertFrom-Json | Out-Null
& (Join-Path $root "scripts/scan-distribution.ps1") -Path $root
Write-Host "Distribution structure test passed: $root"
