"""Dashboard aggregates across channels: totals, per-platform, time series, top videos, activity."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ...models import Channel, ChannelSnapshot, ChannelVideo
from . import PLATFORMS
from .providers.base import utcnow

METRICS = ("followers", "views", "video_count", "likes", "comments", "shares")
ALLOWED_DAYS = (7, 28, 90)


def _date_range(start: datetime, end: datetime) -> list[str]:
    days = (end.date() - start.date()).days
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]


class _History:
    """Carry-forward lookup of a channel's snapshots (gaps keep the last known value)."""

    def __init__(self, snapshots: Iterable[ChannelSnapshot]):
        rows = sorted(snapshots, key=lambda s: s.date)
        self.dates = [r.date for r in rows]
        self.rows = rows

    def at(self, date: str) -> ChannelSnapshot | None:
        index = bisect_right(self.dates, date) - 1
        return self.rows[index] if index >= 0 else None

    def first_in_or_before(self, start: str) -> ChannelSnapshot | None:
        return self.at(start) or (self.rows[0] if self.rows else None)

    @property
    def latest(self) -> ChannelSnapshot | None:
        return self.rows[-1] if self.rows else None


def _metric(row: ChannelSnapshot | None, name: str) -> int | None:
    return getattr(row, name) if row is not None else None


def _sum(values: Iterable[int | None]) -> int:
    return sum(v for v in values if v is not None)


def _change(history: _History, start: str, name: str) -> int | None:
    latest = _metric(history.latest, name)
    first = _metric(history.first_in_or_before(start), name)
    if latest is None or first is None:
        return None
    return latest - first


def _engagement_rate(videos: list[ChannelVideo]) -> float | None:
    views = _sum(v.views for v in videos)
    if not views:
        return None
    interactions = _sum(v.likes for v in videos) + _sum(v.comments for v in videos) + _sum(v.shares for v in videos)
    return round(interactions / views, 4)


def video_view(video: ChannelVideo, channel: Channel | None = None) -> dict[str, Any]:
    channel = channel or video.channel
    return {
        "id": video.id,
        "channel_id": video.channel_id,
        "platform": channel.platform if channel else None,
        "channel_name": channel.name if channel else None,
        "external_id": video.external_id,
        "kind": video.kind,
        "title": video.title,
        "published_at": video.published_at,
        "views": video.views,
        "likes": video.likes,
        "comments": video.comments,
        "shares": video.shares,
        "duration_seconds": video.duration_seconds,
        "thumbnail_url": video.thumbnail_url,
        "url": video.url,
        "media_job_id": video.media_job_id,
        "source": video.source,
        "updated_at": video.updated_at,
    }


def build_dashboard(
    db: Session,
    *,
    days: int = 28,
    platform: str | None = None,
    include_disconnected: bool = False,
    top_limit: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utcnow()
    start_dt = now - timedelta(days=days - 1)
    start = start_dt.strftime("%Y-%m-%d")
    dates = _date_range(start_dt, now)

    query = db.query(Channel)
    if platform:
        query = query.filter(Channel.platform == platform)
    if not include_disconnected:
        query = query.filter(Channel.status != "disconnected")
    channels = query.order_by(Channel.created_at).all()
    ids = [c.id for c in channels]

    snapshots_by_channel: dict[str, list[ChannelSnapshot]] = defaultdict(list)
    videos_by_channel: dict[str, list[ChannelVideo]] = defaultdict(list)
    if ids:
        for snap in db.query(ChannelSnapshot).filter(ChannelSnapshot.channel_id.in_(ids)):
            snapshots_by_channel[snap.channel_id].append(snap)
        for video in db.query(ChannelVideo).filter(ChannelVideo.channel_id.in_(ids)):
            videos_by_channel[video.channel_id].append(video)
    histories = {cid: _History(snapshots_by_channel[cid]) for cid in ids}

    # -- per channel -------------------------------------------------------------
    channel_rows = []
    for channel in channels:
        history = histories[channel.id]
        latest = history.latest
        window_videos = [
            v
            for v in videos_by_channel[channel.id]
            if v.published_at and v.published_at.strftime("%Y-%m-%d") >= start
        ]
        channel_rows.append(
            {
                "id": channel.id,
                "platform": channel.platform,
                "source": channel.source,
                "name": channel.name,
                "handle": channel.handle,
                "avatar_url": channel.avatar_url,
                "status": channel.status,
                "last_synced_at": channel.last_synced_at,
                **{name: _metric(latest, name) for name in METRICS},
                "followers_change": _change(history, start, "followers"),
                "views_change": _change(history, start, "views"),
                "videos_in_period": len(window_videos),
                "engagement_rate": _engagement_rate(window_videos),
            }
        )

    def aggregate(rows: list[dict[str, Any]], channel_ids: list[str]) -> dict[str, Any]:
        videos = [
            v
            for cid in channel_ids
            for v in videos_by_channel[cid]
            if v.published_at and v.published_at.strftime("%Y-%m-%d") >= start
        ]
        changes_f = [r["followers_change"] for r in rows if r["followers_change"] is not None]
        changes_v = [r["views_change"] for r in rows if r["views_change"] is not None]
        return {
            "channels": len(rows),
            "connected": sum(1 for r in rows if r["status"] == "connected"),
            "needs_attention": sum(1 for r in rows if r["status"] in {"needs_reauth", "error"}),
            **{name: _sum(r[name] for r in rows) for name in METRICS},
            "followers_change": sum(changes_f) if changes_f else None,
            "views_change": sum(changes_v) if changes_v else None,
            "videos_in_period": len(videos),
            "period_video_views": _sum(v.views for v in videos),
            "engagement_rate": _engagement_rate(videos),
        }

    totals = aggregate(channel_rows, ids)
    per_platform = []
    for name in PLATFORMS:
        rows = [r for r in channel_rows if r["platform"] == name]
        if platform and name != platform:
            continue
        per_platform.append({"platform": name, **aggregate(rows, [r["id"] for r in rows])})

    # -- time series (carry-forward per channel, summed) -------------------------
    series = []
    for date in dates:
        point: dict[str, Any] = {"date": date, "followers": 0, "views": 0, "by_platform": {}}
        has_data = False
        for channel in channels:
            snap = histories[channel.id].at(date)
            if snap is None:
                continue
            has_data = True
            bucket = point["by_platform"].setdefault(channel.platform, {"followers": 0, "views": 0})
            for metric in ("followers", "views"):
                value = getattr(snap, metric)
                if value is not None:
                    point[metric] += value
                    bucket[metric] += value
        point["has_data"] = has_data
        series.append(point)

    # -- top videos ---------------------------------------------------------------
    channel_by_id = {c.id: c for c in channels}
    all_videos = [v for cid in ids for v in videos_by_channel[cid]]
    period_videos = [v for v in all_videos if v.published_at and v.published_at.strftime("%Y-%m-%d") >= start]
    scope = "period"
    pool = period_videos
    if not pool:
        pool, scope = all_videos, "all_time"
    top = sorted(pool, key=lambda v: (v.views or 0, v.likes or 0), reverse=True)[:top_limit]

    # -- recent activity -----------------------------------------------------------
    activity: list[dict[str, Any]] = []
    for video in sorted(
        (v for v in all_videos if v.published_at), key=lambda v: v.published_at, reverse=True
    )[:20]:
        channel = channel_by_id[video.channel_id]
        activity.append(
            {
                "type": "video_published",
                "at": video.published_at,
                "channel_id": channel.id,
                "platform": channel.platform,
                "channel_name": channel.name,
                "title": video.title,
                "video_id": video.id,
                "url": video.url,
            }
        )
    for channel in channels:
        if channel.connected_at:
            activity.append(
                {
                    "type": "channel_connected" if channel.source == "oauth" else "channel_added",
                    "at": channel.connected_at,
                    "channel_id": channel.id,
                    "platform": channel.platform,
                    "channel_name": channel.name,
                    "title": channel.name,
                }
            )
        if channel.status in {"needs_reauth", "error"}:
            activity.append(
                {
                    "type": "channel_needs_reauth" if channel.status == "needs_reauth" else "sync_error",
                    "at": channel.updated_at,
                    "channel_id": channel.id,
                    "platform": channel.platform,
                    "channel_name": channel.name,
                    "title": channel.last_error,
                }
            )
    activity.sort(key=lambda item: item["at"] or datetime.min, reverse=True)

    return {
        "days": days,
        "start_date": start,
        "end_date": dates[-1],
        "generated_at": now,
        "totals": totals,
        "per_platform": per_platform,
        "channels": channel_rows,
        "series": series,
        "top_videos": [video_view(v, channel_by_id[v.channel_id]) for v in top],
        "top_videos_scope": scope,
        "recent_activity": activity[:30],
    }
