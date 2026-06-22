"""Automated provisioning for the local OmniVoice environment (.venv-omnivoice).

Creates a dedicated Python virtual environment and installs a hardware-aware
PyTorch build — CUDA 12.8 on NVIDIA GPUs, CPU otherwise, the default (MPS) wheel
on macOS — plus the OmniVoice runtime dependencies. The work runs in a daemon
thread and exposes a coarse-grained progress status that mirrors the Colab setup
flow so the desktop UI can poll it.

The frozen production backend cannot build a venv from its own interpreter, so a
real system Python is located via the ``py`` launcher / ``python3.12``.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .omnivoice_local_runtime import PROJECT_DIR, RUNTIME_DIR, SOURCE_API_DIR, clear_validation_cache
from .storage import ensure_storage


SOURCE_REQUIREMENTS_PATH = SOURCE_API_DIR / "app" / "assets" / "omnivoice-requirements.txt"
REQUIREMENTS_PATH = RUNTIME_DIR / "requirements.txt"
VENV_DIR = PROJECT_DIR / ".venv-omnivoice"
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

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
        if base_python is None:
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
        _log(f"\n[{_now()}] Starting OmniVoice setup (target={_target}, base={' '.join(base_python)})\n")
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


def _run_setup(base_python: list[str], target: str) -> None:
    global _state, _message, _error, _finished_at
    venv_python = _venv_python()
    try:
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
        candidates = [
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
