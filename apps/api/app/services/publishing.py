"""Auto-publishing calendar posts to Facebook Pages.

A post opts in with ``auto_publish``. A background loop (``start_publisher``) then:

* uploads the video early and lets **Facebook** publish it at ``scheduled_at``
  (``published=false`` + ``scheduled_publish_time``) when the time is far enough ahead,
  so the computer does not need to be on at that moment;
* uploads and publishes right away when the time has come (or was too close to schedule);
* checks remotely scheduled videos after their time and marks the post published.

``publish_state``: "" (off) | pending | uploading | remote_scheduled | published | failed.
Only Facebook Pages are supported; the Page must have granted ``pages_manage_posts``.

Graph API: https://developers.facebook.com/docs/video-api/guides/publishing
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from sqlalchemy.orm import Session

from .. import database
from ..models import Channel, MediaJob, ScheduledPost
from . import content_calendar as cc
from . import storage
from .channels.oauth import ensure_access_token, refresh_channel_token
from .channels.providers import get_provider
from .channels.providers.base import ChannelAuthError, ChannelProviderError
from .channels.providers.facebook import graph_version

logger = logging.getLogger(__name__)

PUBLISH_PLATFORMS = ("facebook",)
PUBLISH_SCOPE = "pages_manage_posts"
# Facebook accepts a scheduled time 10 minutes to months ahead; keep margins on both ends.
MIN_REMOTE_LEAD = timedelta(minutes=15)
MAX_REMOTE_LEAD = timedelta(days=29)
# Non-resumable Graph video uploads are limited to 1 GB.
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
LOOP_SECONDS = 60.0

ACTIVE_STATES = ("pending", "uploading", "remote_scheduled")


class PublishError(RuntimeError):
    """A problem the user has to fix (shown on the post)."""


def _upload_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, read=900.0, write=900.0), headers={"User-Agent": "VietDub-Studio/1.0"})


# Patched in tests.
client_factory: Callable[[], httpx.Client] = _upload_client
session_factory: Callable[[], Session] | None = None


def _session() -> Session:
    return (session_factory or database.SessionLocal)()


# --------------------------------------------------------------------------- checks


def video_file_for(db: Session, post: ScheduledPost) -> Path:
    if post.video_path:
        path = Path(post.video_path)
        if not path.is_file():
            raise PublishError(f"Không tìm thấy file video: {path}")
        return path
    if post.media_job_id:
        job = db.get(MediaJob, post.media_job_id)
        if job is None:
            raise PublishError("Dự án lồng tiếng đã bị xoá.")
        if not job.output_url:
            raise PublishError("Dự án lồng tiếng chưa render ra video.")
        relative = job.output_url.lstrip("/").removeprefix("storage/")
        path = storage.STORAGE_ROOT / relative
        if not path.is_file():
            raise PublishError(f"Không tìm thấy video đã render: {path.name}")
        return path
    raise PublishError("Bài đăng chưa có video: gắn dự án lồng tiếng hoặc chọn video trong máy.")


def publish_problem(db: Session, post: ScheduledPost) -> str | None:
    """Why this post cannot be auto-published, or None."""
    if post.platform not in PUBLISH_PLATFORMS:
        return "Tự đăng hiện chỉ hỗ trợ Facebook Page."
    channel = db.get(Channel, post.channel_id) if post.channel_id else None
    if channel is None:
        return "Chọn Page Facebook để đăng."
    if channel.source != "oauth" or channel.status == "disconnected":
        return "Page này chưa kết nối qua Facebook."
    if PUBLISH_SCOPE not in channel.scopes.replace(",", " ").split():
        return (
            "Page chưa cấp quyền đăng bài (pages_manage_posts). Thêm quyền này trong ứng dụng Meta "
            "và vào Kênh → Kết nối lại Page."
        )
    if post.scheduled_at is None:
        return "Chọn ngày giờ đăng."
    try:
        path = video_file_for(db, post)
    except PublishError as exc:
        return str(exc)
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        return "Video lớn hơn 1 GB, Facebook không nhận tải lên trực tiếp."
    return None


def description_for(post: ScheduledPost) -> str:
    caption = (post.caption or "").strip()
    tags = " ".join(cc.load_hashtags(post))
    return "\n\n".join(part for part in (caption, tags) if part)


# ------------------------------------------------------------------------ Graph calls


def _check(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"data": payload}
    get_provider("facebook").check_response(response.status_code, payload)
    return payload


def _with_token(db: Session, channel: Channel, call: Callable[[str], Any]) -> Any:
    provider = get_provider("facebook")
    token = ensure_access_token(db, channel, provider)
    try:
        return call(token)
    except ChannelAuthError:
        if not channel.refresh_token_encrypted:
            raise
        return call(refresh_channel_token(db, channel, provider))


def upload_video(token: str, page_id: str, path: Path, *, title: str, description: str, publish_at: datetime | None) -> str:
    """Upload to the Page; ``publish_at`` (naive UTC) schedules it on Facebook's side. Returns the video id."""
    data: dict[str, str] = {"title": title[:255], "description": description}
    if publish_at is not None:
        data["published"] = "false"
        data["scheduled_publish_time"] = str(int(publish_at.replace(tzinfo=timezone.utc).timestamp()))
    with client_factory() as client, path.open("rb") as handle:
        response = client.post(
            f"https://graph-video.facebook.com/{graph_version()}/{page_id}/videos",
            data=data,
            files={"source": (path.name, handle, "application/octet-stream")},
            headers={"Authorization": f"Bearer {token}"},
        )
    video_id = str(_check(response).get("id") or "")
    if not video_id:
        raise ChannelProviderError("Facebook: upload response missing the video id.")
    return video_id


def video_state(token: str, video_id: str) -> dict[str, Any]:
    with client_factory() as client:
        response = client.get(
            f"https://graph.facebook.com/{graph_version()}/{video_id}",
            params={"fields": "id,published,permalink_url,status"},
            headers={"Authorization": f"Bearer {token}"},
        )
    return _check(response)


def delete_video(token: str, video_id: str) -> None:
    with client_factory() as client:
        response = client.delete(
            f"https://graph.facebook.com/{graph_version()}/{video_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    _check(response)


def _permalink(state: dict[str, Any], video_id: str) -> str:
    link = str(state.get("permalink_url") or "")
    if link.startswith("/"):
        link = f"https://www.facebook.com{link}"
    return link or f"https://www.facebook.com/{video_id}"


# ------------------------------------------------------------------------ transitions


def _fail(post: ScheduledPost, message: str) -> None:
    post.publish_state = "failed"
    post.publish_error = message[:1000]
    post.status = "failed"
    post.updated_at = cc.utcnow()


def _mark_published(post: ScheduledPost, url: str, now: datetime) -> None:
    post.publish_state = "published"
    post.publish_error = ""
    post.status = "published"
    post.published_at = post.published_at or now
    post.published_url = url
    post.updated_at = now


def cancel_remote(db: Session, post: ScheduledPost, *, channel_id: str | None = None, remote_id: str | None = None) -> None:
    """Delete a video already handed to Facebook (scheduled, not yet live). Raises on failure.

    ``channel_id`` / ``remote_id`` default to the post's; pass the old ones when the post
    was just edited to point elsewhere.
    """
    remote_id = remote_id or post.remote_id
    channel_id = channel_id or post.channel_id
    if not remote_id:
        return
    channel = db.get(Channel, channel_id) if channel_id else None
    if channel is not None:
        _with_token(db, channel, lambda token: delete_video(token, remote_id))
    post.remote_id = ""
    post.publish_state = "pending" if post.auto_publish else ""


def process_post(db: Session, post: ScheduledPost, now: datetime | None = None) -> None:
    """Advance one auto-publish post. Never raises; failures are recorded on the post."""
    now = now or cc.utcnow()
    try:
        if post.publish_state == "remote_scheduled":
            _check_remote(db, post, now)
            return
        if post.publish_state != "pending" or post.scheduled_at is None or post.status != "scheduled":
            return
        lead = post.scheduled_at - now
        if lead > MAX_REMOTE_LEAD or timedelta(0) < lead < MIN_REMOTE_LEAD:
            return  # Too far ahead for Facebook's scheduler / too close to schedule remotely: wait.
        problem = publish_problem(db, post)
        if problem:
            _fail(post, problem)
            return
        channel = db.get(Channel, post.channel_id)
        path = video_file_for(db, post)
        publish_at = post.scheduled_at if lead >= MIN_REMOTE_LEAD else None
        post.publish_state = "uploading"
        post.publish_error = ""
        db.commit()
        video_id = _with_token(
            db,
            channel,
            lambda token: upload_video(
                token, channel.external_id or "", path, title=post.title, description=description_for(post), publish_at=publish_at
            ),
        )
        post.remote_id = video_id
        if publish_at is None:
            state = _with_token(db, channel, lambda token: video_state(token, video_id))
            _mark_published(post, _permalink(state, video_id), now)
        else:
            post.publish_state = "remote_scheduled"
            post.updated_at = now
    except (PublishError, ChannelProviderError, ChannelAuthError) as exc:
        _fail(post, str(exc))
    except Exception as exc:  # noqa: BLE001 - one bad post must not stop the loop
        logger.exception("Auto-publish failed for post %s", post.id)
        _fail(post, f"Lỗi không mong đợi: {type(exc).__name__}: {exc}")
    finally:
        db.commit()


def _check_remote(db: Session, post: ScheduledPost, now: datetime) -> None:
    if post.scheduled_at is None or post.scheduled_at > now:
        return
    channel = db.get(Channel, post.channel_id) if post.channel_id else None
    if channel is None:
        return
    state = _with_token(db, channel, lambda token: video_state(token, post.remote_id))
    if state.get("published"):
        _mark_published(post, _permalink(state, post.remote_id), now)


def run_due(db: Session, now: datetime | None = None) -> int:
    now = now or cc.utcnow()
    posts = (
        db.query(ScheduledPost)
        .filter(ScheduledPost.auto_publish.is_(True), ScheduledPost.publish_state.in_(("pending", "remote_scheduled")))
        .order_by(ScheduledPost.scheduled_at)
        .all()
    )
    for post in posts:
        process_post(db, post, now)
    return len(posts)


def recover_interrupted(db: Session) -> None:
    """An upload cut by a restart may or may not have reached Facebook: never retry blindly."""
    for post in db.query(ScheduledPost).filter(ScheduledPost.publish_state == "uploading"):
        _fail(post, "Tải lên bị gián đoạn (ứng dụng đã tắt). Kiểm tra Page trên Facebook rồi bấm Thử lại nếu video chưa có.")
    db.commit()


# ---------------------------------------------------------------------------- the loop

_wake = threading.Event()
_thread: threading.Thread | None = None
_run_lock = threading.Lock()


def wake() -> None:
    """Run the loop now (a post was just saved)."""
    _wake.set()


def _loop() -> None:
    while True:
        _wake.wait(LOOP_SECONDS)
        _wake.clear()
        try:
            with _run_lock, _session() as db:
                run_due(db)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-publish loop iteration failed")


def start_publisher() -> None:
    global _thread
    if _thread is not None:
        return
    with _session() as db:
        recover_interrupted(db)
    _thread = threading.Thread(target=_loop, name="auto-publish", daemon=True)
    _thread.start()
    wake()
