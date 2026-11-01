"""Manual channels: create without OAuth, enter metrics by hand or import CSV."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models import Channel
from . import PLATFORMS
from .providers.base import Stats, VideoItem, parse_iso_datetime, utcnow
from .sync import upsert_snapshot, upsert_videos

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = 20000

SNAPSHOT_ALIASES = {
    "date": {"date", "day", "ngay", "ngày"},
    "followers": {"followers", "subscribers", "subs", "fans", "follower_count", "subscriber_count", "nguoi_theo_doi"},
    "views": {"views", "view_count", "total_views", "luot_xem", "lượt_xem"},
    "video_count": {"video_count", "videos", "posts", "so_video"},
    "likes": {"likes", "like_count", "reactions"},
    "comments": {"comments", "comment_count", "binh_luan"},
    "shares": {"shares", "share_count"},
}
VIDEO_ALIASES = {
    "external_id": {"external_id", "id", "video_id", "post_id"},
    "title": {"title", "name", "caption", "message", "tieu_de"},
    "published_at": {"published_at", "date", "published", "created_time", "publish_date"},
    "views": {"views", "view_count"},
    "likes": {"likes", "like_count", "reactions"},
    "comments": {"comments", "comment_count"},
    "shares": {"shares", "share_count"},
    "duration_seconds": {"duration_seconds", "duration", "length"},
    "thumbnail_url": {"thumbnail_url", "thumbnail", "cover", "cover_image_url"},
    "url": {"url", "link", "permalink", "share_url"},
}


class ManualInputError(ValueError):
    pass


def create_manual_channel(db: Session, *, platform: str, name: str, url: str = "", handle: str = "",
                          avatar_url: str = "", metrics: dict[str, Any] | None = None) -> Channel:
    if platform not in PLATFORMS:
        raise ManualInputError(f"Unknown platform '{platform}'.")
    if not name.strip():
        raise ManualInputError("Channel name is required.")
    if url and not url.strip().startswith(("http://", "https://")):
        raise ManualInputError("Channel URL must start with http:// or https://")
    now = utcnow()
    channel = Channel(
        platform=platform,
        source="manual",
        external_id=None,
        name=name.strip()[:500],
        url=url.strip(),
        handle=handle.strip(),
        avatar_url=avatar_url.strip(),
        status="manual",
        connected_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(channel)
    db.flush()
    if metrics and any(v is not None for v in metrics.values()):
        date = normalize_date(metrics["date"]) if metrics.get("date") else None
        upsert_snapshot(db, channel, _stats(metrics), source="manual", date=date, now=now)
        channel.last_synced_at = now
    db.commit()
    return channel


def _stats(values: dict[str, Any]) -> Stats:
    return Stats(**{k: parse_number(values.get(k)) for k in ("followers", "views", "video_count", "likes", "comments", "shares")})


def add_manual_snapshot(db: Session, channel: Channel, values: dict[str, Any]) -> None:
    date = normalize_date(values.get("date")) if values.get("date") else None
    upsert_snapshot(db, channel, _stats(values), source="manual", date=date)
    channel.last_synced_at = utcnow()
    channel.updated_at = utcnow()
    db.commit()


def add_manual_video(db: Session, channel: Channel, values: dict[str, Any]):
    item = _video_from_row(values, fallback_id=f"manual-{int(utcnow().timestamp() * 1000)}")
    rows = upsert_videos(db, channel, [item], source="manual")
    db.commit()
    return rows[0]


_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "tr": 1_000_000, "n": 1_000}


def parse_number(value: Any) -> int | None:
    """Accept 1234, "1,234", "1.234", "1.2K", "3,4M", "2 tr" (Vietnamese "triệu")."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace(" ", "").replace(" ", "")
    if not text or text in {"-", "n/a", "na"}:
        return None
    text = text.replace("triệu", "tr").replace("nghìn", "n").replace("ngàn", "n")
    match = re.fullmatch(r"([0-9][0-9.,]*)(k|m|b|tr|n)?", text)
    if not match:
        raise ManualInputError(f"Not a number: {value!r}")
    number, suffix = match.groups()
    if suffix:
        number = number.replace(",", ".")
        if number.count(".") > 1:
            raise ManualInputError(f"Not a number: {value!r}")
        return int(round(float(number) * _SUFFIX[suffix]))
    # Without suffix, "," and "." are thousands separators (metrics are integers).
    return int(number.replace(",", "").replace(".", ""))


def normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    parsed = parse_iso_datetime(text)
    if parsed:
        return parsed.strftime("%Y-%m-%d")
    raise ManualInputError(f"Unrecognised date: {value!r} (use YYYY-MM-DD)")


def _canonical(header: str, aliases: dict[str, set[str]]) -> str | None:
    key = header.strip().lower().replace(" ", "_").replace("-", "_")
    for canonical, names in aliases.items():
        if key == canonical or key in names:
            return canonical
    return None


def _read_csv(content: bytes | str) -> tuple[list[str], list[dict[str, str]]]:
    if isinstance(content, bytes):
        if len(content) > MAX_CSV_BYTES:
            raise ManualInputError("CSV file is too large (max 5 MB).")
        content = content.decode("utf-8-sig", errors="replace")
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    rows = []
    for index, row in enumerate(reader):
        if index >= MAX_CSV_ROWS:
            raise ManualInputError(f"CSV has too many rows (max {MAX_CSV_ROWS}).")
        rows.append(row)
    return list(reader.fieldnames or []), rows


def import_csv(db: Session, channel: Channel, content: bytes | str, kind: str = "snapshots") -> dict[str, Any]:
    """Import metrics. kind="snapshots": one row per day; kind="videos": one row per video/post.

    Returns {"imported": n, "skipped": n, "errors": [{"row": i, "error": "..."}]} (row numbers
    are 1-based data rows, header excluded). Valid rows are kept even if others fail.
    """
    if kind not in {"snapshots", "videos"}:
        raise ManualInputError("kind must be 'snapshots' or 'videos'.")
    aliases = SNAPSHOT_ALIASES if kind == "snapshots" else VIDEO_ALIASES
    headers, rows = _read_csv(content)
    mapping = {h: _canonical(h, aliases) for h in headers}
    present = {v for v in mapping.values() if v}
    if kind == "snapshots" and "date" not in present:
        raise ManualInputError("CSV needs a 'date' column (YYYY-MM-DD).")
    if kind == "snapshots" and not present - {"date"}:
        raise ManualInputError("CSV needs at least one metric column (followers, views, video_count, likes, comments, shares).")
    if kind == "videos" and not present & {"external_id", "title", "url"}:
        raise ManualInputError("Video CSV needs an 'id', 'title' or 'url' column.")
    imported = 0
    errors: list[dict[str, Any]] = []
    source = "csv"
    videos: list[VideoItem] = []
    for number, raw in enumerate(rows, start=1):
        values = {mapping[k]: v for k, v in raw.items() if k in mapping and mapping[k]}
        if not any(str(v or "").strip() for v in values.values()):
            continue
        try:
            if kind == "snapshots":
                date = normalize_date(values.get("date"))
                upsert_snapshot(db, channel, _stats(values), source=source, date=date)
            else:
                videos.append(_video_from_row(values, fallback_id=f"csv-{number}"))
            imported += 1
        except ManualInputError as exc:
            errors.append({"row": number, "error": str(exc)})
    if videos:
        upsert_videos(db, channel, videos, source=source)
    if imported:
        channel.last_synced_at = utcnow()
        channel.updated_at = utcnow()
    db.commit()
    return {"imported": imported, "skipped": len(errors), "errors": errors[:100]}


def _video_from_row(values: dict[str, Any], fallback_id: str) -> VideoItem:
    url = str(values.get("url") or "").strip()
    external_id = str(values.get("external_id") or "").strip() or url or fallback_id
    published = values.get("published_at")
    published_at = None
    if published:
        published_at = parse_iso_datetime(published) or datetime.strptime(normalize_date(published), "%Y-%m-%d")
    duration = values.get("duration_seconds")
    return VideoItem(
        external_id=external_id[:255],
        title=str(values.get("title") or "").strip()[:1000],
        published_at=published_at,
        views=parse_number(values.get("views")),
        likes=parse_number(values.get("likes")),
        comments=parse_number(values.get("comments")),
        shares=parse_number(values.get("shares")),
        duration_seconds=_parse_duration(duration),
        thumbnail_url=str(values.get("thumbnail_url") or "").strip(),
        url=url,
        kind=str(values.get("kind") or "video"),
    )


def _parse_duration(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if ":" in text:  # mm:ss or hh:mm:ss
        parts = [int(p) for p in text.split(":") if p.isdigit()]
        total = 0
        for part in parts:
            total = total * 60 + part
        return total
    try:
        return int(float(text))
    except ValueError:
        raise ManualInputError(f"Bad duration: {value!r}") from None
