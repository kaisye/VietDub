from __future__ import annotations

import threading
import time
from datetime import datetime

from sqlalchemy.orm import Session, selectinload

from .database import SessionLocal
from .models import Job, JobStep, StepStatus, Video, VideoStatus


WORKFLOW_STEPS = [
    "Source video",
    "Subtitle extraction",
    "Translation",
    "Script adaptation",
    "Voice generation",
    "Audio alignment",
    "Rendering",
    "QA validation",
    "Publishing preparation",
]


def create_job(db: Session, video: Video) -> Job:
    job = Job(video_id=video.id, status=VideoStatus.queued, current_step="Queued")
    db.add(job)
    db.flush()

    for index, step_name in enumerate(WORKFLOW_STEPS):
        db.add(JobStep(job_id=job.id, name=step_name, sort_order=index))

    video.status = VideoStatus.queued
    video.progress = 0
    video.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job


def start_mock_job(job_id: str) -> None:
    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    thread.start()


def _run_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.query(Job).options(selectinload(Job.steps)).filter(Job.id == job_id).one()
        video = db.query(Video).filter(Video.id == job.video_id).one()
        job.status = VideoStatus.processing
        video.status = VideoStatus.processing
        db.commit()

    total = len(WORKFLOW_STEPS)
    for index, step_name in enumerate(WORKFLOW_STEPS):
        with SessionLocal() as db:
            job = db.query(Job).options(selectinload(Job.steps)).filter(Job.id == job_id).one()
            video = db.query(Video).filter(Video.id == job.video_id).one()
            step = sorted(job.steps, key=lambda item: item.sort_order)[index]
            step.status = StepStatus.processing
            step.logs = f"{step_name} started."
            job.current_step = step_name
            job.updated_at = datetime.utcnow()
            db.commit()

        time.sleep(0.8)

        with SessionLocal() as db:
            job = db.query(Job).options(selectinload(Job.steps)).filter(Job.id == job_id).one()
            video = db.query(Video).filter(Video.id == job.video_id).one()
            step = sorted(job.steps, key=lambda item: item.sort_order)[index]
            step.status = StepStatus.completed
            step.progress = 100
            step.runtime_seconds = 8 + (index * 4)
            step.logs = f"{step_name} completed successfully."
            progress = int(((index + 1) / total) * 100)
            job.progress = progress
            video.progress = progress
            job.updated_at = datetime.utcnow()
            video.last_updated = datetime.utcnow()
            db.commit()

        time.sleep(0.35)

    with SessionLocal() as db:
        job = db.query(Job).filter(Job.id == job_id).one()
        video = db.query(Video).filter(Video.id == job.video_id).one()
        job.status = VideoStatus.needs_review
        job.current_step = "Ready for review"
        job.progress = 100
        job.updated_at = datetime.utcnow()
        video.status = VideoStatus.needs_review
        video.progress = 100
        video.last_updated = datetime.utcnow()
        db.commit()
