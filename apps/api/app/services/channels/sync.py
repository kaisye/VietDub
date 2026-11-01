"""Channel sync: profile + daily stats snapshot + recent videos, in a background thread.

The worker never raises: every failure is recorded on the channel (status /
last_error) and in ``sync_status()``. Only OAuth channels are synced; manual
channels are maintained through manual.py.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

from ... import database
from ...models import Channel, ChannelSnapshot, ChannelVideo
from .. import content_calendar
from .oauth import ensure_access_token, refresh_channel_token
from .providers import get_provider
from .providers.base import (
    ChannelAuthError,
    ChannelProvider,
    ChannelProviderError,
    Stats,
    VideoItem,
    utcnow,
)

logger = logging.getLogger(__name__)

# Patched in tests; resolved at call time so the worker uses the configured DB.
session_factory: Callable[[], Session] | None = None

_lock = threading.Lock()
_pending: list[str] = []
_thread: threading.Thread | None = None
_status: dict[str, Any] = {
    "running": False,
    "current_channel_id": None,
    "queued": 0,
    "started_at": None,
    "finished_at": None,
    "results": {},
}


def _session() -> Session:
    factory = session_factory or database.SessionLocal
    return factory()


def today_key(now: datetime | None = None) -> str:
    return (now or utcnow()).strftime("%Y-%m-%d")


def _with_token_retry(db: Session, channel: Channel, provider: ChannelProvider, token_box: list[str], fn):
    """Run ``fn(token)``; on an auth error refresh once and retry (token revoked early)."""
    try:
        return fn(token_box[0])
    except ChannelAuthError:
        if not channel.refresh_token_encrypted:
            raise
        token_box[0] = refresh_channel_token(db, channel, provider)
        return fn(token_box[0])


def sync_channel(db: Session, channel: Channel, provider: ChannelProvider | None = None) -> dict[str, Any]:
    """Synchronise one OAuth channel. Returns a result dict; never raises."""
    if channel.source != "oauth":
        return {"channel_id": channel.id, "ok": False, "skipped": True, "error": "Manual channel"}
    if channel.status == "disconnected" or not channel.access_token_encrypted:
        return {"channel_id": channel.id, "ok": False, "skipped": True, "error": "Channel is disconnected"}
    own = provider is None
    provider = provider or get_provider(channel.platform)
    external_id = channel.external_id or ""
    try:
        token_box = [ensure_access_token(db, channel, provider)]
        profile = _with_token_retry(db, channel, provider, token_box, lambda t: provider.fetch_profile(t, external_id))
        stats = _with_token_retry(db, channel, provider, token_box, lambda t: provider.fetch_stats(t, external_id))
        videos = _with_token_retry(
            db, channel, provider, token_box, lambda t: provider.fetch_recent_videos(t, external_id)
        )
        now = utcnow()
        channel.name = profile.name or channel.name
        channel.handle = profile.handle or channel.handle
        channel.url = profile.url or channel.url
        channel.avatar_url = profile.avatar_url or channel.avatar_url
        video_rows = upsert_videos(db, channel, videos, source="sync", now=now)
        # Content calendar: planned posts whose video just appeared become "published".
        content_calendar.safe_match_after_sync(db, channel.id, video_rows)
        fill_stats_from_videos(stats, videos)
        upsert_snapshot(db, channel, stats, source="sync", now=now)
        channel.status = "connected"
        channel.last_error = ""
        channel.last_synced_at = now
        channel.updated_at = now
        db.commit()
        return {"channel_id": channel.id, "ok": True, "videos": len(videos), "error": None}
    except ChannelAuthError as exc:
        db.rollback()
        _record_error(db, channel, "needs_reauth", str(exc))
        return {"channel_id": channel.id, "ok": False, "error": str(exc), "needs_reauth": True}
    except ChannelProviderError as exc:
        db.rollback()
        _record_error(db, channel, "error", str(exc))
        return {"channel_id": channel.id, "ok": False, "error": str(exc)}
    except Exception:
        db.rollback()
        logger.exception("Channel sync crashed for %s", channel.id)
        message = f"{channel.platform}: unexpected sync error."
        _record_error(db, channel, "error", message)
        return {"channel_id": channel.id, "ok": False, "error": message}
    finally:
        if own:
            provider.close()


def _record_error(db: Session, channel: Channel, status: str, message: str) -> None:
    try:
        channel = db.merge(channel)
        channel.status = status
        channel.last_error = message
        channel.updated_at = utcnow()
        db.commit()
    except Exception:  # pragma: no cover - DB down; nothing else to do
        db.rollback()
        logger.exception("Could not record channel sync error")


def fill_stats_from_videos(stats: Stats, videos: Iterable[VideoItem]) -> Stats:
    """Platforms without lifetime totals (TikTok views, Facebook views/likes) get
    the sum over the recent videos fetched in this sync."""
    videos = list(videos)
    for field in ("views", "likes", "comments", "shares"):
        if getattr(stats, field) is None:
            values = [getattr(v, field) for v in videos if getattr(v, field) is not None]
            if values:
                setattr(stats, field, sum(values))
    return stats


def upsert_snapshot(
    db: Session,
    channel: Channel,
    stats: Stats,
    *,
    source: str,
    date: str | None = None,
    now: datetime | None = None,
) -> ChannelSnapshot:
    now = now or utcnow()
    date = date or today_key(now)
    row = (
        db.query(ChannelSnapshot)
        .filter(ChannelSnapshot.channel_id == channel.id, ChannelSnapshot.date == date)
        .one_or_none()
    )
    if row is None:
        row = ChannelSnapshot(channel_id=channel.id, date=date, created_at=now)
        db.add(row)
    for field in ("followers", "views", "video_count", "likes", "comments", "shares"):
        value = getattr(stats, field)
        if value is not None:
            setattr(row, field, int(value))
    row.source = source
    row.updated_at = now
    return row


def upsert_videos(
    db: Session, channel: Channel, videos: Iterable[VideoItem], *, source: str, now: datetime | None = None
) -> list[ChannelVideo]:
    now = now or utcnow()
    items = [v for v in videos if v.external_id]
    existing = {
        row.external_id: row
        for row in db.query(ChannelVideo).filter(
            ChannelVideo.channel_id == channel.id,
            ChannelVideo.external_id.in_([v.external_id for v in items] or [""]),
        )
    }
    rows = []
    for item in items:
        row = existing.get(item.external_id)
        if row is None:
            row = ChannelVideo(channel_id=channel.id, external_id=item.external_id, created_at=now, source=source)
            db.add(row)
            existing[item.external_id] = row
        row.kind = item.kind or row.kind or "video"
        row.title = item.title or row.title or ""
        row.published_at = item.published_at or row.published_at
        for field in ("views", "likes", "comments", "shares", "duration_seconds"):
            value = getattr(item, field)
            if value is not None:
                setattr(row, field, value)
        row.thumbnail_url = item.thumbnail_url or row.thumbnail_url or ""
        row.url = item.url or row.url or ""
        row.updated_at = now
        rows.append(row)
    return rows


# -- Background worker -----------------------------------------------------------
def start_sync(channel_ids: Iterable[str] | None = None, *, only_stale_hours: float | None = None) -> dict[str, Any]:
    """Queue channels (default: all OAuth channels) and start the worker if idle."""
    db = _session()
    try:
        query = db.query(Channel).filter(Channel.source == "oauth", Channel.status != "disconnected")
        if channel_ids is not None:
            ids = list(channel_ids)
            query = query.filter(Channel.id.in_(ids or [""]))
        channels = query.all()
        if only_stale_hours is not None:
            cutoff = utcnow() - timedelta(hours=only_stale_hours)
            channels = [c for c in channels if c.last_synced_at is None or c.last_synced_at < cutoff]
        targets = [c.id for c in channels]
    finally:
        db.close()
    global _thread
    with _lock:
        for channel_id in targets:
            if channel_id not in _pending and channel_id != _status["current_channel_id"]:
                _pending.append(channel_id)
        _status["queued"] = len(_pending)
        started = False
        if _pending and (_thread is None or not _thread.is_alive()):
            _status.update(running=True, started_at=utcnow(), finished_at=None)
            _thread = threading.Thread(target=_worker, name="channel-sync", daemon=True)
            _thread.start()
            started = True
        return {"queued_channel_ids": targets, "started": started, "running": bool(_status["running"])}


def _worker() -> None:
    while True:
        with _lock:
            if not _pending:
                _status.update(running=False, current_channel_id=None, queued=0, finished_at=utcnow())
                return
            channel_id = _pending.pop(0)
            _status.update(current_channel_id=channel_id, queued=len(_pending))
        result: dict[str, Any]
        db = None
        try:
            db = _session()
            channel = db.get(Channel, channel_id)
            if channel is None:
                result = {"channel_id": channel_id, "ok": False, "error": "Channel not found"}
            else:
                result = sync_channel(db, channel)
        except Exception:  # never let the thread die silently mid-queue
            logger.exception("Channel sync worker error")
            result = {"channel_id": channel_id, "ok": False, "error": "unexpected sync error"}
        finally:
            if db is not None:
                db.close()
        result["finished_at"] = utcnow()
        with _lock:
            _status["results"][channel_id] = result


def sync_status() -> dict[str, Any]:
    with _lock:
        return {
            "running": bool(_status["running"]),
            "current_channel_id": _status["current_channel_id"],
            "queued": len(_pending),
            "started_at": _status["started_at"],
            "finished_at": _status["finished_at"],
            "results": list(_status["results"].values())[-50:],
        }


def wait_for_idle(timeout: float = 10.0) -> bool:
    """Test helper: block until the worker drains its queue."""
    thread = _thread
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()
