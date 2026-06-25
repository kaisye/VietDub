[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Path)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $Path).Path
$errors = [System.Collections.Generic.List[string]]::new()
$deniedNames = @(
    ".env", "runtime-settings.json", "cookies.txt",
    "aether_studio.db", "Voice_Ref.WAV", "voice_scripts.txt", "Instruction.txt"
)
$deniedExtensions = @(".db", ".sqlite", ".sqlite3", ".wav", ".mp3", ".flac")
$binaryExtensions = @(".png", ".ico", ".icns", ".exe", ".dmg", ".zip", ".tgz", ".wav", ".mp3", ".flac")

# Intentionally bundled voice assets: the default OmniVoice reference under
# apps/api/app/assets/. These are the user's own distributable voice setup, so
# the personal-file/extension denials are waived for this directory only — every
# other location still rejects them.
$assetsDir = [IO.Path]::GetFullPath((Join-Path $root "apps/api/app/assets"))
$allowedAssetNames = @("Voice_Ref.WAV", "voice_scripts.txt", "Instruction.txt")
$allowedAssetExtensions = @(".wav", ".mp3")
$secretPatterns = @(
    '(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----',
    '(?i)\bsk-[A-Za-z0-9_-]{20,}',
    '(?i)\bgsk_[A-Za-z0-9_-]{20,}',
    '(?i)\bnvapi-[A-Za-z0-9_-]{20,}',
    '(?i)\bhf_[A-Za-z0-9]{20,}',
    '(?im)^(NVIDIA_API_KEY|GROQ_API_KEY|HF_TOKEN|NGROK_AUTHTOKEN|OMNIVOICE_API_KEY|AETHER_LOCAL_TRANSLATION_API_KEY)[ \t]*=[ \t]*[^\r\n\s#]+',
    '(?i)\b(access_token|refresh_token|client_secret)\b\s*[:=]\s*["''][^"'']{12,}'
)

foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File -Force) {
    $ext = $file.Extension.ToLowerInvariant()
    $inAssets = $file.FullName.StartsWith($assetsDir, [System.StringComparison]::OrdinalIgnoreCase)
    if (($deniedNames -contains $file.Name) -and
        -not ($inAssets -and ($allowedAssetNames -contains $file.Name))) {
        $errors.Add("Denied personal/runtime file: $($file.FullName)")
    }
    if (($deniedExtensions -contains $ext) -and
        -not ($inAssets -and ($allowedAssetExtensions -contains $ext))) {
        $errors.Add("Denied personal media/database extension: $($file.FullName)")
    }
    if ($binaryExtensions -contains $ext) { continue }
    $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction SilentlyContinue
    if ($null -eq $content) { continue }
    foreach ($pattern in $secretPatterns) {
        if ($content -match $pattern) {
            $errors.Add("Possible secret in $($file.FullName): pattern $pattern")
        }
    }
    if ($env:USERNAME -and $content -match [regex]::Escape("C:\Users\$env:USERNAME")) {
        $errors.Add("Personal Windows path in $($file.FullName)")
    }
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_ }
    throw "Distribution scan failed with $($errors.Count) issue(s)."
}
Write-Host "Distribution scan passed: $root"
