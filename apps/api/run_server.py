"""Frozen entry point for the FastAPI backend.

`python -m uvicorn app.main:app` cannot be used inside a PyInstaller bundle, so
this script starts uvicorn programmatically. The Tauri desktop shell launches
the resulting executable as a sidecar and passes configuration via env vars:

    AETHER_API_HOST   bind host (default 127.0.0.1)
    AETHER_API_PORT   bind port (default 8386)
    AETHER_DATA_DIR   per-user writable dir for the SQLite db + storage
    AETHER_PARENT_PID pid of the desktop shell; the server exits when it is gone

Run directly during development with `python apps/api/run_server.py`.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import time


def _parent_alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) would terminate the process on Windows; query it instead.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _exit_with_parent(pid: int, interval: float = 2.0) -> None:
    while _parent_alive(pid):
        time.sleep(interval)
    print(f"[run_server] desktop shell (pid {pid}) is gone; shutting down.", flush=True)
    os._exit(0)


def watch_parent_process() -> None:
    """Exit when the desktop shell that launched us exits (crash, force quit)."""
    raw = os.getenv("AETHER_PARENT_PID", "").strip()
    if not raw.isdigit():
        return
    threading.Thread(
        target=_exit_with_parent, args=(int(raw),), name="parent-watchdog", daemon=True
    ).start()


def main() -> None:
    # PyInstaller one-file apps re-exec the bootloader for child processes; this
    # prevents the server from being launched recursively on Windows.
    multiprocessing.freeze_support()

    # Ensure the bundled `app` package is importable when frozen.
    if getattr(sys, "frozen", False):
        sys.path.insert(0, os.path.dirname(os.path.abspath(sys.executable)))

    watch_parent_process()

    import uvicorn

    from app.main import app

    host = os.getenv("AETHER_API_HOST", "127.0.0.1")
    port = int(os.getenv("AETHER_API_PORT", "8386"))

    # `reload`/`workers` are intentionally off: this is a single embedded server.
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
