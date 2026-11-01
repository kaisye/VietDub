"""Multi-channel API: OAuth connections (YouTube / Facebook Pages / TikTok), manual
channels, sync and the dashboard. Contract: see CONTRACT_channels.md / docs/channels-oauth.md.
"""

from __future__ import annotations

import html
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .database import get_db
from .models import Channel, ChannelSnapshot, ChannelVideo, MediaJob
from .services.channels import PLATFORMS, credentials, dashboard, manual, oauth, sync
from .services.channels.providers import PROVIDERS
from .services.channels.providers.base import utcnow

router = APIRouter(prefix="/api/channels", tags=["channels"])

Platform = Literal["youtube", "facebook", "tiktok"]
PLATFORM_LABELS = {"youtube": "YouTube", "facebook": "Facebook Pages", "tiktok": "TikTok"}


# -- Schemas ---------------------------------------------------------------------------
class CredentialsPatch(BaseModel):
    client_id: str | None = None
    client_secret: str | None = Field(default=None, description="Write-only. Blank/omitted keeps the stored secret.")
    clear_secret: bool = False
    scopes: str | None = Field(default=None, description="Advanced: override default scopes (blank = defaults).")


class SettingsPatch(BaseModel):
    redirect_base_url: str = ""


class Metrics(BaseModel):
    date: str | None = None
    followers: int | str | None = None
    views: int | str | None = None
    video_count: int | str | None = None
    likes: int | str | None = None
    comments: int | str | None = None
    shares: int | str | None = None


class ManualChannelCreate(BaseModel):
    platform: Platform
    name: str = Field(min_length=1, max_length=500)
    url: str = ""
    handle: str = ""
    avatar_url: str = ""
    metrics: Metrics | None = None


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, max_length=500)
    handle: str | None = None
    url: str | None = None
    avatar_url: str | None = None


class SyncRequest(BaseModel):
    channel_ids: list[str] | None = None
    only_stale_hours: float | None = Field(default=None, ge=0)


class ManualVideoCreate(BaseModel):
    external_id: str | None = None
    title: str = ""
    published_at: str | None = None
    views: int | str | None = None
    likes: int | str | None = None
    comments: int | str | None = None
    shares: int | str | None = None
    duration_seconds: int | str | None = None
    thumbnail_url: str = ""
    url: str = ""
    kind: Literal["video", "short", "post"] = "video"


class VideoLinkPatch(BaseModel):
    media_job_id: str | None = None


# -- Serialisers -----------------------------------------------------------------------
def _latest_snapshot(db: Session, channel_id: str) -> ChannelSnapshot | None:
    return (
        db.query(ChannelSnapshot)
        .filter(ChannelSnapshot.channel_id == channel_id)
        .order_by(ChannelSnapshot.date.desc())
        .first()
    )


def snapshot_view(row: ChannelSnapshot) -> dict[str, Any]:
    return {
        "date": row.date,
        "followers": row.followers,
        "views": row.views,
        "video_count": row.video_count,
        "likes": row.likes,
        "comments": row.comments,
        "shares": row.shares,
        "source": row.source,
    }


def channel_view(db: Session, channel: Channel) -> dict[str, Any]:
    latest = _latest_snapshot(db, channel.id)
    return {
        "id": channel.id,
        "platform": channel.platform,
        "source": channel.source,
        "external_id": channel.external_id,
        "name": channel.name,
        "handle": channel.handle,
        "url": channel.url,
        "avatar_url": channel.avatar_url,
        "status": channel.status,
        "last_error": channel.last_error or None,
        "scopes": channel.scopes.split() if channel.scopes else [],
        "token_expires_at": channel.token_expires_at,
        "connected_at": channel.connected_at,
        "last_synced_at": channel.last_synced_at,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
        "latest": snapshot_view(latest) if latest else None,
        "video_total": db.query(ChannelVideo).filter(ChannelVideo.channel_id == channel.id).count(),
    }


def _get_channel(db: Session, channel_id: str) -> Channel:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found.")
    return channel


def _platform_or_404(platform: str) -> str:
    if platform not in PLATFORMS:
        raise HTTPException(status_code=404, detail=f"Unknown platform '{platform}'.")
    return platform


def _settings_view(request: Request) -> dict[str, Any]:
    base = str(request.base_url)
    configured = credentials.get_redirect_base_url()
    platforms = []
    for name in PLATFORMS:
        provider_cls = PROVIDERS[name]
        creds = credentials.get_credentials(name)
        platforms.append(
            {
                **credentials.public_view(name),
                "label": PLATFORM_LABELS[name],
                "redirect_uri": oauth.redirect_uri_for(name, base),
                "default_scopes": list(provider_cls.default_scopes),
                "scopes": provider_cls().scopes(creds),
                "docs_url": provider_cls.docs_url,
            }
        )
    return {
        "redirect_base_url": configured,
        "platforms": platforms,
    }


# -- Settings / credentials ------------------------------------------------------------
@router.get("/settings")
def get_channel_settings(request: Request) -> dict[str, Any]:
    return _settings_view(request)


@router.put("/settings")
def put_channel_settings(payload: SettingsPatch, request: Request) -> dict[str, Any]:
    try:
        credentials.set_redirect_base_url(payload.redirect_base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _settings_view(request)


@router.put("/credentials/{platform}")
def put_credentials(platform: str, payload: CredentialsPatch, request: Request) -> dict[str, Any]:
    _platform_or_404(platform)
    credentials.update_credentials(
        platform,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
        clear_secret=payload.clear_secret,
        scopes=payload.scopes,
    )
    view = _settings_view(request)
    return next(p for p in view["platforms"] if p["platform"] == platform)


# -- OAuth -----------------------------------------------------------------------------
@router.post("/oauth/{platform}/start")
def oauth_start(platform: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _platform_or_404(platform)
    try:
        return oauth.start_authorization(db, platform, str(request.base_url))
    except oauth.ChannelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/oauth/status/{state}")
def oauth_status(state: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = oauth.get_state(db, state)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown OAuth state.")
    view = oauth.state_view(row)
    view["channels"] = [
        channel_view(db, channel)
        for channel_id in view["channel_ids"]
        if (channel := db.get(Channel, channel_id)) is not None
    ]
    return view


@router.get("/oauth/{platform}/callback", response_class=HTMLResponse)
def oauth_callback(
    platform: str,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    error_reason: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if platform not in PLATFORMS:
        return _callback_page(False, "Nền tảng không hợp lệ.", status=404)
    try:
        row = oauth.complete_authorization(
            db,
            platform,
            state=state,
            code=code,
            error=error,
            error_description=error_description or error_reason,
        )
    except oauth.OAuthStateError as exc:
        return _callback_page(False, str(exc), status=400)
    if row.status != "connected":
        return _callback_page(False, row.error or "Kết nối thất bại.", status=400)
    view = oauth.state_view(row)
    try:
        sync.start_sync(view["channel_ids"])
    except Exception:  # sync is best-effort; the connection itself succeeded
        pass
    names = [c.name for cid in view["channel_ids"] if (c := db.get(Channel, cid)) is not None]
    return _callback_page(True, ", ".join(names))


def _callback_page(ok: bool, detail: str, status: int = 200) -> HTMLResponse:
    title = "Đã kết nối, bạn có thể đóng tab này" if ok else "Kết nối không thành công"
    subtitle = (
        "Connected — you can close this tab and return to VietDub."
        if ok
        else "Connection failed — return to VietDub and try again."
    )
    color = "#2563eb" if ok else "#dc2626"
    icon = "✓" if ok else "!"
    body = f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VietDub — {html.escape(title)}</title>
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f8ff;color:#0f172a}}
.card{{background:#fff;border:1px solid #dbe5ff;border-radius:16px;padding:40px 48px;max-width:440px;
text-align:center;box-shadow:0 10px 30px rgba(37,99,235,.08)}}
.icon{{width:56px;height:56px;border-radius:50%;margin:0 auto 16px;display:flex;align-items:center;
justify-content:center;font-size:28px;color:#fff;background:{color}}}
h1{{font-size:20px;margin:0 0 8px}} p{{margin:4px 0;color:#475569;font-size:14px}}
.detail{{margin-top:12px;color:#0f172a;font-weight:500;word-break:break-word}}
</style></head>
<body><div class="card"><div class="icon">{icon}</div><h1>{html.escape(title)}</h1>
<p>{html.escape(subtitle)}</p>{f'<p class="detail">{html.escape(detail)}</p>' if detail else ''}</div>
{'<script>setTimeout(function(){try{window.close()}catch(e){}},2500)</script>' if ok else ''}
</body></html>"""
    return HTMLResponse(content=body, status_code=status, headers={"Cache-Control": "no-store"})


# -- Collection-level routes (declared before /{channel_id}) ----------------------------
@router.get("")
def list_channels(platform: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    query = db.query(Channel)
    if platform:
        query = query.filter(Channel.platform == _platform_or_404(platform))
    return [channel_view(db, c) for c in query.order_by(Channel.created_at).all()]


@router.post("/manual", status_code=201)
def create_manual(payload: ManualChannelCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        channel = manual.create_manual_channel(
            db,
            platform=payload.platform,
            name=payload.name,
            url=payload.url,
            handle=payload.handle,
            avatar_url=payload.avatar_url,
            metrics=payload.metrics.model_dump() if payload.metrics else None,
        )
    except manual.ManualInputError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return channel_view(db, channel)


@router.get("/dashboard")
def get_dashboard(
    days: int = Query(28, ge=1, le=365),
    platform: str | None = None,
    include_disconnected: bool = False,
    top_limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if platform:
        _platform_or_404(platform)
    return dashboard.build_dashboard(
        db, days=days, platform=platform, include_disconnected=include_disconnected, top_limit=top_limit
    )


@router.post("/sync", status_code=202)
def trigger_sync(payload: SyncRequest | None = None) -> dict[str, Any]:
    payload = payload or SyncRequest()
    return sync.start_sync(payload.channel_ids, only_stale_hours=payload.only_stale_hours)


@router.get("/sync/status")
def get_sync_status() -> dict[str, Any]:
    return sync.sync_status()


@router.get("/jobs/{media_job_id}/videos")
def videos_for_job(media_job_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.query(ChannelVideo).filter(ChannelVideo.media_job_id == media_job_id).all()
    return [dashboard.video_view(v) for v in rows]


@router.patch("/videos/{video_id}")
def link_video(video_id: str, payload: VideoLinkPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    video = db.get(ChannelVideo, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    if payload.media_job_id and db.get(MediaJob, payload.media_job_id) is None:
        raise HTTPException(status_code=404, detail="Media job not found.")
    video.media_job_id = payload.media_job_id or None
    video.updated_at = utcnow()
    db.commit()
    return dashboard.video_view(video)


# -- Single channel ----------------------------------------------------------------------
@router.get("/{channel_id}")
def get_channel(channel_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return channel_view(db, _get_channel(db, channel_id))


@router.patch("/{channel_id}")
def patch_channel(channel_id: str, payload: ChannelPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    channel = _get_channel(db, channel_id)
    values = payload.model_dump(exclude_unset=True)
    if "url" in values and values["url"] and not str(values["url"]).startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Channel URL must start with http:// or https://")
    for key, value in values.items():
        if value is not None:
            setattr(channel, key, str(value).strip())
    channel.updated_at = utcnow()
    db.commit()
    return channel_view(db, channel)


@router.post("/{channel_id}/disconnect")
def disconnect(channel_id: str, revoke: bool = True, db: Session = Depends(get_db)) -> dict[str, Any]:
    channel = _get_channel(db, channel_id)
    if channel.source != "oauth":
        raise HTTPException(status_code=400, detail="Manual channels have no connection; delete them instead.")
    result = oauth.disconnect_channel(db, channel, revoke=revoke)
    return {**result, "channel": channel_view(db, channel)}


@router.delete("/{channel_id}")
def delete_channel(channel_id: str, revoke: bool = True, db: Session = Depends(get_db)) -> dict[str, Any]:
    channel = _get_channel(db, channel_id)
    result = {"revoked": False, "revoke_error": None}
    if channel.source == "oauth":
        result = oauth.disconnect_channel(db, channel, revoke=revoke)
    db.delete(channel)
    db.commit()
    return {"deleted": True, **result}


@router.post("/{channel_id}/sync", status_code=202)
def sync_one(channel_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    channel = _get_channel(db, channel_id)
    if channel.source != "oauth":
        raise HTTPException(status_code=400, detail="Manual channels cannot be synced; add metrics or import CSV.")
    if channel.status == "disconnected":
        raise HTTPException(status_code=409, detail="Channel is disconnected; reconnect it first.")
    return sync.start_sync([channel.id])


@router.get("/{channel_id}/snapshots")
def list_snapshots(
    channel_id: str, days: int = Query(90, ge=1, le=3650), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    _get_channel(db, channel_id)
    start = (utcnow() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = (
        db.query(ChannelSnapshot)
        .filter(ChannelSnapshot.channel_id == channel_id, ChannelSnapshot.date >= start)
        .order_by(ChannelSnapshot.date)
        .all()
    )
    return [snapshot_view(r) for r in rows]


@router.post("/{channel_id}/snapshots")
def add_snapshot(channel_id: str, payload: Metrics, db: Session = Depends(get_db)) -> dict[str, Any]:
    channel = _get_channel(db, channel_id)
    try:
        manual.add_manual_snapshot(db, channel, payload.model_dump())
    except manual.ManualInputError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return channel_view(db, channel)


@router.get("/{channel_id}/videos")
def list_videos(
    channel_id: str,
    limit: int = Query(50, ge=1, le=500),
    sort: Literal["published_at", "views", "likes", "comments"] = "published_at",
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    channel = _get_channel(db, channel_id)
    column = getattr(ChannelVideo, sort)
    rows = (
        db.query(ChannelVideo)
        .filter(ChannelVideo.channel_id == channel_id)
        .order_by(column.is_(None), column.desc())
        .limit(limit)
        .all()
    )
    return [dashboard.video_view(v, channel) for v in rows]


@router.post("/{channel_id}/videos", status_code=201)
def add_video(channel_id: str, payload: ManualVideoCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    channel = _get_channel(db, channel_id)
    try:
        video = manual.add_manual_video(db, channel, payload.model_dump())
    except manual.ManualInputError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return dashboard.video_view(video, channel)


@router.post("/{channel_id}/import")
async def import_metrics(
    channel_id: str,
    file: UploadFile = File(...),
    kind: Literal["snapshots", "videos"] = Form("snapshots"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    channel = _get_channel(db, channel_id)
    content = await file.read(manual.MAX_CSV_BYTES + 1)
    try:
        result = manual.import_csv(db, channel, content, kind=kind)
    except manual.ManualInputError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {**result, "channel": channel_view(db, channel)}
