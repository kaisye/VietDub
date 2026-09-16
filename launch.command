#!/usr/bin/env bash
# =============================================================================
#  VietDub - dev launcher cho macOS (ban tuong duong launch.bat)
#
#  Truoc khi chay, script tu kiem tra va cai nhung gi con thieu:
#    - Xcode Command Line Tools (can de build Rust)
#    - Node.js + npm, Rust (cargo)            -> cai qua Homebrew / rustup, CO HOI
#    - .venv + pip install -r apps/api/requirements.txt   -> tu dong
#    - npm install (workspace apps/desktop)               -> tu dong
#    - ffmpeg/ffprobe cho Tauri (npm run prepare:media)   -> tu dong
#    - stub sidecar binaries/videodubbing-api-*           -> tu dong
#
#  Sau do:
#    - Chay backend FastAPI tu SOURCE tai 127.0.0.1:8386 (nen, log backend-dev.log)
#    - Bat AETHER_DESKTOP_SKIP_BACKEND=1 de Tauri KHONG spawn sidecar cu (stale,
#      khac source) ma dung backend source o tren
#    - Mo app desktop (Tauri + Vite). Vite proxy /health, /jobs... -> 127.0.0.1:8386
#
#  Cach dung:
#    ./launch.command              # hoac double-click trong Finder
#    ./launch.command --check      # chi kiem tra moi truong, khong chay app
#    ./launch.command --yes        # tu dong dong y moi buoc cai dat
#    ./launch.command --skip-setup # bo qua preflight, chay thang
#    ./launch.command --reinstall  # cai lai deps du stamp chua doi
#    PYTHON=/duong/dan/python3.12 ./launch.command
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

PORT=8386
LOG="$REPO/backend-dev.log"
STAMPS="$REPO/.build/stamps"
VENV="$REPO/.venv"
REQS="$REPO/apps/api/requirements.txt"
MEDIA_BIN="$REPO/apps/desktop/src-tauri/resources/bin"
MIN_PY_MINOR=10                      # can Python >= 3.10
SIDECAR_TARGET="$(uname -m | sed 's/arm64/aarch64/')-apple-darwin"

AUTO_YES=0; DO_SETUP=1; CHECK_ONLY=0; REINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y)     AUTO_YES=1 ;;
    --check)      CHECK_ONLY=1 ;;
    --skip-setup) DO_SETUP=0 ;;
    --reinstall)  REINSTALL=1 ;;
    --help|-h)    sed -n '3,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[LOI] Tham so khong hieu: $arg (dung --help)"; exit 2 ;;
  esac
done

# Installer cua Homebrew chi IN huong dan them vao PATH chu khong tu lam, nen
# shell nay co the khong thay brew/node du da cai. Nap thang shellenv neu co.
for _brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [[ -x "$_brew" ]] && eval "$("$_brew" shellenv)" && break
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '    \033[32mOK\033[0m   %s\n' "$*"; }
warn() { printf '    \033[33mTHIEU\033[0m %s\n' "$*"; }
info() { printf '    \033[2m->\033[0m    %s\n' "$*"; }
die()  { printf '\033[1;31m[LOI]\033[0m %s\n' "$*" >&2; exit 1; }

# Hoi truoc khi dong vao may (cai Homebrew/Node/Rust). Cac buoc chi anh huong
# trong repo (.venv, node_modules) thi khong hoi.
confirm() {
  [[ "$CHECK_ONLY" == 1 ]] && { info "can cai: $1"; return 1; }
  [[ "$AUTO_YES" == 1 ]] && return 0
  [[ -t 0 ]] || { info "khong co terminal tuong tac, bo qua: $1"; return 1; }
  local reply
  read -r -p "    $1 [y/N] " reply
  [[ "$reply" =~ ^[Yy] ]]
}

stamp_current() { # $1=ten stamp, $2=file nguon -> 0 neu stamp con dung
  [[ "$REINSTALL" == 1 ]] && return 1
  local f="$STAMPS/$1"
  [[ -f "$f" ]] && [[ "$(cat "$f")" == "$(shasum -a 256 "$2" | cut -d' ' -f1)" ]]
}
stamp_write() { mkdir -p "$STAMPS"; shasum -a 256 "$2" | cut -d' ' -f1 > "$STAMPS/$1"; }

# --- Homebrew (chi cai khi mot buoc khac thuc su can) ------------------------
ensure_brew() {
  command -v brew >/dev/null 2>&1 && return 0
  [[ -x /opt/homebrew/bin/brew ]] && { eval "$(/opt/homebrew/bin/brew shellenv)"; return 0; }
  [[ -x /usr/local/bin/brew   ]] && { eval "$(/usr/local/bin/brew shellenv)"; return 0; }
  warn "Homebrew"
  confirm "Cai Homebrew (script chinh thuc tu brew.sh)?" || return 1
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [[ -x /usr/local/bin/brew   ]] && eval "$(/usr/local/bin/brew shellenv)"
  command -v brew >/dev/null 2>&1
}

check_xcode_clt() {
  if xcode-select -p >/dev/null 2>&1; then ok "Xcode Command Line Tools"; return 0; fi
  warn "Xcode Command Line Tools (Rust can linker)"
  if confirm "Mo trinh cai dat Xcode CLT?"; then
    xcode-select --install || true
    die "Hoan tat cua so cai dat Xcode CLT roi chay lai ./launch.command"
  fi
  return 1
}

check_node() {
  if command -v npm >/dev/null 2>&1; then
    ok "Node.js $(node -v 2>/dev/null) / npm $(npm -v)"
    check_brew_path
    return 0
  fi
  warn "Node.js + npm"
  confirm "Cai Node.js bang Homebrew?" || return 1
  ensure_brew || return 1
  brew install node
  command -v npm >/dev/null 2>&1
}

# Neu node den tu Homebrew ma PATH cua shell dang nhap chua co, moi terminal
# khac van "command not found: npm". De nghi ghi mot dong vao ~/.zprofile.
check_brew_path() {
  local prefix line profile="$HOME/.zprofile"
  prefix="$(brew --prefix 2>/dev/null)" || return 0
  [[ "$(command -v npm)" == "$prefix/"* ]] || return 0
  grep -qs 'brew shellenv' "$profile" && return 0
  warn "$prefix/bin chua co trong PATH cua shell dang nhap"
  confirm "Them dong 'brew shellenv' vao ~/.zprofile?" || return 0
  line="eval \"\$($prefix/bin/brew shellenv)\""
  printf '\n# Homebrew (them boi VietDub launch.command)\n%s\n' "$line" >> "$profile"
  ok "da them vao $profile (terminal moi se tu co brew/node)"
}

check_rust() {
  [[ -f "$HOME/.cargo/env" ]] && . "$HOME/.cargo/env"
  if command -v cargo >/dev/null 2>&1; then ok "Rust $(rustc --version 2>/dev/null | cut -d' ' -f2)"; return 0; fi
  warn "Rust (cargo) - Tauri dev can de build"
  confirm "Cai Rust bang rustup (sh.rustup.rs)?" || return 1
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
  . "$HOME/.cargo/env"
  command -v cargo >/dev/null 2>&1
}

# macOS 26/27: CLT co the kem SDK moi hon linker. SDK 27.0 khai kien truc
# "arm64e.x1" trong cac file .tbd ma ld-1267 chua hieu, lam MOI crate Rust fail
# o buoc link ("tapi error: malformed file ... unknown architecture"). Thu link
# thu bang SDK mac dinh; neu hong thi chon SDK moi nhat con link duoc.
sdk_links() {
  local tmp rc=0
  tmp="$(mktemp -d)"
  printf 'int main(void){return 0;}' > "$tmp/t.c"
  clang -isysroot "$1" "$tmp/t.c" -o "$tmp/t.out" >/dev/null 2>&1 || rc=1
  rm -rf "$tmp"
  return $rc
}

ensure_sdkroot() {
  command -v clang >/dev/null 2>&1 || return 0
  if [[ -n "${SDKROOT:-}" ]]; then ok "SDKROOT do ban dat: $SDKROOT"; return 0; fi
  local default_sdk dir sdk
  default_sdk="$(xcrun --show-sdk-path 2>/dev/null || true)"
  if [[ -n "$default_sdk" ]] && sdk_links "$default_sdk"; then
    ok "SDK mac dinh link duoc ($(basename "$default_sdk"))"
    return 0
  fi
  warn "SDK mac dinh ($(basename "${default_sdk:-?}")) khong link duoc - dang tim SDK thay the"
  for dir in "$(xcode-select -p)/SDKs" \
             "$(xcode-select -p)/Platforms/MacOSX.platform/Developer/SDKs"; do
    [[ -d "$dir" ]] || continue
    while IFS= read -r sdk; do
      [[ "$sdk" == "$default_sdk" ]] && continue
      if sdk_links "$sdk"; then
        export SDKROOT="$sdk"
        ok "dung SDKROOT=$(basename "$sdk")"
        return 0
      fi
    done < <(ls -d "$dir"/MacOSX*.sdk 2>/dev/null | sort -rV)
  done
  warn "khong SDK nao link duoc - build Rust nhieu kha nang se that bai"
  return 0
}

# Chon Python >= 3.10: uu tien bien PYTHON, roi .venv, roi ban moi nhat tren PATH.
py_minor() { "$1" -c 'import sys; print(sys.version_info[1] if sys.version_info[0]==3 else -1)' 2>/dev/null || echo -1; }
pick_python() {
  local cand
  if [[ -n "${PYTHON:-}" ]]; then
    [[ -x "$PYTHON" ]] || die "PYTHON=$PYTHON khong chay duoc"
    [[ "$(py_minor "$PYTHON")" -ge "$MIN_PY_MINOR" ]] || die "PYTHON=$PYTHON qua cu, can Python 3.$MIN_PY_MINOR tro len"
    echo "$PYTHON"; return 0
  fi
  for cand in python3.13 python3.12 python3.11 python3.10 python3; do
    cand="$(command -v "$cand" 2>/dev/null)" || continue
    [[ "$(py_minor "$cand")" -ge "$MIN_PY_MINOR" ]] && { echo "$cand"; return 0; }
  done
  return 1
}

check_python_venv() {
  local base
  if [[ -x "$VENV/bin/python" ]] && [[ "$(py_minor "$VENV/bin/python")" -ge "$MIN_PY_MINOR" ]]; then
    ok ".venv ($("$VENV/bin/python" --version 2>&1))"
  else
    if ! base="$(pick_python)"; then
      warn "Python 3.$MIN_PY_MINOR+ (he thong chi co $(python3 --version 2>&1))"
      confirm "Cai python@3.12 bang Homebrew?" || return 1
      ensure_brew || return 1
      brew install python@3.12
      base="$(pick_python)" || return 1
    fi
    warn ".venv chua co - tao bang $base"
    rm -rf "$VENV"
    "$base" -m venv "$VENV"
    rm -f "$STAMPS/pip"
  fi

  if stamp_current pip "$REQS"; then
    ok "pip deps khop requirements.txt"
  else
    warn "pip deps - dang cai (lan dau co the mat vai phut)"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/python" -m pip install -r "$REQS"
    stamp_write pip "$REQS"
  fi
}

check_npm_deps() {
  if [[ -d node_modules ]] && stamp_current npm package-lock.json; then
    ok "node_modules khop package-lock.json"
  else
    warn "node_modules - dang chay npm install"
    npm install
    stamp_write npm package-lock.json
  fi
}

check_media_tools() {
  if [[ -x "$MEDIA_BIN/ffmpeg" && -x "$MEDIA_BIN/ffprobe" ]]; then ok "ffmpeg/ffprobe cho Tauri"; return 0; fi
  warn "ffmpeg/ffprobe - dang chay prepare:media"
  npm --workspace apps/desktop run prepare:media
}

# Tauri doc externalBin "binaries/videodubbing-api" ngay ca o che do dev. Vi dev
# chay backend tu source (AETHER_DESKTOP_SKIP_BACKEND=1) nen chi can file rong,
# giong script check:rs cua Windows. Ban that do scripts/build-release.sh tao.
check_sidecar_stub() {
  local f="apps/desktop/src-tauri/binaries/videodubbing-api-$SIDECAR_TARGET"
  if [[ -e "$f" ]]; then ok "sidecar stub ($SIDECAR_TARGET)"; return 0; fi
  warn "sidecar stub - dang tao $f"
  mkdir -p "$(dirname "$f")"
  : > "$f"
  chmod +x "$f"
}

if [[ "$DO_SETUP" == 1 ]]; then
  say "Kiem tra moi truong ..."
  MISSING=0
  check_xcode_clt   || MISSING=1
  check_node        || MISSING=1
  check_rust        || MISSING=1
  ensure_sdkroot
  check_python_venv || MISSING=1
  if [[ "$MISSING" == 1 ]]; then
    [[ "$CHECK_ONLY" == 1 ]] && { say "--check: con thanh phan chua cai (xem o tren)."; exit 1; }
    die "Con thanh phan chua cai. Chay lai voi --yes de tu dong cai, hoac cai tay roi thu lai."
  fi
  if [[ "$CHECK_ONLY" == 1 ]]; then
    say "--check: cac thanh phan he thong da du. Bo --check de cai deps va chay app."
    exit 0
  fi
  check_npm_deps
  check_media_tools
  check_sidecar_stub
  say "Moi truong da san sang."
else
  [[ -f "$HOME/.cargo/env" ]] && . "$HOME/.cargo/env"
  ensure_sdkroot
fi

if [[ "$CHECK_ONLY" == 1 ]]; then
  say "--check: khong khoi dong app."
  exit 0
fi

# Backend dev chay ben ngoai Tauri, vi vay no khong nhan PATH ma Tauri gan cho
# sidecar dong goi. Dua hai binary vua prepare vao PATH de yt-dlp va moi buoc
# kiem tra/xu ly media deu dung dung ffmpeg/ffprobe kem theo VietDub.
[[ -x "$MEDIA_BIN/ffmpeg" && -x "$MEDIA_BIN/ffprobe" ]] \
  || die "Khong tim thay ffmpeg/ffprobe trong $MEDIA_BIN. Bo --skip-setup va chay lai."
export PATH="$MEDIA_BIN:$PATH"

PYTHON="${PYTHON:-$VENV/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(pick_python)" || die "Khong tim thay Python 3.$MIN_PY_MINOR+"
command -v npm >/dev/null 2>&1 || die "Khong tim thay npm. Bo --skip-setup hoac cai Node.js."

# --- Don dep khi thoat -------------------------------------------------------
API_PID=""
cleanup() {
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    echo ""
    say "Dung backend FastAPI (pid $API_PID) ..."
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# --- 1/2: Backend ------------------------------------------------------------
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  say "1/2: Cong $PORT da co tien trinh lang nghe - dung lai backend dang chay."
else
  say "1/2: Khoi dong backend FastAPI tai http://127.0.0.1:$PORT (log: backend-dev.log)"
  PYTHONUTF8=1 \
  KMP_DUPLICATE_LIB_OK=TRUE \
  "$PYTHON" -m uvicorn app.main:app \
    --reload --host 127.0.0.1 --port "$PORT" --app-dir apps/api \
    >"$LOG" 2>&1 &
  API_PID=$!

  say "Cho backend san sang (toi da 60s) ..."
  for _ in $(seq 1 60); do
    if ! kill -0 "$API_PID" 2>/dev/null; then
      echo "[LOI] Backend thoat som. 40 dong cuoi cua log:" >&2
      tail -n 40 "$LOG" >&2 || true
      exit 1
    fi
    curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && { ok "backend san sang"; break; }
    sleep 1
  done
fi

# --- 2/2: App desktop --------------------------------------------------------
say "2/2: Mo app desktop (Tauri dev). Lan dau build Rust co the mat vai phut ..."
export AETHER_DESKTOP_SKIP_BACKEND=1
npm run desktop:dev

echo ""
say "App desktop da dong. Backend se duoc dung lai ngay sau day."
