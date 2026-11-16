"""One backend per database.

Every backend runs a job worker against its SQLite database and, on startup,
fails the jobs it finds "running" (they were interrupted). A second backend
on the same data directory (a forgotten ``tauri dev`` sidecar, the app opened
twice) would steal queued jobs and run them with its own, possibly older,
code, and kill the first backend's running jobs at startup. So the backend
takes an exclusive lock next to the database and refuses to start without it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import IO

_HANDLE: IO[str] | None = None


class DataDirInUse(RuntimeError):
    pass


def lock_path_for(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    db_path = Path(database_url.removeprefix("sqlite:///")).expanduser().resolve()
    return db_path.with_name(db_path.name + ".lock")


def _try_lock(handle: IO[str]) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def acquire(lock_path: Path | None, *, wait_seconds: float = 10.0) -> None:
    """Hold the database lock for the life of this process, or raise DataDirInUse.

    It waits a little first: ``uvicorn --reload`` starts the new server while
    the old one is still exiting.
    """
    global _HANDLE
    if lock_path is None or _HANDLE is not None:
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while not _try_lock(handle):
        if time.monotonic() >= deadline:
            handle.seek(0)
            holder = handle.read().strip() or "unknown"
            handle.close()
            raise DataDirInUse(
                f"Another VietDub backend (pid {holder}) is already using {lock_path.parent}. "
                "Quit it before starting a second one: two backends on one database steal "
                "each other's jobs."
            )
        time.sleep(0.25)
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _HANDLE = handle  # Released by the OS when the process exits.
