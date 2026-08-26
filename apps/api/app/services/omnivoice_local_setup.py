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
from typing import Any, NamedTuple

import httpx

from .omnivoice_local_runtime import PROJECT_DIR, RUNTIME_DIR, SOURCE_API_DIR, clear_validation_cache
from .storage import ensure_storage


SOURCE_REQUIREMENTS_PATH = SOURCE_API_DIR / "app" / "assets" / "omnivoice-requirements.txt"
REQUIREMENTS_PATH = RUNTIME_DIR / "requirements.txt"
VENV_DIR = PROJECT_DIR / ".venv-omnivoice"
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_CUDA_LEGACY_INDEX = "https://download.pytorch.org/whl/cu126"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

# PyTorch's cu128 wheels are built for sm_75 and up -- sm_50..sm_70 were dropped
# because CUDA 12.8 deprecates them. The cu126 wheels still cover sm_50..sm_90.
# Installing cu128 on an older card (a GTX 950M is sm_50, a GTX 1060 sm_61)
# downloads ~2.5 GB that then fails every kernel launch with "no kernel image is
# available for execution on the device" -- and torch.cuda.is_available() keeps
# returning True, so nothing catches it until the first synthesis.
CUDA_MODERN_MIN_CAPABILITY = (7, 5)
CUDA_LEGACY_MIN_CAPABILITY = (5, 0)
MANAGED_PYTHON_VERSION = "3.12.13"
MANAGED_PYTHON_BUILD = "20260610"


class ManagedPython(NamedTuple):
    """A python-build-standalone target: what to fetch and where python lands."""

    triple: str
    sha256: str
    relative_exe: tuple[str, ...]
    approximate_mb: int
    label: str


# A machine with no system Python gets a pinned, checksum-verified standalone
# CPython instead of a dead end telling the user to go install one by hand.
# Windows needs this at least as much as macOS: a fresh Windows box has neither
# the py launcher nor python on PATH, and the store alias is a stub.
MANAGED_PYTHON_TARGETS: dict[str, ManagedPython] = {
    "macos-arm64": ManagedPython(
        triple="aarch64-apple-darwin",
        sha256="e18ddd4c1e8f4a1d6c4590b37f423d76aec734447edc20ed08e93983d95f2132",
        relative_exe=("python", "bin", "python3"),
        approximate_mb=25,
        label="Apple Silicon",
    ),
    "windows-x86_64": ManagedPython(
        triple="x86_64-pc-windows-msvc",
        sha256="f5e4d9f856567493776f3d1e832c939fbaba5dcbcc5e0492a82ecfceea83b316",
        relative_exe=("python", "python.exe"),
        approximate_mb=46,
        label="Windows",
    ),
}

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
        elif target == "cu126":
            torch_cmd += ["--index-url", TORCH_CUDA_LEGACY_INDEX]
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
    require_cuda = target in {"cu128", "cu126"}
    # torch.cuda.is_available() only proves the driver loaded -- a wheel with no
    # SASS for this card still passes it and fails at the first kernel launch.
    # Compare the card against torch's own arch list and run one real kernel so
    # the mismatch surfaces here, named, instead of mid-synthesis.
    script = "\n".join(
        [
            "import torch",
            f"if {require_cuda!r}:",
            "    assert torch.cuda.is_available(), 'CUDA not available after install'",
            "    major, minor = torch.cuda.get_device_capability()",
            "    arches = torch.cuda.get_arch_list()",
            "    if f'sm_{major}{minor}' not in arches:",
            "        raise SystemExit(",
            "            f'This PyTorch build has no kernels for sm_{major}{minor} '",
            "            f'({torch.cuda.get_device_name(0)}); it supports ' + ', '.join(arches)",
            "        )",
            "    (torch.zeros(1, device='cuda') + 1).cpu()",
            "from omnivoice import OmniVoice",
            "print('ok', torch.__version__)",
        ]
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
        # Apps launched from a packaged build / Start menu don't inherit a shell
        # PATH and often have neither the py launcher nor `python` on PATH. Probe
        # the common per-user and machine-wide install locations (python.org
        # installer + Anaconda/Miniconda) before falling back to bare names.
        candidates = [["py", "-3.12"], ["py", "-3.11"], ["py", "-3.10"], ["py", "-3"]]
        localappdata = os.environ.get("LOCALAPPDATA", "")
        userprofile = os.environ.get("USERPROFILE", "")
        programdata = os.environ.get("ProgramData", r"C:\ProgramData")
        for version in ("Python312", "Python311", "Python310", "Python313"):
            if localappdata:
                candidates.append(
                    [os.path.join(localappdata, "Programs", "Python", version, "python.exe")]
                )
        # python.org "install for all users" lands outside LOCALAPPDATA.
        programfiles = os.environ.get("ProgramFiles", r"C:\Program Files")
        for version in ("Python312", "Python311", "Python310", "Python313"):
            candidates.append([os.path.join(programfiles, version, "python.exe")])
            candidates.append([os.path.join("C:\\", version, "python.exe")])
        for base in (userprofile, programdata):
            if base:
                candidates.append([os.path.join(base, "anaconda3", "python.exe")])
                candidates.append([os.path.join(base, "miniconda3", "python.exe")])
        candidates.append(["python"])
    else:
        candidates = [["python3.12"], ["python3"], ["python"]]

    # A managed Python downloaded by an earlier run is reused before anything
    # else, on every platform that has one.
    managed_key = _managed_python_key()
    if managed_key is not None:
        candidates.insert(0, [str(_managed_python_path(managed_key))])
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


def _managed_python_key() -> str | None:
    """Key into MANAGED_PYTHON_TARGETS for this machine, or None if unsupported."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        return "windows-x86_64"
    return None


def _managed_python_dir(key: str) -> Path:
    return RUNTIME_DIR / f"python-{MANAGED_PYTHON_VERSION}-{key}"


def _managed_python_path(key: str) -> Path:
    return _managed_python_dir(key).joinpath(*MANAGED_PYTHON_TARGETS[key].relative_exe)


def _managed_python_archive_name(target: ManagedPython) -> str:
    return (
        f"cpython-{MANAGED_PYTHON_VERSION}+{MANAGED_PYTHON_BUILD}"
        f"-{target.triple}-install_only.tar.gz"
    )


def _managed_python_url(target: ManagedPython) -> str:
    return (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{MANAGED_PYTHON_BUILD}/cpython-{MANAGED_PYTHON_VERSION}%2B{MANAGED_PYTHON_BUILD}"
        f"-{target.triple}-install_only.tar.gz"
    )


def _managed_python_supported() -> bool:
    return _managed_python_key() is not None


def _ensure_managed_python() -> list[str]:
    key = _managed_python_key()
    if key is None:
        raise RuntimeError("No managed Python build is available for this platform.")
    target = MANAGED_PYTHON_TARGETS[key]
    install_dir = _managed_python_dir(key)
    python_path = _managed_python_path(key)

    existing_version = _python_version([str(python_path)])
    if existing_version is not None and existing_version >= (3, 10):
        return [str(python_path)]

    _set_state(
        "installing_python",
        f"Downloading Python {MANAGED_PYTHON_VERSION} for {target.label} "
        f"(about {target.approximate_mb} MB)…",
    )
    downloads_dir = RUNTIME_DIR / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    archive = downloads_dir / _managed_python_archive_name(target)
    if not archive.exists() or _sha256(archive) != target.sha256:
        archive.unlink(missing_ok=True)
        _download_managed_python_archive(archive, target)

    actual_hash = _sha256(archive)
    if actual_hash != target.sha256:
        archive.unlink(missing_ok=True)
        raise RuntimeError(
            f"Managed Python checksum mismatch. Expected {target.sha256}, got {actual_hash}."
        )

    staging = install_dir.with_name(f"{install_dir.name}.installing")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            bundle.extractall(staging, filter="data")
        staged_python = staging.joinpath(*target.relative_exe)
        if not staged_python.exists():
            expected = "/".join(target.relative_exe)
            raise RuntimeError(f"Managed Python archive does not contain {expected}.")
        bin_dir = staging / "python" / "bin"
        if bin_dir.is_dir():
            # filter="data" strips setuid/setgid but can also drop exec bits from
            # scripts inside bin/. Re-apply exec permission to every file under
            # bin/ so pip and other entry-points are runnable. Windows builds put
            # everything in python/ and Scripts/ and have no exec bit at all.
            for entry in bin_dir.iterdir():
                if entry.is_file():
                    entry.chmod(entry.stat().st_mode | 0o111)
        shutil.rmtree(install_dir, ignore_errors=True)
        staging.replace(install_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    installed_version = _python_version([str(python_path)])
    if installed_version is None or installed_version < (3, 10):
        shutil.rmtree(install_dir, ignore_errors=True)
        raise RuntimeError("Managed Python was extracted but could not be started.")
    _log(f"[{_now()}] Managed Python {installed_version[0]}.{installed_version[1]} ready.\n")
    return [str(python_path)]


def _download_managed_python_archive(destination: Path, target: ManagedPython) -> None:
    temporary = destination.with_suffix(f"{destination.suffix}.download")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    downloaded = 0
    last_reported = -10
    try:
        with httpx.stream(
            "GET",
            _managed_python_url(target),
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
        if actual_hash != target.sha256:
            raise RuntimeError(
                "Managed Python download checksum mismatch. "
                f"Expected {target.sha256}, got {actual_hash}."
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

        gpus = _detect_nvidia_gpus()
    except Exception:
        return "cpu"
    if not gpus:
        return "cpu"

    capabilities = [
        capability
        for capability in (_compute_capability(gpu) for gpu in gpus)
        if capability is not None
    ]
    if not capabilities:
        # Driver too old to report compute_cap. Keep the modern wheel rather than
        # downgrading every card we simply could not measure; _verify catches a
        # mismatch before the environment is marked ready.
        return "cu128"
    best = max(capabilities)
    if best >= CUDA_MODERN_MIN_CAPABILITY:
        return "cu128"
    if best >= CUDA_LEGACY_MIN_CAPABILITY:
        return "cu126"
    return "cpu"


def _compute_capability(gpu: dict[str, Any]) -> tuple[int, int] | None:
    raw = gpu.get("compute_capability")
    if not isinstance(raw, str):
        return None
    major, _, minor = raw.partition(".")
    if not major.isdigit() or not minor.isdigit():
        return None
    return int(major), int(minor)


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
