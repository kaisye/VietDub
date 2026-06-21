"""Resolve which TTS runtime should serve a job.

The simplified desktop app prioritises a local NVIDIA GPU running OmniVoice, then
a configured Colab/remote OmniVoice runtime, and finally the always-available
Edge default voice. Detection callables are injectable so the resolver stays
unit-testable without real hardware or network access.
"""

from __future__ import annotations

from typing import Callable

from .runtime_settings import RuntimeSettings, get_runtime_settings


RUNTIME_OMNIVOICE_LOCAL = "omnivoice_local"
RUNTIME_OMNIVOICE_COLAB = "omnivoice_colab"
RUNTIME_EDGE = "edge"
RUNTIME_VIENEU = "vieneu"


def _local_gpu_available() -> bool:
    from .runtime_hardware import detect_omnivoice_runtime_options

    info = detect_omnivoice_runtime_options()
    if info.get("gpu_count"):
        return True
    torch_status = info.get("torch") or {}
    return bool(torch_status.get("cuda_available"))


def _local_omnivoice_ready(settings: RuntimeSettings) -> bool:
    from .omnivoice_local_runtime import local_omnivoice_status

    try:
        return bool(local_omnivoice_status(settings).get("reachable"))
    except Exception:
        return False


def _colab_omnivoice_ready() -> bool:
    from .omnivoice_colab_runtime import get_colab_runtime_status

    try:
        return bool(get_colab_runtime_status().get("reachable"))
    except Exception:
        return False


def resolve_effective_tts_runtime(
    settings: RuntimeSettings | None = None,
    *,
    gpu_available: Callable[[], bool] | None = None,
    local_ready: Callable[[RuntimeSettings], bool] | None = None,
    colab_ready: Callable[[], bool] | None = None,
) -> str:
    """Return one of ``omnivoice_local`` / ``omnivoice_colab`` / ``edge``.

    Order honours ``prefer_local_gpu``: when set (the default), a reachable local
    GPU OmniVoice wins, otherwise a reachable Colab runtime is tried first. If no
    OmniVoice runtime is available the default Edge voice is used.
    """
    settings = settings or get_runtime_settings()

    # Non-OmniVoice providers don't probe GPU/Colab: VieNeu runs offline on CPU,
    # Edge is the always-available cloud default.
    if settings.tts_provider == "vieneu":
        return RUNTIME_VIENEU
    if settings.tts_provider != "omnivoice":
        return RUNTIME_EDGE

    gpu_available = gpu_available or _local_gpu_available
    local_ready = local_ready or _local_omnivoice_ready
    colab_ready = colab_ready or _colab_omnivoice_ready

    def local_ok() -> bool:
        return gpu_available() and local_ready(settings)

    candidates: list[tuple[str, Callable[[], bool]]] = (
        [(RUNTIME_OMNIVOICE_LOCAL, local_ok), (RUNTIME_OMNIVOICE_COLAB, colab_ready)]
        if settings.prefer_local_gpu
        else [(RUNTIME_OMNIVOICE_COLAB, colab_ready), (RUNTIME_OMNIVOICE_LOCAL, local_ok)]
    )
    for runtime, is_ready in candidates:
        try:
            if is_ready():
                return runtime
        except Exception:
            continue
    return RUNTIME_EDGE
