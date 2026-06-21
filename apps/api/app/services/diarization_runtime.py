from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from .runtime_settings import LOCAL_DIARIZATION_URL, RuntimeSettings
from .storage import ensure_storage


_process: subprocess.Popen | None = None
_started_at: float | None = None
_last_error: str | None = None


def local_diarization_status(settings: RuntimeSettings) -> dict[str, Any]:
    health = _health(settings.diarization_api_url)
    health_status = str((health or {}).get("status") or "")
    state = health_status if health_status in {"ready", "loading", "error", "not_loaded"} else "stopped"
    if _process and _process.poll() is None and not health:
        state = "starting"
    if _last_error and not health:
        state = "error"
    python_path = _conda_python_path()
    if not python_path.exists() and not health:
        state = "setup_required"
    return {
        "state": state,
        "reachable": bool(health),
        "managed": bool(_process and _process.poll() is None),
        "device": str((health or {}).get("device") or settings.diarization_device),
        "api_url": settings.diarization_api_url or LOCAL_DIARIZATION_URL,
        "pid": _process.pid if _process and _process.poll() is None else None,
        "python_path": str(python_path),
        "started_at": _started_at,
        "error": _last_error or (health or {}).get("model_error"),
        "log_path": str(_log_path()),
        "model": (health or {}).get("model"),
        "setup_command": "powershell -ExecutionPolicy Bypass -File diarization/setup_diarization.ps1",
        "message": "Accept the Hugging Face model terms before starting." if not settings.huggingface_token else None,
    }


def start_local_diarization(settings: RuntimeSettings) -> dict[str, Any]:
    global _process, _started_at, _last_error
    if _health(settings.diarization_api_url):
        return local_diarization_status(settings)
    python_path = _conda_python_path()
    if not python_path.exists():
        _last_error = "The aether-diarization Conda environment is not installed."
        return local_diarization_status(settings)
    if not settings.huggingface_token:
        _last_error = "HF_TOKEN is required before the diarization model can be loaded."
        return local_diarization_status(settings)
    if _process and _process.poll() is None:
        return local_diarization_status(settings)

    root = Path(__file__).resolve().parents[4]
    service = root / "diarization" / "diarization_service.py"
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    env = os.environ.copy()
    env["HF_TOKEN"] = settings.huggingface_token
    env["AETHER_DIARIZATION_DEVICE"] = settings.diarization_device or "auto"
    _last_error = None
    try:
        _process = subprocess.Popen(
            [str(python_path), str(service)],
            cwd=str(root),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _started_at = time.time()
    except Exception as exc:
        _last_error = str(exc)
    return local_diarization_status(settings)


def stop_local_diarization() -> dict[str, Any]:
    global _process, _last_error
    if _process and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None
    _last_error = None
    from .runtime_settings import get_runtime_settings

    return local_diarization_status(get_runtime_settings())


def _health(base_url: str) -> dict[str, Any] | None:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=2)
        if response.status_code < 400:
            data = response.json()
            return data if isinstance(data, dict) else None
    except Exception:
        return None
    return None


def _conda_python_path() -> Path:
    configured = os.getenv("AETHER_DIARIZATION_PYTHON", "").strip()
    if configured:
        return Path(configured).expanduser()
    conda_exe = os.getenv("CONDA_EXE", "").strip()
    if conda_exe:
        base = Path(conda_exe).resolve().parent.parent
    else:
        conda_prefix = Path(os.getenv("CONDA_PREFIX", Path.home() / "anaconda3"))
        base = conda_prefix.parent.parent if conda_prefix.parent.name.lower() == "envs" else conda_prefix
    return base / "envs" / "aether-diarization" / "python.exe"


def _log_path() -> Path:
    return ensure_storage() / "logs" / "diarization-runtime.log"
