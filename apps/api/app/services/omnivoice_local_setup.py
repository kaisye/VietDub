"""Automated provisioning for the local OmniVoice environment (.venv-omnivoice).

Creates a dedicated Python virtual environment and installs a hardware-aware
PyTorch build — CUDA 12.8 on NVIDIA GPUs, CPU otherwise, the default (MPS) wheel
on macOS — plus the OmniVoice runtime dependencies. The work runs in a daemon
thread and exposes a coarse-grained progress status that mirrors the Colab setup
flow so the desktop UI can poll it.

The frozen production backend cannot build a venv from its own interpreter. A
system Python is reused when available; Apple Silicon otherwise receives a
pinned, checksum-verified standalone Python runtime under app-data.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .omnivoice_local_runtime import PROJECT_DIR, RUNTIME_DIR, SOURCE_API_DIR, clear_validation_cache
from .storage import ensure_storage


SOURCE_REQUIREMENTS_PATH = SOURCE_API_DIR / "app" / "assets" / "omnivoice-requirements.txt"
REQUIREMENTS_PATH = RUNTIME_DIR / "requirements.txt"
VENV_DIR = PROJECT_DIR / ".venv-omnivoice"
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
MANAGED_PYTHON_VERSION = "3.12.13"
MANAGED_PYTHON_BUILD = "20260610"
MANAGED_PYTHON_ARCHIVE_NAME = (
    f"cpython-{MANAGED_PYTHON_VERSION}+{MANAGED_PYTHON_BUILD}"
    "-aarch64-apple-darwin-install_only.tar.gz"
)
MANAGED_PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{MANAGED_PYTHON_BUILD}/cpython-{MANAGED_PYTHON_VERSION}%2B{MANAGED_PYTHON_BUILD}"
    "-aarch64-apple-darwin-install_only.tar.gz"
)
MANAGED_PYTHON_SHA256 = "e18ddd4c1e8f4a1d6c4590b37f423d76aec734447edc20ed08e93983d95f2132"
MANAGED_PYTHON_DIR = RUNTIME_DIR / f"python-{MANAGED_PYTHON_VERSION}-macos-arm64"
MANAGED_PYTHON_PATH = MANAGED_PYTHON_DIR / "python" / "bin" / "python3"

_lock = threading.Lock()
_thread: threading.Thread | None = None
_state = "idle"
_message = ""
_error = ""
_target = ""
_started_at: float | None = None
_finished_at: float | None = None
_installation_checked = False


def local_omnivoice_setup_status() -> dict[str, Any]:
    global _installation_checked, _state, _message, _error, _target, _finished_at
    with _lock:
        if _state != "idle" or _installation_checked:
            return _status_locked()
        _installation_checked = True

    target = _select_torch_target()
    venv_python = _venv_python()
    verification_error = ""
    if venv_python.exists():
        try:
            _verify(venv_python, target)
        except Exception as exc:  # Existing environments may be partial or stale.
            verification_error = str(exc)

    with _lock:
        # Setup may start while the existing environment is being checked.
        if _state == "idle":
            _target = target
            if venv_python.exists() and not verification_error:
                _state = "ready"
                _message = "OmniVoice environment is ready."
                _error = ""
                _finished_at = time.time()
            elif verification_error:
                _error = verification_error
        return _status_locked()


def start_local_omnivoice_setup() -> dict[str, Any]:
    """Begin provisioning .venv-omnivoice in a background thread (idempotent)."""
    global _thread, _state, _message, _error, _target, _started_at, _finished_at
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _status_locked()

        _materialize_requirements()
        base_python, detect_error = _find_base_python()
        install_managed_python = base_python is None and _managed_python_supported()
        if base_python is None and not install_managed_python:
            _state = "error"
            _error = detect_error
            _message = ""
            return _status_locked()

        _target = _select_torch_target()
        _state = "creating_venv"
        _message = "Preparing OmniVoice environment…"
        _error = ""
        _started_at = time.time()
        _finished_at = None
        base_label = "managed Python download" if install_managed_python else " ".join(base_python or [])
        _log(f"\n[{_now()}] Starting OmniVoice setup (target={_target}, base={base_label})\n")
        _thread = threading.Thread(target=_run_setup, args=(base_python, _target), daemon=True)
        _thread.start()
        return _status_locked()


def _status_locked() -> dict[str, Any]:
    busy = _thread is not None and _thread.is_alive()
    return {
        "state": _state,
        "busy": busy,
        "message": _message,
        "error": _error,
        "target": _target,
        "venv_path": str(VENV_DIR),
        "started_at": _started_at,
        "finished_at": _finished_at,
        "log_tail": _tail_log(),
    }


def _set_state(state: str, message: str) -> None:
    global _state, _message
    with _lock:
        _state = state
        _message = message
    _log(f"[{_now()}] {state}: {message}\n")


def _run_setup(base_python: list[str] | None, target: str) -> None:
    global _state, _message, _error, _finished_at
    venv_python = _venv_python()
    try:
        if base_python is None:
            base_python = _ensure_managed_python()
        if not venv_python.exists():
            _set_state("creating_venv", "Creating .venv-omnivoice…")
            _run(base_python + ["-m", "venv", str(VENV_DIR)])
        _run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])

        _set_state(
            "installing_torch",
            f"Installing PyTorch ({target}) — this can take several minutes…",
        )
        torch_cmd = [str(venv_python), "-m", "pip", "install", "torch", "torchaudio"]
        if target == "cu128":
            torch_cmd += ["--index-url", TORCH_CUDA_INDEX]
        elif target == "cpu":
            torch_cmd += ["--index-url", TORCH_CPU_INDEX]
        # "mps" uses the default PyPI index.
        _run(torch_cmd)

        _set_state("installing_deps", "Installing OmniVoice dependencies…")
        _run([str(venv_python), "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)])

        _set_state("verifying", "Verifying installation…")
        _verify(venv_python, target)

        clear_validation_cache()
        with _lock:
            _state = "ready"
            _message = "OmniVoice environment is ready."
            _error = ""
            _finished_at = time.time()
        _log(f"[{_now()}] ready\n")
    except Exception as exc:  # noqa: BLE001 - any failure is surfaced to the UI
        with _lock:
            _state = "error"
            _error = str(exc)
            _finished_at = time.time()
        _log(f"[{_now()}] error: {exc}\n")


def _run(cmd: list[str]) -> None:
    _log(f"[{_now()}] $ {' '.join(cmd)}\n")
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with _log_path().open("a", encoding="utf-8") as log_file:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creation_flags,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}. See setup log for details."
        )


def _verify(venv_python: Path, target: str) -> None:
    require_cuda = target == "cu128"
    script = (
        "import torch; "
        f"assert {require_cuda!r} is False or torch.cuda.is_available(), 'CUDA not available after install'; "
        "from omnivoice import OmniVoice; print('ok', torch.__version__)"
    )
    result = subprocess.run(
        [str(venv_python), "-c", script],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    _log((result.stdout or "") + (result.stderr or "") + "\n")
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "verification failed").strip().splitlines()
        raise RuntimeError(message[-1] if message else "verification failed")


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _materialize_requirements() -> None:
    if not SOURCE_REQUIREMENTS_PATH.exists():
        raise RuntimeError("Bundled OmniVoice requirements are missing.")
    REQUIREMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    source = SOURCE_REQUIREMENTS_PATH.read_bytes()
    if not REQUIREMENTS_PATH.exists() or REQUIREMENTS_PATH.read_bytes() != source:
        REQUIREMENTS_PATH.write_bytes(source)


def _find_base_python() -> tuple[list[str] | None, str]:
    """Locate a system Python (>=3.10) able to build a venv. ``None`` if absent."""
    system = platform.system()
    if system == "Darwin":
        # Apps opened from Finder do not inherit the shell PATH. Check the
        # standard python.org and Homebrew locations before command names.
        candidates = [[str(MANAGED_PYTHON_PATH)]] + [
            [f"/Library/Frameworks/Python.framework/Versions/{version}/bin/python{version}"]
            for version in ("3.12", "3.11", "3.10")
        ]
        candidates += [
            [f"{prefix}/python{version}"]
            for prefix in ("/opt/homebrew/bin", "/usr/local/bin")
            for version in ("3.12", "3.11", "3.10")
        ]
        candidates += [
            ["/opt/homebrew/bin/python3"],
            ["/usr/local/bin/python3"],
            ["python3.12"],
            ["python3"],
            ["python"],
        ]
    elif os.name == "nt":
        candidates = [["py", "-3.12"], ["py", "-3"], ["python"]]
    else:
        candidates = [["python3.12"], ["python3"], ["python"]]
    # The running interpreter is only usable when it's a real Python — never the
    # PyInstaller-frozen backend exe.
    if not getattr(sys, "frozen", False):
        candidates.append([sys.executable])

    errors: list[str] = []
    for cmd in candidates:
        version = _python_version(cmd)
        if version is None:
            errors.append(f"{' '.join(cmd)}: not found")
            continue
        if version < (3, 10):
            errors.append(f"{' '.join(cmd)}: Python {version[0]}.{version[1]} too old (need 3.10+)")
            continue
        return cmd, ""

    if system == "Darwin":
        hint = (
            "No suitable Python found. Install Python 3.12 from "
            "https://www.python.org/downloads/macos/, finish the installer, "
            "reopen VietDub, then retry."
        )
    elif os.name == "nt":
        hint = (
            "No suitable Python found. Install Python 3.12 from python.org "
            "(tick 'Add python.exe to PATH' / enable the py launcher), then retry."
        )
    else:
        hint = "No suitable Python found. Install Python 3.12, then retry."
    return None, f"{hint} [{' | '.join(errors)}]"


def _managed_python_supported() -> bool:
    return platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}


def _ensure_managed_python() -> list[str]:
    existing_version = _python_version([str(MANAGED_PYTHON_PATH)])
    if existing_version is not None and existing_version >= (3, 10):
        return [str(MANAGED_PYTHON_PATH)]

    _set_state(
        "installing_python",
        f"Downloading Python {MANAGED_PYTHON_VERSION} for Apple Silicon (about 25 MB)…",
    )
    downloads_dir = RUNTIME_DIR / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    archive = downloads_dir / MANAGED_PYTHON_ARCHIVE_NAME
    if not archive.exists() or _sha256(archive) != MANAGED_PYTHON_SHA256:
        archive.unlink(missing_ok=True)
        _download_managed_python_archive(archive)

    actual_hash = _sha256(archive)
    if actual_hash != MANAGED_PYTHON_SHA256:
        archive.unlink(missing_ok=True)
        raise RuntimeError(
            "Managed Python checksum mismatch. "
            f"Expected {MANAGED_PYTHON_SHA256}, got {actual_hash}."
        )

    staging = MANAGED_PYTHON_DIR.with_name(f"{MANAGED_PYTHON_DIR.name}.installing")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            bundle.extractall(staging, filter="data")
        staged_python = staging / "python" / "bin" / "python3"
        if not staged_python.exists():
            raise RuntimeError("Managed Python archive does not contain python/bin/python3.")
        # filter="data" strips setuid/setgid but can also drop exec bits from
        # scripts inside bin/. Re-apply exec permission to every file under
        # bin/ so pip and other entry-points are runnable.
        bin_dir = staging / "python" / "bin"
        for entry in bin_dir.iterdir():
            if entry.is_file():
                entry.chmod(entry.stat().st_mode | 0o111)
        shutil.rmtree(MANAGED_PYTHON_DIR, ignore_errors=True)
        staging.replace(MANAGED_PYTHON_DIR)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    installed_version = _python_version([str(MANAGED_PYTHON_PATH)])
    if installed_version is None or installed_version < (3, 10):
        shutil.rmtree(MANAGED_PYTHON_DIR, ignore_errors=True)
        raise RuntimeError("Managed Python was extracted but could not be started.")
    _log(f"[{_now()}] Managed Python {installed_version[0]}.{installed_version[1]} ready.\n")
    return [str(MANAGED_PYTHON_PATH)]


def _download_managed_python_archive(destination: Path) -> None:
    temporary = destination.with_suffix(f"{destination.suffix}.download")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    downloaded = 0
    last_reported = -10
    try:
        with httpx.stream(
            "GET",
            MANAGED_PYTHON_URL,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=300.0),
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        progress = min(100, int(downloaded * 100 / total))
                        if progress >= last_reported + 10:
                            last_reported = progress
                            _set_state(
                                "installing_python",
                                f"Downloading Python {MANAGED_PYTHON_VERSION}… {progress}%",
                            )
        actual_hash = digest.hexdigest()
        if actual_hash != MANAGED_PYTHON_SHA256:
            raise RuntimeError(
                "Managed Python download checksum mismatch. "
                f"Expected {MANAGED_PYTHON_SHA256}, got {actual_hash}."
            )
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _python_version(cmd: list[str]) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            cmd + ["-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        major, minor = result.stdout.strip().split(".")[:2]
        return int(major), int(minor)
    except ValueError:
        return None


def _select_torch_target() -> str:
    if platform.system() == "Darwin":
        return "mps"
    try:
        from .runtime_hardware import _detect_nvidia_gpus

        if _detect_nvidia_gpus():
            return "cu128"
    except Exception:
        pass
    return "cpu"


def _log_path() -> Path:
    path = ensure_storage() / "logs" / "omnivoice-setup.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _log(text: str) -> None:
    try:
        with _log_path().open("a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass


def _tail_log(max_chars: int = 2000) -> str:
    path = _log_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")[-max_chars:].strip()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
