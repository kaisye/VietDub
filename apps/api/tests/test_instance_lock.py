from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app import instance_lock


def test_lock_path_sits_next_to_the_sqlite_database(tmp_path: Path) -> None:
    db = tmp_path / "aether_studio.db"
    assert instance_lock.lock_path_for(f"sqlite:///{db}") == db.with_name("aether_studio.db.lock").resolve()
    assert instance_lock.lock_path_for("postgresql://host/db") is None


def test_second_backend_on_the_same_database_is_refused(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "aether_studio.db.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time
                sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
                from pathlib import Path
                from app import instance_lock
                instance_lock.acquire(Path({str(lock)!r}), wait_seconds=0)
                print("locked", flush=True)
                time.sleep(30)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(instance_lock, "_HANDLE", None)
        with pytest.raises(instance_lock.DataDirInUse, match=f"pid {holder.pid}"):
            instance_lock.acquire(lock, wait_seconds=0.3)
    finally:
        holder.kill()
        holder.wait()
    # Once the first backend is gone the lock is free again.
    monkeypatch.setattr(instance_lock, "_HANDLE", None)
    instance_lock.acquire(lock, wait_seconds=2)
    assert instance_lock._HANDLE is not None
    instance_lock._HANDLE.close()
    monkeypatch.setattr(instance_lock, "_HANDLE", None)
