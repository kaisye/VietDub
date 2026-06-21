from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from typing import Any

from .omnivoice_local_runtime import inspect_local_omnivoice_environment


LOCAL_OMNIVOICE_URL = "http://127.0.0.1:8008"


def detect_omnivoice_runtime_options() -> dict[str, Any]:
    nvidia_gpus = _detect_nvidia_gpus()
    mps_gpu = _detect_apple_mps()
    torch_status = _detect_torch_cuda()

    all_gpus = nvidia_gpus + ([mps_gpu] if mps_gpu else [])

    nvidia_options: list[dict[str, Any]] = [
        {
            "id": f"local-gpu:{gpu['index']}",
            "kind": "local-gpu",
            "label": gpu["name"],
            "description": f"Local NVIDIA GPU, {gpu['memory_gb']:.1f} GB VRAM",
            "available": True,
            "device": f"cuda:{gpu['index']}",
            "api_url": LOCAL_OMNIVOICE_URL,
            "requires_url": False,
            "recommended": False,
            "warning": torch_status.get("warning", ""),
            "launch_args": ["--runtime", "gpu", "--device", f"cuda:{gpu['index']}"],
        }
        for gpu in nvidia_gpus
    ]

    mps_options: list[dict[str, Any]] = (
        [
            {
                "id": "local-mps",
                "kind": "local-gpu",
                "label": mps_gpu["name"],
                "description": (
                    f"Apple GPU (Metal MPS)"
                    + (f", {mps_gpu['memory_gb']:.0f} GB unified memory" if mps_gpu["memory_gb"] else "")
                ),
                "available": True,
                "device": "mps",
                "api_url": LOCAL_OMNIVOICE_URL,
                "requires_url": False,
                "recommended": True,
                "warning": "",
                "launch_args": ["--runtime", "mps", "--device", "mps"],
            }
        ]
        if mps_gpu
        else []
    )

    if nvidia_gpus:
        auto_desc = f"Prefer {nvidia_gpus[0]['name']} and fall back to CPU"
    elif mps_gpu:
        auto_desc = f"Prefer {mps_gpu['name']} (Metal) and fall back to CPU"
    else:
        auto_desc = "No GPU detected; use local CPU"

    recommended_id = "local-mps" if (mps_gpu and not nvidia_gpus) else "auto"
    options: list[dict[str, Any]] = [
        {
            "id": "auto",
            "kind": "auto",
            "label": "Auto detect",
            "description": auto_desc,
            "available": True,
            "device": "auto",
            "api_url": LOCAL_OMNIVOICE_URL,
            "requires_url": False,
            "recommended": not mps_gpu,
            "warning": torch_status.get("warning", ""),
            "launch_args": ["--runtime", "auto"],
        },
        *nvidia_options,
        *mps_options,
        {
            "id": "local-cpu",
            "kind": "local-cpu",
            "label": "Local CPU",
            "description": _cpu_description(),
            "available": True,
            "device": "cpu",
            "api_url": LOCAL_OMNIVOICE_URL,
            "requires_url": False,
            "recommended": not all_gpus,
            "warning": "OmniVoice on CPU is supported for fallback and testing, but synthesis will be slow.",
            "launch_args": ["--runtime", "cpu"],
        },
        {
            "id": "colab",
            "kind": "remote",
            "label": "Google Colab / remote GPU",
            "description": "Connect to an OmniVoice FastAPI URL running on Colab or another GPU machine.",
            "available": True,
            "device": "remote",
            "api_url": "",
            "requires_url": True,
            "recommended": False,
            "warning": "Cloudflare and ngrok URLs can change after the remote runtime restarts.",
            "launch_args": [],
        },
    ]
    return {
        "recommended_id": recommended_id,
        "local_api_url": LOCAL_OMNIVOICE_URL,
        "gpu_count": len(all_gpus),
        "gpus": all_gpus,
        "torch": torch_status,
        "options": options,
    }


def _detect_nvidia_gpus() -> list[dict[str, Any]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    command = [
        executable,
        "--query-gpu=index,name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return []

    gpus: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            index = int(parts[0])
            memory_gb = float(parts[2]) / 1024
        except ValueError:
            continue
        gpus.append(
            {
                "index": index,
                "name": parts[1],
                "memory_gb": round(memory_gb, 1),
                "driver_version": parts[3],
            }
        )
    return gpus


def _detect_apple_mps() -> dict[str, Any] | None:
    """Return Apple GPU info when running on macOS with MPS support, else None."""
    if platform.system() != "Darwin":
        return None
    # Prefer torch confirmation; fall back to arm64 heuristic
    try:
        import torch
        if not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
            return None
    except ImportError:
        if platform.machine() != "arm64":
            return None

    chip = "Apple Silicon GPU"
    try:
        r = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            chip = r.stdout.strip()
    except OSError:
        pass

    memory_gb = 0.0
    try:
        r = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if r.returncode == 0:
            memory_gb = round(int(r.stdout.strip()) / 1024 ** 3, 1)
    except (OSError, ValueError):
        pass

    return {
        "index": 0,
        "name": chip,
        "memory_gb": memory_gb,
        "driver_version": "Metal",
        "device": "mps",
    }


def _detect_torch_cuda() -> dict[str, Any]:
    try:
        import torch
    except ImportError:
        environment = inspect_local_omnivoice_environment()
        if environment["ready"]:
            return {
                "installed": True,
                "cuda_available": True,
                "version": "",
                "cuda_version": "",
                "environment_path": environment["python_path"],
                "warning": "",
            }
        return {
            "installed": False,
            "cuda_available": False,
            "version": "",
            "cuda_version": "",
            "environment_path": "",
            "warning": environment["error"],
        }

    cuda_available = bool(torch.cuda.is_available())
    return {
        "installed": True,
        "cuda_available": cuda_available,
        "version": str(torch.__version__),
        "cuda_version": str(torch.version.cuda or ""),
        "environment_path": sys.executable,
        "warning": "" if cuda_available else "This Python environment has PyTorch, but CUDA is not available.",
    }


def _cpu_description() -> str:
    cpu = platform.processor().strip() or os.getenv("PROCESSOR_IDENTIFIER", "").strip()
    return f"Local CPU: {cpu}" if cpu else "Local CPU fallback"
