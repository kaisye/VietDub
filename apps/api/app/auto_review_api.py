"""Auto review API: review a dubbed video's voice pacing and subtitle cuts, propose and apply fixes.

Endpoints (all under /jobs so the desktop dev proxy already forwards them):
  POST /jobs/{id}/auto-review                 start a review {mode: report|propose|safe}
  GET  /jobs/{id}/auto-review                 full result (issues, proposals, batches)
  GET  /jobs/{id}/auto-review/status          light status for polling / badges
  POST /jobs/{id}/auto-review/apply           {scope: selected|all|safe, ids?}
  POST /jobs/{id}/auto-review/reject          {ids}
  POST /jobs/{id}/auto-review/batches/{bid}/revert
  POST /jobs/{id}/auto-review/proposals/{pid}/preview   synthesize a rewrite to listen to
  GET  /jobs/auto-review/summaries?ids=a,b    badges for project lists
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .database import get_db
from .models import MediaJob, MediaJobStatus
from .services import editor
from .services.auto_review import runner
from .services.production_configuration import parse_configuration_json

router = APIRouter(tags=["auto-review"])

Mode = Literal["report", "propose", "safe"]


class StartIn(BaseModel):
    mode: Mode = "propose"


class ApplyIn(BaseModel):
    scope: Literal["selected", "all", "safe"] = "selected"
    ids: list[str] = Field(default_factory=list, max_length=2000)


class RejectIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=2000)


def _job(job_id: str, db: Session) -> MediaJob:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def _voice_settings(job: MediaJob) -> dict[str, Any]:
    from .worker import editor_voice_settings

    return editor_voice_settings(job)


def _idle(job_id: str) -> dict[str, Any]:
    return {"job_id": job_id, "status": "idle", "proposals": [], "issues": [], "batches": []}


@router.get("/jobs/auto-review/summaries")
def review_summaries(ids: str = Query("", max_length=40000)) -> dict[str, dict[str, Any]]:
    wanted = [item for item in ids.split(",") if item.strip()][:500]
    return {job_id: runner.summary(runner.load_review(job_id)) for job_id in wanted}


@router.post("/jobs/{job_id}/auto-review")
def start_auto_review(job_id: str, payload: StartIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = _job(job_id, db)
    if job.status != MediaJobStatus.ready:
        raise HTTPException(status_code=409, detail="Chỉ rà soát được video đã lồng tiếng xong.")
    try:
        return runner.start_review(
            job_id,
            mode=payload.mode,
            configuration=parse_configuration_json(job.configuration_snapshot_json),
            title=job.source_title or "",
            source_language=job.source_language or "auto",
            voice_settings=_voice_settings(job),
            trigger="manual",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/auto-review")
def get_auto_review(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    _job(job_id, db)
    review = runner.load_review(job_id)
    return {**review, "running": runner.is_running(job_id)} if review else _idle(job_id)


@router.get("/jobs/{job_id}/auto-review/status")
def get_auto_review_status(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    _job(job_id, db)
    review = runner.load_review(job_id)
    if not review:
        return {"status": "idle", "summary": runner.summary(None)}
    return {
        "status": review.get("status"),
        "phase": review.get("phase"),
        "progress": review.get("progress"),
        "applying": review.get("applying"),
        "error": review.get("error"),
        "updated_at": review.get("updated_at"),
        "summary": runner.summary(review),
    }


@router.post("/jobs/{job_id}/auto-review/apply")
def apply_auto_review(job_id: str, payload: ApplyIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = _job(job_id, db)
    try:
        return runner.start_apply(job_id, payload.ids, _voice_settings(job), scope=payload.scope)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/auto-review/reject")
def reject_auto_review(job_id: str, payload: RejectIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    _job(job_id, db)
    try:
        return runner.reject(job_id, payload.ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/auto-review/batches/{batch_id}/revert")
def revert_auto_review_batch(job_id: str, batch_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    _job(job_id, db)
    try:
        review = runner.revert_batch(job_id, batch_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with editor.workspace_lock(job_id):
        workspace = editor.load_workspace(job_id)
    return {"review": review, "workspace": editor.with_issues(workspace)}


@router.post("/jobs/{job_id}/auto-review/proposals/{proposal_id}/preview")
def preview_auto_review(job_id: str, proposal_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = _job(job_id, db)
    try:
        return runner.preview_rewrite(job_id, proposal_id, _voice_settings(job))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — TTS errors are shown to the user.
        raise HTTPException(status_code=500, detail=f"Tạo bản nghe thử lỗi: {exc}") from exc
