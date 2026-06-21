"""Frozen entry point for the FastAPI backend.

`python -m uvicorn app.main:app` cannot be used inside a PyInstaller bundle, so
this script starts uvicorn programmatically. The Tauri desktop shell launches
the resulting executable as a sidecar and passes configuration via env vars:

    AETHER_API_HOST   bind host (default 127.0.0.1)
    AETHER_API_PORT   bind port (default 8386)
    AETHER_DATA_DIR   per-user writable dir for the SQLite db + storage

Run directly during development with `python apps/api/run_server.py`.
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def main() -> None:
    # PyInstaller one-file apps re-exec the bootloader for child processes; this
    # prevents the server from being launched recursively on Windows.
    multiprocessing.freeze_support()

    # Ensure the bundled `app` package is importable when frozen.
    if getattr(sys, "frozen", False):
        sys.path.insert(0, os.path.dirname(os.path.abspath(sys.executable)))

    import uvicorn

    from app.main import app

    host = os.getenv("AETHER_API_HOST", "127.0.0.1")
    port = int(os.getenv("AETHER_API_PORT", "8386"))

    # `reload`/`workers` are intentionally off: this is a single embedded server.
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
