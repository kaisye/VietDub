"""Scans: search providers, upsert videos + snapshots, score, translate — in a background thread.

One worker thread drains a FIFO queue of scans (like channels sync). The worker
never raises: provider failures are recorded per platform on the scan status so a
broken source (e.g. Douyin) never fails the whole feature.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

from ... import database
from ...models import ViralSnapshot, ViralTopic, ViralVideo
from . import language as lang_mod
from . import llm, scoring
from . import settings as viral_settings
from .providers import (
    DouyinPublicProvider,
    SearchQuery,
    SourceError,
    VideoItem,
    douyin_hot_words,
    estimate_youtube_quota,
    parse_source_url,
    pick_provider,
    resolve_short_link,
    utcnow,
)

logger = logging.getLogger(__name__)

# Patched in tests; resolved at call time so the worker uses the configured DB.
session_factory: Callable[[], Session] | None = None

PLATFORMS = ("youtube", "douyin")
WINDOWS = ("24h", "7d", "30d", "90d")
DURATIONS = ("any", "short", "long")
ORDERS = ("views", "relevance", "date")
SOURCE_LANGUAGES = ("auto", "any", "en", "zh", "ja", "ko", "es", "other")
MAX_KEYWORDS_PER_PLATFORM = 3
SNAPSHOT_MERGE_SECONDS = 300  # re-scans closer than this update the last snapshot
TRANSLATE_BATCH = 25
TRANSLATE_MAX = 60
STOP_CODES = {"needs_key", "auth", "quota_exceeded", "payment_required", "unsupported"}

_lock = threading.Lock()
_queue: list[dict[str, Any]] = []
_scans: dict[str, dict[str, Any]] = {}
_thread: threading.Thread | None = None


def _session() -> Session:
    return (session_factory or database.SessionLocal)()


def _loads(value: str | None, default: Any) -> Any:
    try:
        data = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ------------------------------------------------------------------------- serialisers


def topic_view(topic: ViralTopic) -> dict[str, Any]:
    return {
        "id": topic.id,
        "name": topic.name,
        "kind": topic.kind,
        "platform": topic.platform,
        "source_url": topic.source_url,
        "keywords": _loads(topic.keywords_json, {}),
        "filters": _loads(topic.filters_json, {}),
        "watched": bool(topic.watched),
        "last_scanned_at": _iso(topic.last_scanned_at),
        "last_scan_count": topic.last_scan_count,
        "created_at": _iso(topic.created_at),
        "updated_at": _iso(topic.updated_at),
    }


def snapshot_view(row: ViralSnapshot) -> dict[str, Any]:
    return {
        "scanned_at": _iso(row.scanned_at),
        "views": row.views,
        "likes": row.likes,
        "comments": row.comments,
        "shares": row.shares,
        "collects": row.collects,
    }


def video_view(row: ViralVideo, *, detail: bool = False) -> dict[str, Any]:
    breakdown = _loads(row.breakdown_json, {})
    data = {
        "id": row.id,
        "platform": row.platform,
        "external_id": row.external_id,
        "url": row.url,
        "title": row.title,
        "title_vi": row.title_vi,
        "creator_name": row.creator_name,
        "creator_id": row.creator_id,
        "creator_url": row.creator_url,
        "followers": row.followers,
        "published_at": _iso(row.published_at),
        "duration": row.duration_seconds,
        "thumbnail_url": row.thumbnail_url,
        "language": row.language or None,
        "stats": {
            "views": row.views,
            "likes": row.likes,
            "comments": row.comments,
            "shares": row.shares,
            "collects": row.collects,
        },
        "score": row.score,
        "tier": row.tier,
        "views_per_hour": row.views_per_hour,
        "badges": breakdown.get("badges", []),
        "metrics": breakdown.get("metrics", {}),
        "dub": breakdown.get("dub", {}),
        "confidence": breakdown.get("confidence"),
        "topic_ids": _loads(row.topic_ids_json, []),
        "status": row.status,
        "watched": bool(row.watched),
        "media_job_id": row.media_job_id,
        "calendar_post_id": row.calendar_post_id,
        "has_analysis": bool(row.analysis_json),
        "source": row.source,
        "first_seen_at": _iso(row.first_seen_at),
        "last_scanned_at": _iso(row.last_scanned_at),
    }
    if detail:
        data["description"] = row.description
        data["breakdown"] = breakdown
        data["snapshots"] = [snapshot_view(s) for s in row.snapshots]
        data["analysis"] = _loads(row.analysis_json, {}) or None
        data["analysis_at"] = _iso(row.analysis_at)
    return data


# ---------------------------------------------------------------------------- persist


def upsert_items(db: Session, items: Iterable[VideoItem], *, topic_id: str | None = None, now: datetime | None = None) -> list[ViralVideo]:
    now = now or utcnow()
    rows: list[ViralVideo] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.platform, item.external_id)
        if not item.external_id or key in seen:
            continue
        seen.add(key)
        row = db.query(ViralVideo).filter_by(platform=item.platform, external_id=item.external_id).one_or_none()
        if row is None:
            row = ViralVideo(platform=item.platform, external_id=item.external_id, first_seen_at=now)
            db.add(row)
        for attr, value in (
            ("url", item.url),
            ("title", item.title),
            ("description", item.description),
            ("creator_name", item.creator_name),
            ("creator_id", item.creator_id),
            ("creator_url", item.creator_url),
            ("thumbnail_url", item.thumbnail),
            ("language", item.language or ""),
            ("source", item.source),
        ):
            if value:
                if attr == "title" and row.title and row.title != value:
                    row.title_vi = ""  # title changed: re-translate lazily
                setattr(row, attr, value)
        for attr, value in (
            ("followers", item.followers),
            ("published_at", item.published_at),
            ("duration_seconds", int(item.duration) if item.duration else None),
            ("views", item.views),
            ("likes", item.likes),
            ("comments", item.comments),
            ("shares", item.shares),
            ("collects", item.collects),
        ):
            if value is not None:
                setattr(row, attr, value)
        if topic_id:
            ids = _loads(row.topic_ids_json, [])
            if topic_id not in ids:
                ids.append(topic_id)
                row.topic_ids_json = json.dumps(ids)
        row.last_scanned_at = now
        row.updated_at = now
        db.flush()
        _record_snapshot(db, row, item, now)
        rows.append(row)
    return rows


def _record_snapshot(db: Session, row: ViralVideo, item: VideoItem, now: datetime) -> None:
    if all(v is None for v in (item.views, item.likes, item.comments, item.shares, item.collects)):
        return
    last = (
        db.query(ViralSnapshot).filter_by(video_id=row.id).order_by(ViralSnapshot.scanned_at.desc()).first()
    )
    if last is not None and (now - last.scanned_at).total_seconds() < SNAPSHOT_MERGE_SECONDS:
        snap = last
    else:
        snap = ViralSnapshot(video_id=row.id)
        db.add(snap)
    snap.scanned_at = now
    snap.views, snap.likes, snap.comments = item.views, item.likes, item.comments
    snap.shares, snap.collects = item.shares, item.collects


def _relevance_language(choice: str | None, platform: str) -> str:
    code = lang_mod.expected_language(choice, platform)
    return code if platform == "youtube" and code in lang_mod.FILTER_LANGUAGES else ""


def detect_languages(items: Iterable[VideoItem]) -> None:
    """Replace the platform's language hint with the detected source language."""
    for item in items:
        item.language = lang_mod.detect_language(item.title, item.description, item.language)


def metrics_for(row: ViralVideo, source_language: str | None = "auto") -> scoring.VideoMetrics:
    return scoring.VideoMetrics(
        expected_language=lang_mod.expected_language(source_language, row.platform),
        platform=row.platform,
        views=row.views,
        likes=row.likes,
        comments=row.comments,
        shares=row.shares,
        collects=row.collects,
        followers=row.followers,
        published_at=row.published_at,
        duration=row.duration_seconds,
        title=row.title or "",
        description=row.description or "",
        language=row.language or None,
    )


def rescore(
    db: Session,
    rows: list[ViralVideo],
    *,
    window: str | None = None,
    now: datetime | None = None,
    source_language: str | None = None,
) -> None:
    """Score rows together. ``source_language=None`` reuses each row's last choice."""
    now = now or utcnow()
    batch = []
    choices = []
    for row in rows:
        choice = source_language or _loads(row.breakdown_json, {}).get("source_language") or "auto"
        choices.append(choice)
        snaps = (
            db.query(ViralSnapshot).filter_by(video_id=row.id).order_by(ViralSnapshot.scanned_at).all()
        )
        # Douyin often hides plays: use the like-based estimate for momentum too.
        snapshots = [
            scoring.Snapshot(
                at=s.scanned_at,
                views=s.views or (int(s.likes * scoring.DOUYIN_LIKES_TO_VIEWS) if row.platform == "douyin" and s.likes else None),
            )
            for s in snaps
        ]
        batch.append((metrics_for(row, choice), snapshots))
    for row, choice, result in zip(rows, choices, scoring.score_batch(batch, now=now, window=window)):
        has_data = result.confidence > 0 and result.metrics.get("views") is not None
        row.score = result.score if has_data else None
        row.tier = result.tier if has_data else ""
        vph = result.metrics.get("views_per_hour")
        row.views_per_hour = int(vph) if vph is not None else None
        data = result.as_dict()
        data["window"] = window
        data["source_language"] = choice
        data["scored_at"] = now.isoformat()
        row.breakdown_json = json.dumps(data, ensure_ascii=False)


def translate_missing(db: Session, rows: list[ViralVideo], *, limit: int = TRANSLATE_MAX) -> int:
    """Best-effort LLM translation of titles (skips Vietnamese titles). Returns count."""
    pending = [
        r for r in rows
        if r.title and not r.title_vi and not _loads(r.breakdown_json, {}).get("dub", {}).get("vietnamese")
    ][:limit]
    if not pending or not llm.llm_status()["configured"]:
        return 0
    done = 0
    for start in range(0, len(pending), TRANSLATE_BATCH):
        chunk = pending[start : start + TRANSLATE_BATCH]
        mapping = {str(i): r.title for i, r in enumerate(chunk)}
        try:
            out = llm.run_sync(llm.translate_titles(mapping))
        except llm.LLMUnavailable as exc:
            logger.info("Title translation skipped: %s", exc.code)
            return done
        except Exception:  # never fail a scan on translation
            logger.exception("Title translation failed")
            return done
        for key, text in out.items():
            chunk[int(key)].title_vi = text[:1000]
            done += 1
        db.commit()
    return done


# ------------------------------------------------------------------------------- scans


def normalize_request(req: dict[str, Any], topic: ViralTopic | None = None) -> dict[str, Any]:
    settings = viral_settings.load_settings()
    filters = _loads(topic.filters_json, {}) if topic else {}
    merged = {**filters, **{k: v for k, v in req.items() if v not in (None, "", [])}}
    platforms = [p for p in (merged.get("platforms") or list(PLATFORMS)) if p in PLATFORMS] or list(PLATFORMS)
    window = merged.get("window") if merged.get("window") in WINDOWS else "7d"
    duration = merged.get("duration") if merged.get("duration") in DURATIONS else "any"
    order = merged.get("order") if merged.get("order") in ORDERS else "views"
    region = str(merged.get("region") or settings.get("default_region") or "").upper()[:2]
    limit = max(5, min(int(merged.get("limit") or 20), 50))
    min_views = max(0, int(merged.get("min_views") or 0))
    source_language = merged.get("source_language") if merged.get("source_language") in SOURCE_LANGUAGES else "auto"
    keywords: dict[str, list[str]] = {}
    given = req.get("keywords") or (_loads(topic.keywords_json, {}) if topic else {})
    query = str(req.get("query") or (topic.name if topic else "") or "").strip()
    for platform in platforms:
        words = [str(w).strip() for w in (given.get(platform) or []) if str(w).strip()]
        if not words and query:
            words = [query]
        keywords[platform] = words[:MAX_KEYWORDS_PER_PLATFORM]
    return {
        "topic_id": topic.id if topic else None,
        "topic_kind": topic.kind if topic else "keyword",
        "creator": {"platform": topic.platform, "url": topic.source_url} if topic and topic.kind == "creator" else None,
        "query": query,
        "platforms": platforms,
        "window": window,
        "duration": duration,
        "order": order,
        "region": region,
        "limit": limit,
        "min_views": min_views,
        "source_language": source_language,
        "keywords": keywords,
    }


def estimate(req: dict[str, Any]) -> dict[str, Any]:
    settings = viral_settings.load_settings()
    youtube = None
    if "youtube" in req["platforms"]:
        provider = pick_provider("youtube", settings)
        if provider.id == "youtube_api":
            youtube = estimate_youtube_quota(len(req["keywords"].get("youtube") or []), req["limit"])
    return {"youtube_quota_units": youtube, "youtube_quota_used_today": viral_settings.quota_used_today()}


def _new_scan(req: dict[str, Any], trigger: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "state": "queued",
        "trigger": trigger,
        "request": req,
        "phase": "queued",
        "progress": {"done": 0, "total": max(1, sum(len(v) or 1 for v in req["keywords"].values()))},
        "providers": {},
        "found": 0,
        "new": 0,
        "language_mismatched": 0,
        "video_ids": [],
        "douyin_hot": None,
        "estimate": estimate(req),
        "error": None,
        "created_at": utcnow().isoformat(),
        "started_at": None,
        "finished_at": None,
    }


def _creator_ident(url: str) -> tuple[str, str] | None:
    parsed = parse_source_url(url)
    if parsed and parsed["kind"] == "short_link":
        parsed = resolve_short_link(url)
    if not parsed or parsed["kind"] != "creator":
        return None
    return parsed["platform"], parsed["id"]


def run_scan(scan: dict[str, Any]) -> dict[str, Any]:
    """Execute one scan synchronously (the worker calls this; tests call it directly)."""
    req = scan["request"]
    scan.update(state="running", phase="searching", started_at=utcnow().isoformat())
    settings = viral_settings.load_settings()
    now = utcnow()
    collected: list[VideoItem] = []
    done = 0
    if req.get("creator"):
        ident = _creator_ident(req["creator"]["url"])
        platform = ident[0] if ident else (req["creator"].get("platform") or "youtube")
        provider = pick_provider(platform, settings)
        info = {"provider": provider.id, "label": provider.label, "ok": True, "count": 0, "error_code": None, "message": ""}
        scan["providers"][platform] = info
        try:
            if not ident:
                raise SourceError("bad_url", "Link kênh không hợp lệ.")
            items = provider.fetch_creator(ident[1], req["limit"])
            info["count"] = len(items)
            collected.extend(items)
        except SourceError as exc:
            info.update(ok=False, error_code=exc.code, message=exc.message)
        scan["progress"]["done"] = scan["progress"]["total"]
    else:
        for platform in req["platforms"]:
            provider = pick_provider(platform, settings)
            info = {"provider": provider.id, "label": provider.label, "ok": True, "count": 0, "error_code": None, "message": ""}
            scan["providers"][platform] = info
            for keyword in req["keywords"].get(platform) or []:
                q = SearchQuery(
                    keyword=keyword,
                    window=req["window"],
                    region=req["region"],
                    duration=req["duration"],
                    limit=req["limit"],
                    order=req["order"],
                    language=_relevance_language(req.get("source_language"), platform),
                )
                try:
                    items = provider.search(q)
                    info["count"] += len(items)
                    collected.extend(items)
                except SourceError as exc:
                    info.update(ok=info["count"] > 0, error_code=exc.code, message=exc.message)
                    if exc.code in STOP_CODES:
                        break
                except Exception as exc:  # defensive: provider bug
                    logger.exception("Viral provider %s crashed", provider.id)
                    info.update(ok=info["count"] > 0, error_code="provider_error", message=str(exc)[:300])
                finally:
                    done += 1
                    scan["progress"]["done"] = min(done, scan["progress"]["total"])
            if platform == "douyin" and isinstance(provider, DouyinPublicProvider):
                try:
                    scan["douyin_hot"] = douyin_hot_words(20)
                except SourceError as exc:
                    scan["douyin_hot"] = {"error_code": exc.code, "message": exc.message}

    scan["progress"]["done"] = scan["progress"]["total"]
    scan["phase"] = "scoring"
    detect_languages(collected)
    choice = req.get("source_language") or "auto"
    scan["language_mismatched"] = sum(
        1 for it in collected if not lang_mod.matches(it.language, lang_mod.expected_language(choice, it.platform))
    )
    min_views = req.get("min_views") or 0
    if min_views:
        collected = [
            it for it in collected
            if (scoring.effective_views(scoring.VideoMetrics(platform=it.platform, views=it.views, likes=it.likes))[0] or 0) >= min_views
        ]
    db = _session()
    try:
        before = {
            (p, e)
            for p, e in db.query(ViralVideo.platform, ViralVideo.external_id).filter(
                ViralVideo.external_id.in_([it.external_id for it in collected] or [""])
            )
        }
        rows = upsert_items(db, collected, topic_id=req.get("topic_id"), now=now)
        rescore(db, rows, window=req["window"], now=now, source_language=choice)
        if req.get("topic_id"):
            topic = db.get(ViralTopic, req["topic_id"])
            if topic is not None:
                topic.last_scanned_at = now
                topic.last_scan_count = len(rows)
        db.commit()
        scan["found"] = len(rows)
        scan["new"] = sum(1 for r in rows if (r.platform, r.external_id) not in before)
        scan["video_ids"] = [r.id for r in rows]
        if settings.get("translate_titles", True):
            scan["phase"] = "translating"
            translate_missing(db, rows)
    finally:
        db.close()
    errors = [p for p in scan["providers"].values() if not p["ok"]]
    scan.update(
        state="failed" if errors and len(errors) == len(scan["providers"]) and not collected else "done",
        phase="done",
        finished_at=utcnow().isoformat(),
    )
    if scan["state"] == "failed":
        scan["error"] = "; ".join(p["message"] for p in errors if p["message"])[:500]
    return scan


def refresh_videos(db: Session, rows: list[ViralVideo]) -> dict[str, Any]:
    """Re-fetch stats for stored videos (watch list / analyze). Returns per-platform errors."""
    settings = viral_settings.load_settings()
    errors: dict[str, dict[str, str]] = {}
    fresh: list[VideoItem] = []
    for platform in PLATFORMS:
        ids = [r.external_id for r in rows if r.platform == platform]
        if not ids:
            continue
        provider = pick_provider(platform, settings)
        try:
            fresh.extend(provider.fetch_videos(ids))
        except SourceError as exc:
            errors[platform] = {"code": exc.code, "message": exc.message}
    now = utcnow()
    detect_languages(fresh)
    updated = upsert_items(db, fresh, now=now)
    rescore(db, rows, now=now)
    db.commit()
    return {"updated": len(updated), "errors": errors}


def analyze_urls(db: Session, urls: list[str], *, watch: bool = True) -> dict[str, Any]:
    """Pasted links -> scored videos; creator links -> watched creator topics (+ scan)."""
    settings = viral_settings.load_settings()
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    creators: list[ViralTopic] = []
    wanted: dict[str, list[tuple[str, str]]] = {"youtube": [], "douyin": []}
    for url in urls:
        parsed = parse_source_url(url)
        if parsed and parsed["kind"] == "short_link":
            parsed = resolve_short_link(parsed["url"])
        if not parsed:
            errors.append({"url": url, "code": "bad_url", "message": "Link không phải video/kênh YouTube hoặc Douyin."})
            continue
        if parsed["kind"] == "creator":
            topic = db.query(ViralTopic).filter_by(kind="creator", source_url=parsed["url"]).one_or_none()
            if topic is None:
                topic = ViralTopic(
                    name=parsed["id"].split("/")[-1],
                    kind="creator",
                    platform=parsed["platform"],
                    source_url=parsed["url"],
                    filters_json=json.dumps({"platforms": [parsed["platform"]], "window": "90d", "limit": 20}),
                )
                db.add(topic)
                db.flush()
            creators.append(topic)
            continue
        wanted[parsed["platform"]].append((parsed["id"], url))
    fetched: list[VideoItem] = []
    for platform, pairs in wanted.items():
        if not pairs:
            continue
        provider = pick_provider(platform, settings)
        try:
            items = provider.fetch_videos([vid for vid, _ in pairs])
        except SourceError as exc:
            errors.extend({"url": url, "code": exc.code, "message": exc.message} for _, url in pairs)
            continue
        got = {it.external_id for it in items}
        for vid, url in pairs:
            if vid not in got:
                errors.append({"url": url, "code": "not_found", "message": "Không lấy được thông tin video."})
        fetched.extend(items)
    now = utcnow()
    detect_languages(fetched)
    rows = upsert_items(db, fetched, now=now)
    for row in rows:
        if watch:
            row.watched = True
    rescore(db, rows, now=now)
    db.commit()
    if settings.get("translate_titles", True):
        translate_missing(db, rows)
    results = [video_view(r) for r in rows]
    scans = []
    for topic in creators:
        scans.append(start_scan(normalize_request({}, topic), trigger="creator"))
    return {
        "videos": results,
        "creators": [topic_view(t) for t in creators],
        "scans": [{"id": s["id"], "topic_id": s["request"].get("topic_id")} for s in scans],
        "errors": errors,
    }


# ------------------------------------------------------------------------------ worker


def start_scan(req: dict[str, Any], *, trigger: str = "manual") -> dict[str, Any]:
    global _thread
    scan = _new_scan(req, trigger)
    with _lock:
        _scans[scan["id"]] = scan
        _queue.append(scan)
        # Keep the in-memory history bounded.
        for old in list(_scans)[:-50]:
            if _scans[old]["state"] in {"done", "failed"}:
                _scans.pop(old, None)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, name="viral-scan", daemon=True)
            _thread.start()
    return scan


def _worker() -> None:
    while True:
        with _lock:
            if not _queue:
                return
            scan = _queue.pop(0)
        try:
            run_scan(scan)
        except Exception as exc:  # never let the thread die silently mid-queue
            logger.exception("Viral scan worker error")
            scan.update(state="failed", phase="done", error=str(exc)[:500], finished_at=utcnow().isoformat())


def get_scan(scan_id: str) -> dict[str, Any] | None:
    with _lock:
        scan = _scans.get(scan_id)
        return _public_scan(scan) if scan else None


def list_scans(limit: int = 10) -> list[dict[str, Any]]:
    with _lock:
        return [_public_scan(s) for s in list(_scans.values())[-limit:]][::-1]


def _public_scan(scan: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in scan.items() if k != "video_ids"}
    out["video_count"] = len(scan.get("video_ids") or [])
    return json.loads(json.dumps(out, default=str))


def wait_for_idle(timeout: float = 10.0) -> bool:
    thread = _thread
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


def reset_for_tests() -> None:
    with _lock:
        _queue.clear()
        _scans.clear()


# --------------------------------------------------------------------------- scheduler

_scheduler: threading.Thread | None = None
_scheduler_state: dict[str, Any] = {"last_run_at": None}
scheduler_allowed = True  # tests switch the thread off
SCHEDULER_TICK_SECONDS = 60


def ensure_scheduler() -> None:
    """Start the (idle-by-default) periodic re-scan thread once."""
    global _scheduler
    if not scheduler_allowed:
        return
    with _lock:
        if _scheduler is not None and _scheduler.is_alive():
            return
        _scheduler = threading.Thread(target=_scheduler_loop, name="viral-scheduler", daemon=True)
        _scheduler.start()


def scheduler_status(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or viral_settings.load_settings()
    last = _scheduler_state.get("last_run_at")
    hours = int(settings.get("auto_rescan_hours") or 6)
    next_run = (last + timedelta(hours=hours)) if (last and settings.get("auto_rescan_enabled")) else None
    return {
        "enabled": bool(settings.get("auto_rescan_enabled")),
        "interval_hours": hours,
        "last_run_at": _iso(last),
        "next_run_at": _iso(next_run),
    }


def run_periodic_once(now: datetime | None = None) -> int:
    """Queue scans for watched topics and refresh watched videos. Returns scans queued."""
    now = now or utcnow()
    _scheduler_state["last_run_at"] = now
    db = _session()
    queued = 0
    try:
        for topic in db.query(ViralTopic).filter(ViralTopic.watched.is_(True)).all():
            start_scan(normalize_request({}, topic), trigger="schedule")
            queued += 1
        watched = db.query(ViralVideo).filter(ViralVideo.watched.is_(True), ViralVideo.status != "dismissed").limit(100).all()
        if watched:
            refresh_videos(db, watched)
    except Exception:
        logger.exception("Periodic viral re-scan failed")
    finally:
        db.close()
    return queued


def _scheduler_loop() -> None:
    while True:
        time.sleep(SCHEDULER_TICK_SECONDS)
        try:
            settings = viral_settings.load_settings()
            if not settings.get("auto_rescan_enabled"):
                continue
            last = _scheduler_state.get("last_run_at")
            hours = int(settings.get("auto_rescan_hours") or 6)
            if last is None or utcnow() - last >= timedelta(hours=hours):
                run_periodic_once()
        except Exception:
            logger.exception("Viral scheduler tick failed")
