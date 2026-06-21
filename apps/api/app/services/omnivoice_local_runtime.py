from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .runtime_settings import LOCAL_OMNIVOICE_URL, RuntimeSettings
from .storage import ensure_storage


SOURCE_API_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = Path(os.getenv("AETHER_RUNTIME_DIR") or SOURCE_API_DIR.parents[1]).expanduser()
RUNTIME_DIR = PROJECT_DIR / "omnivoice"
LAUNCHER_PATH = RUNTIME_DIR / "omnivoice_local_service.py"
_process: subprocess.Popen[str] | None = None
_process_lock = threading.Lock()
_started_at: float | None = None
_runtime_id = ""
_device = ""
_python_path = ""
_last_error = ""
_validation_cache: dict[tuple[str, str, str], str | None] = {}


def clear_validation_cache() -> None:
    """Drop cached interpreter validations (call after (re)installing the venv)."""
    _validation_cache.clear()


def start_local_omnivoice(settings: RuntimeSettings) -> dict[str, Any]:
    global _process, _started_at, _runtime_id, _device, _python_path, _last_error
    runtime, device = _normalize_runtime(settings.omnivoice_runtime, settings.omnivoice_device)
    if runtime == "remote":
        raise ValueError("Colab/remote runtime cannot be started on this machine.")

    with _process_lock:
        current = _service_health()
        if current["reachable"]:
            return _status_payload(current, managed=_process is not None, runtime=runtime, device=device)

        if _process is not None and _process.poll() is None:
            return _status_payload(current, managed=True, runtime=_runtime_id, device=_device)

        _materialize_runtime_files()
        python_path, validation_error = _find_omnivoice_python(runtime, device)
        if not python_path:
            _last_error = validation_error
            return _setup_required_payload(runtime, device, validation_error)

        log_path = _log_path()
        command = [
            str(python_path),
            str(LAUNCHER_PATH),
            "--runtime",
            runtime,
            "--device",
            device,
            "--host",
            "127.0.0.1",
            "--port",
            "8008",
        ]
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting: {' '.join(command)}\n")
            log_file.flush()
            _process = subprocess.Popen(
                command,
                cwd=str(PROJECT_DIR),
                env=os.environ.copy(),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creation_flags,
            )

        _started_at = time.time()
        _runtime_id = settings.omnivoice_runtime
        _device = device
        _python_path = str(python_path)
        _last_error = ""
        return _status_payload(
            {"reachable": False, "status": "starting", "detail": None},
            managed=True,
            runtime=_runtime_id,
            device=device,
        )


def _materialize_runtime_files() -> None:
    """Copy executable OmniVoice helpers out of source/PyInstaller storage.

    PyInstaller one-file bundles are extracted to a temporary directory which
    disappears when the backend exits. Local model environments must therefore
    reference durable copies under the per-user runtime directory.
    """
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    sources = {
        SOURCE_API_DIR / "omnivoice_local_service.py": LAUNCHER_PATH,
        SOURCE_API_DIR / "app" / "assets" / "omnivoice_service.py": RUNTIME_DIR / "omnivoice_service.py",
    }
    for source, destination in sources.items():
        if not source.exists():
            raise RuntimeError(f"Bundled OmniVoice runtime asset is missing: {source.name}")
        if not destination.exists() or source.read_bytes() != destination.read_bytes():
            shutil.copy2(source, destination)


def stop_local_omnivoice() -> dict[str, Any]:
    global _process, _last_error
    with _process_lock:
        if _process is None or _process.poll() is not None:
            _process = None
            health = _service_health()
            if health["reachable"]:
                return {
                    **_status_payload(health, managed=False, runtime=_runtime_id, device=_device),
                    "message": "OmniVoice is running outside Aether and was not stopped.",
                }
            return _status_payload(health, managed=False, runtime=_runtime_id, device=_device)

        _process.terminate()
        try:
            _process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            _process.kill()
            _process.wait(timeout=5)
        _process = None
        _last_error = ""
        return _status_payload(
            {"reachable": False, "status": "stopped", "detail": None},
            managed=False,
            runtime=_runtime_id,
            device=_device,
        )


def local_omnivoice_status(settings: RuntimeSettings | None = None) -> dict[str, Any]:
    global _process, _last_error
    runtime_id = settings.omnivoice_runtime if settings else _runtime_id
    requested_device = settings.omnivoice_device if settings else _device
    runtime, device = _normalize_runtime(runtime_id or "auto", requested_device or "auto")
    if runtime == "remote":
        return {
            **_status_payload(
                {"reachable": False, "status": "remote", "detail": None},
                managed=False,
                runtime=runtime_id,
                device=device,
            ),
            "api_url": settings.omnivoice_api_url if settings else "",
        }

    with _process_lock:
        health = _service_health()
        managed = _process is not None and _process.poll() is None
        if health["reachable"]:
            return _status_payload(health, managed=managed, runtime=runtime_id, device=device)

        if _process is not None:
            return_code = _process.poll()
            if return_code is None:
                return _status_payload(
                    {"reachable": False, "status": "starting", "detail": None},
                    managed=True,
                    runtime=runtime_id,
                    device=device,
                )
            _last_error = f"OmniVoice exited with code {return_code}. {_tail_log()}"
            _process = None
            return _status_payload(
                {"reachable": False, "status": "error", "detail": _last_error},
                managed=False,
                runtime=runtime_id,
                device=device,
            )

        python_path, validation_error = _find_omnivoice_python(runtime, device)
        if not python_path:
            return _setup_required_payload(runtime_id, device, validation_error)
        return _status_payload(health, managed=False, runtime=runtime_id, device=device)


def inspect_local_omnivoice_environment() -> dict[str, Any]:
    if platform.system() == "Darwin":
        python_path, error = _find_omnivoice_python("mps", "mps")
    else:
        python_path, error = _find_omnivoice_python("gpu", "cuda:0")
    return {
        "ready": python_path is not None,
        "python_path": str(python_path or ""),
        "error": error,
    }


def _normalize_runtime(runtime_id: str, device: str) -> tuple[str, str]:
    if runtime_id == "colab":
        return "remote", "remote"
    if runtime_id == "local-cpu":
        return "cpu", "cpu"
    if runtime_id.startswith("local-gpu:"):
        index = runtime_id.split(":", 1)[1]
        return "gpu", f"cuda:{index}"
    if runtime_id == "local-mps":
        return "mps", "mps"
    if device.startswith("cuda"):
        return "gpu", device
    if device == "mps":
        return "mps", "mps"
    return "auto", "auto"


def _find_omnivoice_python(runtime: str, device: str) -> tuple[Path | None, str]:
    candidates: list[Path] = []
    configured = os.getenv("AETHER_OMNIVOICE_PYTHON", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    if os.name == "nt":
        candidates.append(PROJECT_DIR / ".venv-omnivoice" / "Scripts" / "python.exe")
        conda_prefix_value = os.getenv("CONDA_PREFIX", "").strip()
        if conda_prefix_value:
            conda_prefix = Path(conda_prefix_value).expanduser()
            candidates.extend(
                [
                    conda_prefix / "python.exe",
                    conda_prefix / "envs" / "omnivoice" / "python.exe",
                    conda_prefix.parent / "omnivoice" / "python.exe",
                ]
            )
        candidates.extend(
            [
                Path.home() / "anaconda3" / "envs" / "omnivoice" / "python.exe",
                Path.home() / "miniconda3" / "envs" / "omnivoice" / "python.exe",
            ]
        )
    else:
        candidates.append(PROJECT_DIR / ".venv-omnivoice" / "bin" / "python")
    candidates.append(Path(sys.executable))

    errors: list[str] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        validation = _validate_python(resolved, runtime, device)
        if validation is None:
            return resolved, ""
        errors.append(f"{resolved}: {validation}")

    if runtime == "mps" or (platform.system() == "Darwin" and runtime not in ("gpu", "cpu")):
        setup_command = (
            "Create .venv-omnivoice with MPS-enabled PyTorch: "
            "python3 -m venv .venv-omnivoice && "
            ".venv-omnivoice/bin/pip install torch torchvision torchaudio omnivoice[all]. "
            "See omnivoice/README_LOCAL.md."
        )
    else:
        setup_command = (
            "Create .venv-omnivoice and install the CUDA 12.8 PyTorch and OmniVoice dependencies. "
            "See omnivoice/README_LOCAL.md."
        )
    if configured:
        setup_command += f" Configured interpreter: {configured}."
    return None, f"{setup_command} {' | '.join(errors)}".strip()


def _validate_python(python_path: Path, runtime: str, device: str) -> str | None:
    cache_key = (str(python_path), runtime, device)
    if cache_key in _validation_cache:
        return _validation_cache[cache_key]
    require_cuda = runtime == "gpu" or (runtime == "auto" and device.startswith("cuda"))
    require_mps = runtime == "mps" or (runtime == "auto" and device == "mps")
    script = (
        "import torch, fastapi, uvicorn, numpy, requests, soundfile; from omnivoice import OmniVoice; "
        f"assert {require_cuda!r} is False or torch.cuda.is_available(), 'CUDA is unavailable'; "
        f"assert {require_mps!r} is False or (hasattr(torch.backends, 'mps') and torch.backends.mps.is_available()), 'MPS is unavailable'; "
        "print(torch.__version__)"
    )
    try:
        result = subprocess.run(
            [str(python_path), "-c", script],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        error = str(exc)
        _validation_cache[cache_key] = error
        return error
    if result.returncode == 0:
        _validation_cache[cache_key] = None
        return None
    error = (result.stderr or result.stdout or "dependency validation failed").strip().splitlines()[-1]
    _validation_cache[cache_key] = error
    return error


def _service_health() -> dict[str, Any]:
    try:
        response = httpx.get(f"{LOCAL_OMNIVOICE_URL}/health", timeout=1.5)
        if response.status_code >= 400:
            return {"reachable": False, "status": "error", "detail": f"HTTP {response.status_code}"}
        payload = response.json()
        return {
            "reachable": True,
            "status": str(payload.get("status") or "ready"),
            "detail": payload.get("model_error"),
            "model": payload.get("model"),
            "device": payload.get("device"),
        }
    except Exception:
        return {"reachable": False, "status": "stopped", "detail": None}


def _status_payload(
    health: dict[str, Any],
    *,
    managed: bool,
    runtime: str,
    device: str,
) -> dict[str, Any]:
    state = str(health.get("status") or "stopped")
    if health.get("reachable") and state == "not_loaded":
        state = "starting"
    return {
        "state": state,
        "reachable": bool(health.get("reachable")),
        "managed": managed,
        "runtime": runtime,
        "device": str(health.get("device") or device),
        "api_url": LOCAL_OMNIVOICE_URL,
        "pid": _process.pid if _process is not None and _process.poll() is None else None,
        "python_path": _python_path,
        "started_at": _started_at,
        "error": health.get("detail") or _last_error,
        "log_path": str(_log_path()),
        "model": health.get("model"),
    }


def _setup_required_payload(runtime: str, device: str, error: str) -> dict[str, Any]:
    return {
        **_status_payload(
            {"reachable": False, "status": "setup_required", "detail": error},
            managed=False,
            runtime=runtime,
            device=device,
        ),
        "setup_command": "py -3.12 -m venv .venv-omnivoice",
    }


def _log_path() -> Path:
    path = ensure_storage() / "logs" / "omnivoice-local.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _tail_log(max_chars: int = 1200) -> str:
    path = _log_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")[-max_chars:].strip()
