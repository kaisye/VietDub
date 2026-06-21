# VietDub — desktop shell (Tauri)

Tauri v2 wrapper around the Vite + React SPA. The Rust shell starts the FastAPI
backend as a child process so the whole tool runs from one window.

- **Dev:** loads the Vite dev server and runs the backend from source
  (`python -m uvicorn`).
- **Packaged:** serves the static `dist/` frontend and runs a frozen
  single-file FastAPI backend bundled as a Tauri `externalBin` sidecar.

## Prerequisites

- Rust toolchain (`cargo`, `rustc`)
- Node deps installed: `npm install`
- Python backend deps installed (same env used for `npm run dev:api`)
- FFmpeg on PATH (rendering / yt-dlp post-processing)
- Windows: WebView2 runtime (preinstalled on Windows 11)

## Run the dev shell

From the repository root:

```powershell
npm run desktop:dev
```

`tauri dev` then:

1. starts the Vite dev server (`npm run dev`, port 5173), and
2. spawns the FastAPI backend on `127.0.0.1:8386` (from `src/main.rs`).

The backend child process is terminated when the window closes.

### Environment overrides

- `AETHER_DESKTOP_SKIP_BACKEND=1` — do not start the backend (run it yourself).
- `AETHER_PYTHON` — Python executable for the dev backend (default `python`).
- `AETHER_API_PORT` — backend port (dev default `8386`, packaged default `18386`).
- `AETHER_DATA_DIR` — writable dir for the SQLite db + media storage. In a
  packaged build the shell sets this to the OS app-local-data dir automatically;
  unset in dev (uses the repo-relative `storage/` + `aether_studio.db`).

Frontend → backend base URL is `VITE_API_BASE_URL` (dev requests use the Vite
proxy to `8386`; packaged builds use `http://127.0.0.1:18386`).

## Production packaging

Two artifacts go into the installer:

1. **Static frontend** — `npm run build` (Vite) → `apps/desktop/dist/`, served by
   the WebView. No Node sidecar.
2. **Frozen backend** — `npm run build:backend` runs PyInstaller via
   [`scripts/build-backend.ps1`](../scripts/build-backend.ps1), producing a
   single-file `videodubbing-api` executable and copying it to
   `src-tauri/binaries/videodubbing-api-<target-triple>(.exe)`. This is declared
   in `tauri.conf.json > bundle.externalBin`, so Tauri ships it next to the main
   binary and `main.rs` launches it (preferring it over the dev `python` path).

Full build (does both, then the installer):

```powershell
npm run desktop:build
```

Output installers land in `src-tauri/target/release/bundle/` (`.msi` / `.exe`
on Windows).

### Notes

- **FFmpeg is not bundled.** It must be on PATH at runtime. Bundling it as an
  additional `externalBin`/resource is a possible follow-up.
- OmniVoice local/Colab runtimes run out-of-process and are intentionally
  excluded from the frozen backend (it falls back to Edge TTS when neither is
  available).
- The frozen backend keeps all mutable state under `AETHER_DATA_DIR`, never in
  the install directory.
