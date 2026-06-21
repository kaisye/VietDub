from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SERVICE_DIR.parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "outputs"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _configure_environment(args: argparse.Namespace, device: str) -> None:
    if args.env_file:
        _load_env_file(Path(args.env_file).expanduser().resolve())

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    os.environ["OMNIVOICE_HOST"] = args.host
    os.environ["OMNIVOICE_PORT"] = str(args.port)
    os.environ["OMNIVOICE_DEVICE_MAP"] = device
    os.environ["OMNIVOICE_OUTPUT_DIR"] = str(output_dir)
    os.environ["OMNIVOICE_LOAD_ON_STARTUP"] = "1"
    os.environ["OMNIVOICE_LOAD_ASR"] = "1" if args.load_asr else "0"
    os.environ["OMNIVOICE_SHARE"] = "0"
    if args.api_key is not None:
        os.environ["OMNIVOICE_API_KEY"] = args.api_key


def _resolve_device(runtime: str, requested_device: str) -> tuple[str, str]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch is not installed. Install PyTorch in the dedicated OmniVoice environment."
        ) from exc

    if runtime == "cpu" or requested_device == "cpu":
        return "cpu", "Local CPU"

    if runtime == "auto" and not torch.cuda.is_available():
        return "cpu", "Local CPU fallback (CUDA unavailable)"

    if runtime == "gpu" and not torch.cuda.is_available():
        raise RuntimeError(
            "NVIDIA GPU was detected by the system, but this Python environment cannot use CUDA. "
            "Install a CUDA-enabled PyTorch build and verify torch.cuda.is_available() returns True."
        )

    device = "cuda:0" if requested_device == "auto" else requested_device
    if not device.startswith("cuda"):
        raise RuntimeError(f"GPU runtime requires a CUDA device, received: {device}")

    try:
        device_index = int(device.split(":", 1)[1]) if ":" in device else 0
        gpu_name = torch.cuda.get_device_name(device_index)
        memory_gb = torch.cuda.get_device_properties(device_index).total_memory / (1024**3)
    except (ValueError, IndexError, AssertionError) as exc:
        raise RuntimeError(f"Invalid or unavailable CUDA device: {device}") from exc

    return device, f"{gpu_name} ({memory_gb:.1f} GB)"


def _print_runtime_options() -> None:
    print("Available OmniVoice runtimes:")
    print("  auto       Prefer CUDA and fall back to CPU")
    gpus = _system_nvidia_gpus()
    if gpus:
        for gpu in gpus:
            print(f"  gpu        cuda:{gpu['index']} - {gpu['name']} ({gpu['memory_gb']:.1f} GB)")
    else:
        print("  gpu        No NVIDIA GPU detected by nvidia-smi")
    print("  cpu        Local CPU fallback (slow)")
    print("  colab      Configure the Colab URL in Aether Settings; it is not started by this launcher")

    try:
        import torch
    except ImportError:
        print("PyTorch: not installed in this Python environment")
        return
    print(f"PyTorch: {torch.__version__}, CUDA available: {torch.cuda.is_available()}")


def _system_nvidia_gpus() -> list[dict[str, object]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=index,name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    gpus: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            gpus.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "memory_gb": float(parts[2]) / 1024,
                }
            )
        except ValueError:
            continue
    return gpus


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run OmniVoice locally with automatic CPU/GPU selection.")
    parser.add_argument("--host", default=os.getenv("OMNIVOICE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OMNIVOICE_PORT", "8008")))
    parser.add_argument(
        "--runtime",
        choices=("auto", "gpu", "cpu"),
        default=os.getenv("OMNIVOICE_RUNTIME", "auto"),
        help="Execution runtime. Auto prefers an NVIDIA GPU and falls back to CPU.",
    )
    parser.add_argument("--device", default=os.getenv("OMNIVOICE_DEVICE_MAP", "auto"))
    parser.add_argument("--output-dir", default=os.getenv("OMNIVOICE_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--env-file", default=str(SERVICE_DIR / ".env.local"))
    parser.add_argument(
        "--load-asr",
        action="store_true",
        default=os.getenv("OMNIVOICE_LOAD_ASR", "0").strip().lower() in {"1", "true", "yes", "on"},
        help="Load OmniVoice ASR components as well as TTS.",
    )
    parser.add_argument("--allow-cpu", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--list-devices", action="store_true", help="Print detected runtime options and exit.")
    return parser


def main() -> None:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=str(SERVICE_DIR / ".env.local"))
    pre_args, _unknown = pre_parser.parse_known_args()
    _load_env_file(Path(pre_args.env_file).expanduser().resolve())

    args = _build_parser().parse_args()
    if args.list_devices:
        _print_runtime_options()
        return
    if args.allow_cpu and args.runtime == "auto":
        args.runtime = "cpu"
    device, runtime_label = _resolve_device(args.runtime, args.device)
    _configure_environment(args, device)
    print(f"Runtime selected: {runtime_label}, device={device}")

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn is not installed. Run: pip install -r omnivoice/requirements.txt") from exc

    # Import after local environment defaults are set because the shared service
    # reads its model, device and storage configuration at module import time.
    from omnivoice_service import app

    local_url = f"http://127.0.0.1:{args.port}"
    print(f"Starting local OmniVoice service at {local_url}")
    print("Aether configuration:")
    print("  AETHER_TTS_PROVIDER=omnivoice")
    print(f"  OMNIVOICE_API_URL={local_url}")
    print("  OMNIVOICE_TRANSPORT=fastapi")
    print("  AETHER_OMNIVOICE_USE_SRT_ENDPOINT=1")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
