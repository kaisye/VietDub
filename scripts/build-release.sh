#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "VietDub macOS release must be built on Apple Silicon." >&2
  exit 1
fi

# Fail before downloading/building dependencies when macOS developer tools and
# the active SDK are from different releases.  The mismatch otherwise surfaces
# much later as opaque Rust crate failures (for example, `quote` or `serde`).
TOOLCHAIN_PROBE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vietdub-toolchain.XXXXXX")"
trap 'rm -rf "$TOOLCHAIN_PROBE_DIR"' EXIT
TOOLCHAIN_PROBE_LOG="$TOOLCHAIN_PROBE_DIR/linker.log"
if ! printf 'int main(void) { return 0; }\n' | xcrun --sdk macosx clang -x c - \
  -o "$TOOLCHAIN_PROBE_DIR/probe" 2>"$TOOLCHAIN_PROBE_LOG"; then
  # Some CLT installations leave a newer, incompatible SDK selected while a
  # working SDK is also installed. Pick the newest linkable real SDK directory.
  DEVELOPER_DIR="$(xcode-select -p 2>/dev/null || true)"
  COMPATIBLE_SDK=""
  for CANDIDATE_SDK in "$DEVELOPER_DIR"/SDKs/MacOSX*.sdk; do
    [[ -d "$CANDIDATE_SDK" && ! -L "$CANDIDATE_SDK" ]] || continue
    if printf 'int main(void) { return 0; }\n' | \
      SDKROOT="$CANDIDATE_SDK" xcrun clang -isysroot "$CANDIDATE_SDK" -x c - \
        -o "$TOOLCHAIN_PROBE_DIR/probe" 2>/dev/null; then
      COMPATIBLE_SDK="$CANDIDATE_SDK"
    fi
  done

  if [[ -n "$COMPATIBLE_SDK" ]]; then
    export SDKROOT="$COMPATIBLE_SDK"
    echo "Default macOS SDK is not linkable; using compatible SDK: $SDKROOT"
  else
    cat "$TOOLCHAIN_PROBE_LOG" >&2
  cat >&2 <<'EOF'

The active macOS SDK cannot be linked by the selected developer tools.
This is a system toolchain problem, not a Rust/Tauri dependency problem.

Reinstall or update Xcode Command Line Tools, then retry:
  sudo rm -rf /Library/Developer/CommandLineTools
  xcode-select --install

Only if full Xcode is installed at /Applications/Xcode.app, select its
matching toolchain instead:
  sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
EOF
    exit 1
  fi
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
