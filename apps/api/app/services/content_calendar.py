"""Content calendar helpers: hashtag/title normalisation, auto-matching synced videos to
planned posts, and "suggested slots" from a channel's posting history.

VietDub never uploads to the platforms. A planned post becomes "published" either when the
user marks it so, or when a channel sync brings a video whose title matches a post planned
on that channel within ``MATCH_WINDOW`` (``match_synced_videos``).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..models import Channel, ChannelVideo, ScheduledPost

logger = logging.getLogger(__name__)

STATUSES = ("idea", "draft", "scheduled", "published", "failed", "cancelled")
PLATFORMS = ("youtube", "facebook", "tiktok")
COLORS = ("", "blue", "violet", "pink", "cyan", "green", "amber", "slate")
# Posts still waiting to go out (auto-match candidates, "upcoming" list).
OPEN_STATUSES = ("draft", "scheduled")
MATCH_WINDOW = timedelta(days=2)
MAX_HASHTAGS = 30


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# -- Normalisation -------------------------------------------------------------------------
def normalize_hashtags(value: Any) -> list[str]:
    """Accept a list or a free-text string ("#a, b #c") -> ["#a", "#b", "#c"] (deduped)."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\s,;]+", value)
    else:
        parts = [str(v) for v in value]
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = part.strip().lstrip("#").strip()
        tag = re.sub(r"\s+", "", tag)
        if not tag:
            continue
        tag = "#" + tag[:80]
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            out.append(tag)
    return out[:MAX_HASHTAGS]


def load_hashtags(post: ScheduledPost) -> list[str]:
    try:
        data = json.loads(post.hashtags_json or "[]")
        return [str(x) for x in data] if isinstance(data, list) else []
    except ValueError:
        return []


def _fold(text: str) -> str:
    """Lowercase, strip Vietnamese diacritics (đ -> d), keep letters/digits only."""
    text = (text or "").casefold().replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"#\S+", " ", text)  # hashtags in TikTok captions are noise
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def title_similarity(a: str, b: str) -> float:
    """0..1. 1.0 for equal or containment (≥ 8 chars); otherwise token Jaccard."""
    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0
    short, long_ = sorted((fa, fb), key=len)
    if len(short) >= 8 and short in long_:
        return 0.95
    ta, tb = set(fa.split()), set(fb.split())
    return len(ta & tb) / len(ta | tb)


# -- Auto-match after channel sync -----------------------------------------------------------
MATCH_THRESHOLD = 0.6


def match_synced_videos(db: Session, channel_id: str, videos: Iterable[ChannelVideo]) -> int:
    """Mark planned posts on ``channel_id`` as published when a synced video matches.

    A video matches a draft/scheduled post on the same channel whose ``scheduled_at`` is
    within ±2 days of the video's ``published_at`` and whose title is similar enough. Each
    video and each post is used at most once (best score first). Does not commit; returns
    the number of posts linked.
    """
    videos = [v for v in videos if v.published_at is not None and (v.title or "").strip()]
    if not videos:
        return 0
    db.flush()  # new ChannelVideo rows need their ids
    earliest = min(v.published_at for v in videos) - MATCH_WINDOW
    latest = max(v.published_at for v in videos) + MATCH_WINDOW
    posts = (
        db.query(ScheduledPost)
        .filter(
            ScheduledPost.channel_id == channel_id,
            ScheduledPost.status.in_(OPEN_STATUSES),
            ScheduledPost.channel_video_id.is_(None),
            ScheduledPost.scheduled_at.isnot(None),
            ScheduledPost.scheduled_at >= earliest,
            ScheduledPost.scheduled_at <= latest,
        )
        .all()
    )
    if not posts:
        return 0
    already_linked = {
        row[0]
        for row in db.query(ScheduledPost.channel_video_id)
        .filter(ScheduledPost.channel_video_id.in_([v.id for v in videos]))
        .all()
    }
    candidates: list[tuple[float, float, ScheduledPost, ChannelVideo]] = []
    for video in videos:
        if video.id in already_linked:
            continue
        for post in posts:
            gap = abs((post.scheduled_at - video.published_at).total_seconds())
            if gap > MATCH_WINDOW.total_seconds():
                continue
            score = title_similarity(post.title, video.title)
            if score >= MATCH_THRESHOLD:
                candidates.append((score, -gap, post, video))
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    used_posts: set[str] = set()
    used_videos: set[str] = set()
    now = utcnow()
    for _score, _gap, post, video in candidates:
        if post.id in used_posts or video.id in used_videos:
            continue
        used_posts.add(post.id)
        used_videos.add(video.id)
        apply_published(post, video=video, now=now)
    return len(used_posts)


def apply_published(
    post: ScheduledPost,
    *,
    video: ChannelVideo | None = None,
    url: str | None = None,
    published_at: datetime | None = None,
    now: datetime | None = None,
) -> ScheduledPost:
    now = now or utcnow()
    post.status = "published"
    if video is not None:
        post.channel_video_id = video.id
        post.channel_id = post.channel_id or video.channel_id
        if not post.platform and video.channel is not None:
            post.platform = video.channel.platform
    post.published_url = (url or "").strip() or (video.url if video is not None else "") or post.published_url or ""
    post.published_at = published_at or (video.published_at if video is not None else None) or post.published_at or now
    if post.scheduled_at is None:
        post.scheduled_at = post.published_at
    post.updated_at = now
    return post


def safe_match_after_sync(db: Session, channel_id: str, videos: Iterable[ChannelVideo]) -> int:
    """Defensive wrapper used by channel sync: never lets a calendar problem fail a sync."""
    try:
        return match_synced_videos(db, channel_id, list(videos))
    except Exception:  # pragma: no cover - defensive
        logger.exception("Content calendar auto-match failed for channel %s", channel_id)
        return 0


# -- Suggested slots ---------------------------------------------------------------------------
DEFAULT_HOURS = {"youtube": (19, 12), "facebook": (20, 12), "tiktok": (21, 18)}


def suggest_slots(
    db: Session,
    *,
    platform: str | None,
    channel_id: str | None,
    tz_offset_minutes: int,
    days: int,
    limit: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Next free slots at the channel's best-performing local hours.

    History = synced videos of the channel (or platform) with views; hours ranked by
    average views. Falls back to common evening slots when there is too little data.
    Slots already taken by a planned post (same hour, same channel/platform) are skipped.
    """
    now = now or utcnow()
    offset = timedelta(minutes=tz_offset_minutes)
    query = db.query(ChannelVideo).filter(ChannelVideo.published_at.isnot(None), ChannelVideo.views.isnot(None))
    if channel_id:
        query = query.filter(ChannelVideo.channel_id == channel_id)
    elif platform:
        query = query.join(Channel, Channel.id == ChannelVideo.channel_id).filter(Channel.platform == platform)
    videos = query.order_by(ChannelVideo.published_at.desc()).limit(500).all()
    # CSV / manual imports often carry a date only (stored as 00:00:00 UTC): no hour signal.
    videos = [v for v in videos if v.published_at.time() != datetime.min.time()]

    by_hour: dict[int, list[int]] = defaultdict(list)
    for video in videos:
        local = video.published_at + offset
        by_hour[local.hour].append(int(video.views or 0))
    ranked = sorted(
        ((sum(v) / len(v), hour, len(v)) for hour, v in by_hour.items() if v),
        reverse=True,
    )
    basis = "history" if len(videos) >= 5 and ranked else "default"
    if basis == "history":
        hours = [hour for _avg, hour, _n in ranked[:3]]
    else:
        hours = list(DEFAULT_HOURS.get(platform or "", (19, 12)))

    taken_q = db.query(ScheduledPost.scheduled_at).filter(
        ScheduledPost.scheduled_at.isnot(None),
        ScheduledPost.scheduled_at >= now,
        ScheduledPost.status.notin_(("cancelled",)),
    )
    if channel_id:
        taken_q = taken_q.filter(ScheduledPost.channel_id == channel_id)
    elif platform:
        taken_q = taken_q.filter(ScheduledPost.platform == platform)
    taken = {(row[0] + offset).replace(minute=0, second=0, microsecond=0) for row in taken_q.all()}

    slots: list[dict[str, Any]] = []
    local_now = now + offset
    for day in range(days + 1):
        date = (local_now + timedelta(days=day)).date()
        for rank, hour in enumerate(hours):
            local = datetime(date.year, date.month, date.day, hour)
            if local <= local_now + timedelta(minutes=30) or local in taken:
                continue
            avg = next((a for a, h, _n in ranked if h == hour), None)
            slots.append(
                {
                    "scheduled_at": (local - offset).isoformat(),
                    "local_hour": hour,
                    "rank": rank,
                    "avg_views": round(avg) if avg is not None else None,
                }
            )
            if len(slots) >= limit:
                return {"basis": basis, "hours": hours, "sample_size": len(videos), "slots": slots}
    return {"basis": basis, "hours": hours, "sample_size": len(videos), "slots": slots}
