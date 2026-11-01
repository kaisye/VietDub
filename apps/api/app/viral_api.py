"""Viral discovery API (prefix /api/viral). Contract: scratchpad CONTRACT_viral.md / docs/viral-discovery.md."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .database import get_db
from .models import ScheduledPost, ViralTopic, ViralVideo
from .services.viral import language as lang_mod
from .services.viral import llm, scan
from .services.viral import settings as viral_settings
from .services.viral.providers import SourceError, availability_report, douyin_hot_words, utcnow


def _scheduler_dependency() -> None:
    scan.ensure_scheduler()


router = APIRouter(prefix="/api/viral", tags=["viral"], dependencies=[Depends(_scheduler_dependency)])

PlatformName = Literal["youtube", "douyin"]
Window = Literal["24h", "7d", "30d", "90d"]
Duration = Literal["any", "short", "long"]
Order = Literal["views", "relevance", "date"]
SourceLanguage = Literal["auto", "any", "en", "zh", "ja", "ko", "es", "other"]


# -- Schemas ---------------------------------------------------------------------------
class Filters(BaseModel):
    platforms: list[PlatformName] | None = None
    window: Window | None = None
    region: str | None = Field(default=None, max_length=2)
    duration: Duration | None = None
    order: Order | None = None
    limit: int | None = Field(default=None, ge=5, le=50)
    min_views: int | None = Field(default=None, ge=0)
    source_language: SourceLanguage | None = None


class TopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    keywords: dict[str, list[str]] = Field(default_factory=dict)
    filters: Filters | None = None
    watched: bool = True


class TopicPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    keywords: dict[str, list[str]] | None = None
    filters: Filters | None = None
    watched: bool | None = None


class ExpandRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)


class ScanRequest(Filters):
    topic_id: str | None = None
    query: str | None = Field(default=None, max_length=200)
    keywords: dict[str, list[str]] | None = None


class AnalyzeRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)
    watch: bool = True


class VideoPatch(BaseModel):
    status: Literal["new", "saved", "dismissed", "dubbed"] | None = None
    watched: bool | None = None
    media_job_id: str | None = None
    title_vi: str | None = Field(default=None, max_length=1000)


class RefreshRequest(BaseModel):
    video_ids: list[str] | None = None  # default: whole watch list


class IdeaRequest(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class SettingsPatch(BaseModel):
    youtube_api_key: str | None = None
    youtube_mode: Literal["auto", "api", "ytdlp"] | None = None
    default_region: str | None = None
    douyin_mode: Literal["public", "tikhub", "off"] | None = None
    tikhub_api_key: str | None = None
    tikhub_base_url: str | None = None
    translate_titles: bool | None = None
    auto_rescan_enabled: bool | None = None
    auto_rescan_hours: int | None = Field(default=None, ge=1, le=168)


# -- Helpers ---------------------------------------------------------------------------
def _topic_or_404(db: Session, topic_id: str) -> ViralTopic:
    topic = db.get(ViralTopic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail={"code": "topic_not_found", "message": "Không tìm thấy chủ đề."})
    return topic


def _video_or_404(db: Session, video_id: str) -> ViralVideo:
    video = db.get(ViralVideo, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail={"code": "video_not_found", "message": "Không tìm thấy video."})
    return video


def _clean_keywords(keywords: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for platform in scan.PLATFORMS:
        words: list[str] = []
        for word in keywords.get(platform) or []:
            text = " ".join(str(word).split())[:80]
            if text and text not in words:
                words.append(text)
        out[platform] = words[:8]
    return out


def _settings_view() -> dict[str, Any]:
    current = viral_settings.load_settings()
    return {
        **viral_settings.public_settings(current),
        "providers": availability_report(current),
        "llm": llm.llm_status(),
        "scheduler": scan.scheduler_status(current),
    }


# -- Settings / availability -----------------------------------------------------------
@router.get("/settings")
def get_settings() -> dict[str, Any]:
    return _settings_view()


@router.put("/settings")
def put_settings(payload: SettingsPatch) -> dict[str, Any]:
    try:
        viral_settings.update_settings(payload.model_dump(exclude_none=True))
    except viral_settings.SettingsError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_settings", "message": str(exc)}) from exc
    return _settings_view()


@router.get("/providers")
def get_providers() -> dict[str, Any]:
    return {"providers": availability_report(), "llm": llm.llm_status()}


@router.get("/douyin/hot")
def get_douyin_hot(limit: int = Query(default=30, ge=1, le=50)) -> dict[str, Any]:
    try:
        return {"words": douyin_hot_words(limit), "fetched_at": utcnow().isoformat()}
    except SourceError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message}) from exc


# -- Topics ----------------------------------------------------------------------------
@router.post("/expand-keywords")
async def expand_keywords(payload: ExpandRequest) -> dict[str, Any]:
    return await llm.expand_keywords(payload.topic)


@router.get("/topics")
def list_topics(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.query(ViralTopic).order_by(ViralTopic.created_at).all()
    return [scan.topic_view(t) for t in rows]


@router.post("/topics", status_code=201)
def create_topic(payload: TopicCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    now = utcnow()
    topic = ViralTopic(
        name=payload.name.strip(),
        keywords_json=json.dumps(_clean_keywords(payload.keywords), ensure_ascii=False),
        filters_json=json.dumps(payload.filters.model_dump(exclude_none=True) if payload.filters else {}),
        watched=payload.watched,
        created_at=now,
        updated_at=now,
    )
    db.add(topic)
    db.commit()
    return scan.topic_view(topic)


@router.patch("/topics/{topic_id}")
def patch_topic(topic_id: str, payload: TopicPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    topic = _topic_or_404(db, topic_id)
    if payload.name is not None:
        topic.name = payload.name.strip()
    if payload.keywords is not None:
        topic.keywords_json = json.dumps(_clean_keywords(payload.keywords), ensure_ascii=False)
    if payload.filters is not None:
        topic.filters_json = json.dumps(payload.filters.model_dump(exclude_none=True))
    if payload.watched is not None:
        topic.watched = payload.watched
    topic.updated_at = utcnow()
    db.commit()
    return scan.topic_view(topic)


@router.delete("/topics/{topic_id}", status_code=204)
def delete_topic(topic_id: str, db: Session = Depends(get_db)) -> None:
    topic = _topic_or_404(db, topic_id)
    db.delete(topic)
    db.commit()


# -- Scans -----------------------------------------------------------------------------
@router.post("/scan", status_code=202)
def start_scan(payload: ScanRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    topic = _topic_or_404(db, payload.topic_id) if payload.topic_id else None
    data = payload.model_dump(exclude_none=True)
    data.pop("topic_id", None)
    req = scan.normalize_request(data, topic)
    if not req.get("creator") and not any(req["keywords"].values()):
        raise HTTPException(status_code=422, detail={"code": "no_keywords", "message": "Nhập chủ đề hoặc từ khóa để quét."})
    if topic is not None and payload.keywords is not None:
        topic.keywords_json = json.dumps(_clean_keywords(payload.keywords), ensure_ascii=False)
        db.commit()
    return scan.get_scan(scan.start_scan(req)["id"]) or {}


@router.get("/scans")
def list_scans(limit: int = Query(default=10, ge=1, le=50)) -> list[dict[str, Any]]:
    return scan.list_scans(limit)


@router.get("/scans/{scan_id}")
def get_scan(scan_id: str) -> dict[str, Any]:
    data = scan.get_scan(scan_id)
    if data is None:
        raise HTTPException(status_code=404, detail={"code": "scan_not_found", "message": "Không tìm thấy lượt quét."})
    return data


@router.post("/analyze")
def analyze(payload: AnalyzeRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return scan.analyze_urls(db, payload.urls, watch=payload.watch)


# -- Videos ----------------------------------------------------------------------------
SORTS = {
    "score": (ViralVideo.score.desc(),),
    "velocity": (ViralVideo.views_per_hour.desc(),),
    "views": (ViralVideo.views.desc(),),
    "newest": (ViralVideo.published_at.desc(),),
    "recent": (ViralVideo.last_scanned_at.desc(),),
}


def _language_clause(choice: str):
    """Rows whose detected language matches the choice (unknown language is kept)."""
    clauses = []
    for platform in scan.PLATFORMS:
        expected = lang_mod.expected_language(choice, platform)
        if expected is None:
            clauses.append(ViralVideo.platform == platform)
        elif expected == "other":
            clauses.append(and_(ViralVideo.platform == platform, ViralVideo.language.notin_(lang_mod.FILTER_LANGUAGES)))
        else:
            clauses.append(
                and_(ViralVideo.platform == platform, or_(ViralVideo.language == expected, ViralVideo.language == ""))
            )
    return or_(*clauses)


@router.get("/videos")
def list_videos(
    topic_id: str | None = None,
    scan_id: str | None = None,
    platform: PlatformName | None = None,
    status: str | None = Query(default=None, description="new|saved|dismissed|dubbed|watched|all (default: not dismissed)"),
    sort: Literal["score", "velocity", "views", "newest", "recent"] = "score",
    window: Window | None = None,
    min_score: int | None = Query(default=None, ge=0, le=100),
    min_views: int | None = Query(default=None, ge=0),
    duration: Duration | None = None,
    q: str | None = None,
    source_language: SourceLanguage | None = Query(
        default=None, description="auto = English for YouTube, Chinese for Douyin; any/omitted = no filter"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=24, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = db.query(ViralVideo)
    if scan_id:
        data = scan._scans.get(scan_id)
        query = query.filter(ViralVideo.id.in_((data or {}).get("video_ids") or [""]))
    if topic_id:
        query = query.filter(ViralVideo.topic_ids_json.like(f'%"{topic_id}"%'))
    if platform:
        query = query.filter(ViralVideo.platform == platform)
    if status == "watched":
        query = query.filter(ViralVideo.watched.is_(True))
    elif status and status != "all":
        query = query.filter(ViralVideo.status == status)
    elif not status:
        query = query.filter(ViralVideo.status != "dismissed")
    if window:
        cutoff = utcnow() - timedelta(hours=scan.scoring.WINDOW_HOURS[window])
        query = query.filter(or_(ViralVideo.published_at.is_(None), ViralVideo.published_at >= cutoff))
    if min_score is not None:
        query = query.filter(ViralVideo.score >= min_score)
    if min_views:
        query = query.filter(ViralVideo.views >= min_views)
    if duration == "short":
        query = query.filter(ViralVideo.duration_seconds <= scan.scoring.SHORT_MAX_SECONDS)
    elif duration == "long":
        query = query.filter(ViralVideo.duration_seconds > scan.scoring.SHORT_MAX_SECONDS)
    if source_language and source_language != "any":
        query = query.filter(_language_clause(source_language))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(ViralVideo.title.like(like), ViralVideo.title_vi.like(like), ViralVideo.creator_name.like(like)))
    total = query.count()
    order = SORTS[sort]
    rows = query.order_by(*(o.nulls_last() for o in order), ViralVideo.last_scanned_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "offset": offset, "limit": limit, "items": [scan.video_view(r) for r in rows]}


@router.get("/top")
def top_videos(
    limit: int = Query(default=3, ge=1, le=12),
    hours: int = Query(default=72, ge=1, le=24 * 30),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Dashboard card: best recent finds from saved topics / watch list."""
    cutoff = utcnow() - timedelta(hours=hours)
    rows = (
        db.query(ViralVideo)
        .filter(ViralVideo.status != "dismissed", ViralVideo.last_scanned_at >= cutoff, ViralVideo.score.isnot(None))
        .order_by(ViralVideo.score.desc())
        .limit(limit)
        .all()
    )
    return {"items": [scan.video_view(r) for r in rows], "topics": db.query(ViralTopic).count()}


@router.get("/videos/{video_id}")
def get_video(video_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return scan.video_view(_video_or_404(db, video_id), detail=True)


@router.patch("/videos/{video_id}")
def patch_video(video_id: str, payload: VideoPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    video = _video_or_404(db, video_id)
    fields = payload.model_dump(exclude_unset=True)
    for key, value in fields.items():
        if key == "title_vi":
            video.title_vi = (value or "").strip()
        elif value is not None or key == "media_job_id":
            setattr(video, key, value)
    if fields.get("media_job_id"):
        video.status = "dubbed"
    video.updated_at = utcnow()
    db.commit()
    return scan.video_view(video, detail=True)


@router.post("/videos/refresh")
def refresh(payload: RefreshRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    query = db.query(ViralVideo)
    if payload and payload.video_ids:
        query = query.filter(ViralVideo.id.in_(payload.video_ids))
    else:
        query = query.filter(ViralVideo.watched.is_(True), ViralVideo.status != "dismissed")
    rows = query.limit(100).all()
    result = scan.refresh_videos(db, rows)
    return {**result, "items": [scan.video_view(r) for r in rows]}


@router.post("/videos/{video_id}/translate")
async def translate_video(video_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    video = _video_or_404(db, video_id)
    try:
        out = await llm.translate_titles({"0": video.title})
    except llm.LLMUnavailable as exc:
        raise HTTPException(status_code=400 if exc.code == "not_configured" else 502, detail={"code": exc.code, "message": exc.message}) from exc
    if out.get("0"):
        video.title_vi = out["0"][:1000]
        db.commit()
    return scan.video_view(video, detail=True)


@router.post("/videos/{video_id}/analysis")
async def analyze_video(video_id: str, refresh: bool = False, db: Session = Depends(get_db)) -> dict[str, Any]:
    video = _video_or_404(db, video_id)
    if video.analysis_json and not refresh:
        return {"cached": True, "analysis": json.loads(video.analysis_json), "analysis_at": scan._iso(video.analysis_at)}
    view = scan.video_view(video, detail=True)
    try:
        result = await llm.analyze_video(view)
    except llm.LLMUnavailable as exc:
        raise HTTPException(status_code=400 if exc.code == "not_configured" else 502, detail={"code": exc.code, "message": exc.message}) from exc
    status = llm.llm_status()
    result["provider"], result["model"] = status["provider"], status["model"]
    video.analysis_json = json.dumps(result, ensure_ascii=False)
    video.analysis_at = utcnow()
    if result.get("title_vi") and not video.title_vi:
        video.title_vi = result["title_vi"][:1000]
    db.commit()
    return {"cached": False, "analysis": result, "analysis_at": scan._iso(video.analysis_at)}


@router.post("/videos/{video_id}/idea", status_code=201)
def save_idea(video_id: str, payload: IdeaRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Save the video as an idea in the content calendar (ScheduledPost status=idea)."""
    video = _video_or_404(db, video_id)
    payload = payload or IdeaRequest()
    analysis = json.loads(video.analysis_json) if video.analysis_json else {}
    title = (payload.title or analysis.get("title_vi") or video.title_vi or video.title or "Ý tưởng video viral")[:500]
    notes = payload.notes
    if notes is None:
        lines = [f"Nguồn ({video.platform}): {video.url}"]
        if video.title_vi and video.title:
            lines.append(f"Tiêu đề gốc: {video.title}")
        if video.score is not None:
            lines.append(f"Điểm viral: {video.score}/100")
        if analysis.get("angle"):
            lines.append(f"Góc khai thác: {analysis['angle']}")
        notes = "\n".join(lines)
    now = utcnow()
    post = ScheduledPost(title=title, status="idea", notes=notes, label="Viral", created_at=now, updated_at=now)
    db.add(post)
    db.flush()
    video.calendar_post_id = post.id
    if video.status == "new":
        video.status = "saved"
    db.commit()
    return {"post_id": post.id, "title": post.title, "video": scan.video_view(video)}
