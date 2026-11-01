"""Content calendar API: plan, reschedule and track posts across channels.

Posts are a plan / tracker only — VietDub does not upload to the platforms. The user posts
manually and marks the post published (optionally linking the synced ChannelVideo); channel
sync also auto-matches new videos to planned posts (services/content_calendar.py).

Datetimes follow the rest of the API: naive UTC ISO strings without ``Z``. Inputs may carry
an offset / ``Z`` and are converted to UTC.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .database import get_db
from .models import Channel, ChannelVideo, MediaJob, ScheduledPost
from .services import content_calendar as cc

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

Status = Literal["idea", "draft", "scheduled", "published", "failed", "cancelled"]
Platform = Literal["youtube", "facebook", "tiktok"]
Color = Literal["", "blue", "violet", "pink", "cyan", "green", "amber", "slate"]

MAX_RANGE_DAYS = 400


# -- Schemas ---------------------------------------------------------------------------------
class PostBase(BaseModel):
    caption: str | None = Field(default=None, max_length=10000)
    hashtags: list[str] | str | None = None
    platform: Platform | None = None
    channel_id: str | None = None
    media_job_id: str | None = None
    scheduled_at: str | None = None
    status: Status | None = None
    published_url: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=10000)
    color: Color | None = None
    label: str | None = Field(default=None, max_length=60)


class PostCreate(PostBase):
    title: str = Field(min_length=1, max_length=500)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class PostPatch(PostBase):
    title: str | None = Field(default=None, min_length=1, max_length=500)


class DuplicateRequest(BaseModel):
    scheduled_at: str | None = None


class PublishRequest(BaseModel):
    published_url: str | None = Field(default=None, max_length=2048)
    channel_video_id: str | None = None
    published_at: str | None = None


# -- Helpers -----------------------------------------------------------------------------------
def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def parse_datetime(value: str | None, field: str = "scheduled_at") -> datetime | None:
    """ISO 8601 (date or datetime, optional offset/Z) -> naive UTC. Blank -> None."""
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise HTTPException(400, f"Invalid {field}: {value!r} (use ISO 8601, e.g. 2026-11-02T19:00:00Z).")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def clean_url(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(400, "published_url must be an http(s) URL.")
    return value


def _get_post(db: Session, post_id: str) -> ScheduledPost:
    post = db.get(ScheduledPost, post_id)
    if post is None:
        raise HTTPException(404, "Post not found")
    return post


def post_views(db: Session, posts: list[ScheduledPost]) -> list[dict[str, Any]]:
    """Serialise posts with their channel / job / video summaries (batched lookups)."""
    channel_ids = {p.channel_id for p in posts if p.channel_id}
    job_ids = {p.media_job_id for p in posts if p.media_job_id}
    video_ids = {p.channel_video_id for p in posts if p.channel_video_id}
    channels = {c.id: c for c in db.query(Channel).filter(Channel.id.in_(channel_ids))} if channel_ids else {}
    jobs = {j.id: j for j in db.query(MediaJob).filter(MediaJob.id.in_(job_ids))} if job_ids else {}
    videos = {v.id: v for v in db.query(ChannelVideo).filter(ChannelVideo.id.in_(video_ids))} if video_ids else {}
    out = []
    for post in posts:
        channel = channels.get(post.channel_id or "")
        job = jobs.get(post.media_job_id or "")
        video = videos.get(post.channel_video_id or "")
        out.append(
            {
                "id": post.id,
                "title": post.title,
                "caption": post.caption or "",
                "hashtags": cc.load_hashtags(post),
                "platform": post.platform,
                # A deleted channel leaves a dangling id on SQLite (FK pragma off): report null.
                "channel_id": post.channel_id if channel else None,
                "channel": (
                    {
                        "id": channel.id,
                        "name": channel.name,
                        "handle": channel.handle,
                        "platform": channel.platform,
                        "avatar_url": channel.avatar_url,
                        "status": channel.status,
                    }
                    if channel
                    else None
                ),
                "media_job_id": post.media_job_id,
                "media_job": (
                    {
                        "id": job.id,
                        "title": job.source_title or job.video_url,
                        "thumbnail_url": job.thumbnail_url,
                        "status": job.status.value if hasattr(job.status, "value") else job.status,
                    }
                    if job
                    else None
                ),
                "scheduled_at": iso(post.scheduled_at),
                "status": post.status,
                "published_at": iso(post.published_at),
                "published_url": post.published_url or "",
                "channel_video_id": post.channel_video_id if video else None,
                "channel_video": (
                    {
                        "id": video.id,
                        "title": video.title,
                        "url": video.url,
                        "views": video.views,
                        "likes": video.likes,
                        "comments": video.comments,
                        "thumbnail_url": video.thumbnail_url,
                        "published_at": iso(video.published_at),
                    }
                    if video
                    else None
                ),
                "notes": post.notes or "",
                "color": post.color or "",
                "label": post.label or "",
                "created_at": iso(post.created_at),
                "updated_at": iso(post.updated_at),
            }
        )
    return out


def post_view(db: Session, post: ScheduledPost) -> dict[str, Any]:
    return post_views(db, [post])[0]


def apply_fields(db: Session, post: ScheduledPost, data: dict[str, Any]) -> None:
    """Apply validated fields (only keys present in ``data``) and enforce invariants."""
    if "title" in data and data["title"] is not None:
        title = data["title"].strip()
        if not title:
            raise HTTPException(400, "title must not be blank")
        post.title = title
    for key in ("caption", "notes", "label"):
        if key in data:
            setattr(post, key, (data[key] or "").strip() if key == "label" else (data[key] or ""))
    if "color" in data:
        post.color = data["color"] or ""
    if "hashtags" in data:
        post.hashtags_json = json.dumps(cc.normalize_hashtags(data["hashtags"]), ensure_ascii=False)
    if "platform" in data:
        post.platform = data["platform"]
    if "channel_id" in data:
        channel_id = data["channel_id"] or None
        if channel_id:
            channel = db.get(Channel, channel_id)
            if channel is None:
                raise HTTPException(404, "Channel not found")
            if post.platform and "platform" in data and data["platform"] and data["platform"] != channel.platform:
                raise HTTPException(400, "platform does not match the channel's platform.")
            post.platform = channel.platform
        post.channel_id = channel_id
    elif "platform" in data and post.channel_id:
        channel = db.get(Channel, post.channel_id)
        if channel is not None and data["platform"] != channel.platform:
            # Switching platform drops a channel of another platform.
            post.channel_id = None
    if "media_job_id" in data:
        job_id = data["media_job_id"] or None
        if job_id and db.get(MediaJob, job_id) is None:
            raise HTTPException(404, "Dubbing project not found")
        post.media_job_id = job_id
    if "scheduled_at" in data:
        post.scheduled_at = parse_datetime(data["scheduled_at"])
    if "published_url" in data:
        post.published_url = clean_url(data["published_url"])
    if "status" in data and data["status"]:
        post.status = data["status"]
        if post.status == "published" and post.published_at is None:
            post.published_at = post.scheduled_at or cc.utcnow()
        if post.status != "published":
            post.published_at = None if post.status in ("idea", "draft", "scheduled") else post.published_at
    if post.status == "idea" and "scheduled_at" in data and post.scheduled_at is not None and "status" not in data:
        # Dropping an idea onto the calendar turns it into a draft.
        post.status = "draft"
    if post.status == "scheduled" and post.scheduled_at is None:
        raise HTTPException(400, "A scheduled post needs scheduled_at.")


# -- Endpoints ---------------------------------------------------------------------------------
@router.get("/posts")
def list_posts(
    start: str | None = Query(default=None, description="Inclusive, ISO (UTC if no offset)."),
    end: str | None = Query(default=None, description="Exclusive, ISO."),
    platform: str | None = Query(default=None, description="Comma separated; 'none' = no platform."),
    channel_id: str | None = None,
    status: str | None = Query(default=None, description="Comma separated statuses."),
    unscheduled: bool | None = Query(default=None, description="true = only posts without a date."),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=1000, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = db.query(ScheduledPost)
    start_dt = parse_datetime(start, "start")
    end_dt = parse_datetime(end, "end")
    if start_dt and end_dt:
        if end_dt <= start_dt:
            raise HTTPException(400, "end must be after start.")
        if end_dt - start_dt > timedelta(days=MAX_RANGE_DAYS):
            raise HTTPException(400, f"Date range is limited to {MAX_RANGE_DAYS} days.")
    if start_dt:
        query = query.filter(ScheduledPost.scheduled_at >= start_dt)
    if end_dt:
        query = query.filter(ScheduledPost.scheduled_at < end_dt)
    if unscheduled is True:
        query = query.filter(ScheduledPost.scheduled_at.is_(None))
    elif unscheduled is False:
        query = query.filter(ScheduledPost.scheduled_at.isnot(None))
    if platform:
        wanted = [p.strip() for p in platform.split(",") if p.strip()]
        bad = [p for p in wanted if p not in cc.PLATFORMS and p != "none"]
        if bad:
            raise HTTPException(400, f"Unknown platform: {', '.join(bad)}")
        conds = [ScheduledPost.platform.in_([p for p in wanted if p != "none"])]
        if "none" in wanted:
            conds.append(ScheduledPost.platform.is_(None))
        query = query.filter(or_(*conds))
    if channel_id:
        query = query.filter(ScheduledPost.channel_id == channel_id)
    if status:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        bad = [s for s in wanted if s not in cc.STATUSES]
        if bad:
            raise HTTPException(400, f"Unknown status: {', '.join(bad)}")
        query = query.filter(ScheduledPost.status.in_(wanted))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(ScheduledPost.title.ilike(like), ScheduledPost.caption.ilike(like)))
    posts = (
        query.order_by(
            ScheduledPost.scheduled_at.is_(None),
            ScheduledPost.scheduled_at.asc(),
            ScheduledPost.created_at.desc(),
        )
        .limit(limit)
        .all()
    )
    return post_views(db, posts)


@router.get("/upcoming")
def upcoming_posts(limit: int = Query(default=5, ge=1, le=50), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Next planned (draft / scheduled) posts from now on, soonest first."""
    posts = (
        db.query(ScheduledPost)
        .filter(
            ScheduledPost.status.in_(cc.OPEN_STATUSES),
            ScheduledPost.scheduled_at.isnot(None),
            ScheduledPost.scheduled_at >= cc.utcnow() - timedelta(hours=1),
        )
        .order_by(ScheduledPost.scheduled_at.asc())
        .limit(limit)
        .all()
    )
    return post_views(db, posts)


@router.get("/suggestions")
def suggested_slots(
    platform: Platform | None = None,
    channel_id: str | None = None,
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840, description="Local minus UTC, e.g. 420 for UTC+7."),
    days: int = Query(default=7, ge=1, le=60),
    limit: int = Query(default=6, ge=1, le=30),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if channel_id:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise HTTPException(404, "Channel not found")
        platform = platform or channel.platform  # type: ignore[assignment]
    return cc.suggest_slots(
        db, platform=platform, channel_id=channel_id, tz_offset_minutes=tz_offset_minutes, days=days, limit=limit
    )


@router.post("/posts", status_code=201)
def create_post(payload: PostCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    now = cc.utcnow()
    data = payload.model_dump(exclude_unset=True)
    post = ScheduledPost(title=payload.title, created_at=now, updated_at=now)
    if not data.get("status"):
        data["status"] = "scheduled" if parse_datetime(data.get("scheduled_at")) else "idea"
    apply_fields(db, post, data)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post_view(db, post)


@router.get("/posts/{post_id}")
def get_post(post_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return post_view(db, _get_post(db, post_id))


@router.patch("/posts/{post_id}")
def update_post(post_id: str, payload: PostPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    post = _get_post(db, post_id)
    data = payload.model_dump(exclude_unset=True)
    apply_fields(db, post, data)
    if post.status != "published" and "status" in data:
        post.channel_video_id = None if data["status"] in ("idea", "draft", "scheduled") else post.channel_video_id
    post.updated_at = cc.utcnow()
    db.commit()
    db.refresh(post)
    return post_view(db, post)


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: str, db: Session = Depends(get_db)) -> Response:
    post = _get_post(db, post_id)
    db.delete(post)
    db.commit()
    return Response(status_code=204)


@router.post("/posts/{post_id}/duplicate", status_code=201)
def duplicate_post(post_id: str, payload: DuplicateRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    src = _get_post(db, post_id)
    now = cc.utcnow()
    scheduled_at = src.scheduled_at
    if payload is not None and payload.scheduled_at is not None:
        scheduled_at = parse_datetime(payload.scheduled_at)
    status = src.status
    if status in ("published", "failed", "cancelled"):
        status = "draft" if scheduled_at else "idea"
    if status == "scheduled" and scheduled_at is None:
        status = "idea"
    copy = ScheduledPost(
        title=src.title,
        caption=src.caption,
        hashtags_json=src.hashtags_json,
        platform=src.platform,
        channel_id=src.channel_id,
        media_job_id=src.media_job_id,
        scheduled_at=scheduled_at,
        status=status,
        notes=src.notes,
        color=src.color,
        label=src.label,
        created_at=now,
        updated_at=now,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return post_view(db, copy)


@router.post("/posts/{post_id}/publish")
def mark_published(post_id: str, payload: PublishRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    post = _get_post(db, post_id)
    payload = payload or PublishRequest()
    video = None
    if payload.channel_video_id:
        video = db.get(ChannelVideo, payload.channel_video_id)
        if video is None:
            raise HTTPException(404, "Channel video not found")
        if post.channel_id and video.channel_id != post.channel_id:
            raise HTTPException(400, "The video belongs to another channel.")
    url = clean_url(payload.published_url)
    published_at = parse_datetime(payload.published_at, "published_at")
    cc.apply_published(post, video=video, url=url, published_at=published_at)
    db.commit()
    db.refresh(post)
    return post_view(db, post)


@router.get("/posts/{post_id}/video-candidates")
def video_candidates(post_id: str, limit: int = Query(default=10, ge=1, le=50), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Synced videos of the post's channel (or platform) near its date, best title match first —
    lets the user link the published video when marking the post published."""
    post = _get_post(db, post_id)
    query = db.query(ChannelVideo, Channel).join(Channel, Channel.id == ChannelVideo.channel_id)
    if post.channel_id:
        query = query.filter(ChannelVideo.channel_id == post.channel_id)
    elif post.platform:
        query = query.filter(Channel.platform == post.platform)
    anchor = post.scheduled_at or cc.utcnow()
    query = query.filter(
        ChannelVideo.published_at.isnot(None),
        ChannelVideo.published_at >= anchor - timedelta(days=14),
        ChannelVideo.published_at <= anchor + timedelta(days=14),
    )
    rows = query.limit(300).all()
    scored = []
    for video, channel in rows:
        score = cc.title_similarity(post.title, video.title)
        gap = abs((video.published_at - anchor).total_seconds())
        scored.append((score, -gap, video, channel))
    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [
        {
            "id": video.id,
            "channel_id": video.channel_id,
            "channel_name": channel.name,
            "platform": channel.platform,
            "title": video.title,
            "url": video.url,
            "thumbnail_url": video.thumbnail_url,
            "published_at": iso(video.published_at),
            "views": video.views,
            "score": round(score, 2),
        }
        for score, _gap, video, channel in scored[:limit]
    ]
