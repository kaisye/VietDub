"""Live, human-friendly sub-status for the active pipeline step.

The pipeline already exposes coarse status (status / progress / current_step) via
the job record. But the *interesting* detail — "is the voice actually being
synthesised, or is the runtime reconnecting?" — happens deep inside the service
layer (tts.py recovery, Colab restart) and was previously only visible in the
Python logs. This module is a tiny, dependency-free side channel:

* the worker binds the current job id to the thread (``bind_job``),
* any service running in that thread publishes a short message (``report_progress``),
* the API reads the latest message (``get_progress_detail``) and returns it
  alongside the job so the UI can show a friendly live line — no terminal logs.

The store is in-memory and per-process. The worker runs in the same process as
the API (a daemon thread), so no database column or IPC is needed; the detail is
ephemeral live status and is fine to lose on restart.
"""

from __future__ import annotations

import contextvars
import threading
import time

_current_job_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aether_progress_job_id", default=""
)

_lock = threading.Lock()
_details: dict[str, tuple[str, float]] = {}


def bind_job(job_id: str) -> None:
    """Associate the calling thread with ``job_id`` for subsequent reports."""
    _current_job_id.set(job_id or "")


def report_progress(detail: str) -> None:
    """Publish a short, display-ready status line for the bound job.

    Safe to call from anywhere: if no job is bound to the current thread (e.g. a
    request handler or a test), it is a no-op.
    """
    job_id = _current_job_id.get()
    if not job_id:
        return
    with _lock:
        _details[job_id] = (detail or "", time.time())


def get_progress_detail(job_id: str) -> str:
    """Return the latest published detail for ``job_id`` (empty if none)."""
    with _lock:
        entry = _details.get(job_id)
    return entry[0] if entry else ""


def clear_progress(job_id: str) -> None:
    """Drop any stored detail for ``job_id`` (call on terminal states)."""
    with _lock:
        _details.pop(job_id, None)
