#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "VietDub macOS release must be built on Apple Silicon." >&2
  exit 1
fi

VERSION="${1:-$(node -p "require('./apps/desktop/src-tauri/tauri.conf.json').version")}" 
PYTHON="${PYTHON:-python3.12}"
VENV="$ROOT/.build/venv-macos-arm64"

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r apps/api/requirements.txt pyinstaller

npm ci
npm --workspace apps/desktop run prepare:media

"$VENV/bin/python" -m PyInstaller apps/api/videodubbing-api.spec \
  --noconfirm --clean \
  --distpath build/backend/dist \
  --workpath build/backend/work

mkdir -p apps/desktop/src-tauri/binaries
cp build/backend/dist/videodubbing-api \
  apps/desktop/src-tauri/binaries/videodubbing-api-aarch64-apple-darwin
chmod +x apps/desktop/src-tauri/binaries/videodubbing-api-aarch64-apple-darwin

npm --workspace apps/desktop run tauri build -- \
  --target aarch64-apple-darwin --bundles dmg

BUNDLE="$(find apps/desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/dmg -name '*.dmg' -print -quit)"
if [[ -z "$BUNDLE" ]]; then
  echo "DMG bundle was not produced." >&2
  exit 1
fi

mkdir -p release
ARTIFACT="release/VietDub-${VERSION}-macos-arm64.dmg"
cp "$BUNDLE" "$ARTIFACT"
shasum -a 256 "$ARTIFACT" > "${ARTIFACT}.sha256"
echo "Release ready: $ARTIFACT"
