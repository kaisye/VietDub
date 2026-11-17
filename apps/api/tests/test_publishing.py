from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models import Channel, ScheduledPost
from app.services import publishing
from app.services.channels import crypto

from test_calendar import cal  # noqa: F401 - fixture
from test_channels_support import channels_env  # noqa: F401 - fixture

NOW = datetime(2026, 11, 17, 3, 0, 0)


@pytest.fixture
def pub(cal, monkeypatch, tmp_path):  # noqa: F811
    mock = cal["mock"]
    monkeypatch.setattr(publishing, "client_factory", lambda: httpx.Client(transport=httpx.MockTransport(mock)))
    monkeypatch.setattr(publishing.cc, "utcnow", lambda: NOW)
    with cal["Session"]() as db:
        page = Channel(
            platform="facebook", name="VietNote", source="oauth", status="connected", external_id="1234",
            access_token_encrypted=crypto.encrypt_secret("page-token"),
            scopes="pages_show_list pages_read_engagement pages_manage_posts",
        )
        db.add(page)
        db.commit()
        page_id = page.id
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-bytes")
    return {**cal, "page_id": page_id, "video": video}


def _create(pub, **extra):
    body = {
        "title": "Bài A", "caption": "Nội dung", "hashtags": "#a #b", "platform": "facebook",
        "channel_id": pub["page_id"], "video_path": str(pub["video"]), "auto_publish": True, **extra,
    }
    return pub["cal"].post("/api/calendar/posts", json=body)


def _run(pub, now=NOW):
    with pub["Session"]() as db:
        publishing.run_due(db, now)
        return db.query(ScheduledPost).one()


def test_far_ahead_posts_are_scheduled_on_facebook(pub):
    mock = pub["mock"]
    mock.on("POST", "https://graph-video.facebook.com/v26.0/1234/videos", {"id": "vid1"})
    post = _create(pub, scheduled_at="2026-11-17T05:00:00Z")
    assert post.status_code == 201, post.text
    assert post.json()["publish_state"] == "pending" and post.json()["status"] == "scheduled"

    saved = _run(pub)
    assert (saved.publish_state, saved.remote_id) == ("remote_scheduled", "vid1")
    upload = mock.find("POST", "/1234/videos")[0]
    body = upload.read().decode("utf-8", "replace")
    assert 'name="published"' in body and 'name="scheduled_publish_time"' in body
    assert str(int(datetime(2026, 11, 17, 5, 0, tzinfo=timezone.utc).timestamp())) in body
    assert "Nội dung\r\n\r\n#a #b" in body or "Nội dung\n\n#a #b" in body
    assert upload.headers["authorization"] == "Bearer page-token"

    # Not live yet: nothing changes. After the time, Facebook reports it published.
    mock.on("GET", "https://graph.facebook.com/v26.0/vid1", {"id": "vid1", "published": False})
    assert _run(pub, NOW + timedelta(hours=3)).publish_state == "remote_scheduled"
    mock.on("GET", "https://graph.facebook.com/v26.0/vid1", {"id": "vid1", "published": True, "permalink_url": "/reel/vid1"})
    done = _run(pub, NOW + timedelta(hours=3))
    assert (done.publish_state, done.status, done.published_url) == ("published", "published", "https://www.facebook.com/reel/vid1")


def test_due_posts_are_published_right_away(pub):
    mock = pub["mock"]
    mock.on("POST", "https://graph-video.facebook.com/v26.0/1234/videos", {"id": "vid2"})
    mock.on("GET", "https://graph.facebook.com/v26.0/vid2", {"id": "vid2", "published": True, "permalink_url": "https://fb.watch/x"})
    _create(pub, scheduled_at="2026-11-17T02:50:00Z")
    done = _run(pub)
    assert (done.publish_state, done.status, done.published_url) == ("published", "published", "https://fb.watch/x")
    assert 'name="published"' not in mock.find("POST", "/1234/videos")[0].read().decode("utf-8", "replace")


def test_too_close_to_schedule_remotely_waits_for_the_time(pub):
    _create(pub, scheduled_at="2026-11-17T03:05:00Z")
    assert _run(pub).publish_state == "pending"
    assert not pub["mock"].find("POST", "/videos")


def test_editing_a_remotely_scheduled_post_replaces_the_facebook_copy(pub):
    mock = pub["mock"]
    mock.on("POST", "https://graph-video.facebook.com/v26.0/1234/videos", {"id": "vid1"})
    mock.on("DELETE", "https://graph.facebook.com/v26.0/vid1", {"success": True})
    post = _create(pub, scheduled_at="2026-11-17T05:00:00Z").json()
    _run(pub)
    client = pub["cal"]
    assert client.patch(f"/api/calendar/posts/{post['id']}", json={"notes": "only a note"}).json()["publish_state"] == "remote_scheduled"
    assert not mock.find("DELETE", "vid1")
    edited = client.patch(f"/api/calendar/posts/{post['id']}", json={"caption": "Nội dung mới"}).json()
    assert (edited["publish_state"], edited["remote_id"]) == ("pending", "")
    assert len(mock.find("DELETE", "vid1")) == 1

    _run(pub)
    assert client.delete(f"/api/calendar/posts/{post['id']}").status_code == 204
    assert len(mock.find("DELETE", "vid1")) == 2


def test_cannot_turn_on_without_what_publishing_needs(pub):
    assert "Page" in _create(pub, scheduled_at="2026-11-17T05:00:00Z", channel_id=None).json()["detail"]
    assert "video" in _create(pub, scheduled_at="2026-11-17T05:00:00Z", video_path="").json()["detail"]
    assert "Facebook" in _create(pub, scheduled_at="2026-11-17T05:00:00Z", platform="tiktok", channel_id=None).json()["detail"]
    with pub["Session"]() as db:
        page = db.get(Channel, pub["page_id"])
        page.scopes = "pages_show_list"
        db.commit()
    missing = _create(pub, scheduled_at="2026-11-17T05:00:00Z")
    assert missing.status_code == 400 and "pages_manage_posts" in missing.json()["detail"]


def test_facebook_errors_fail_the_post_and_retry_puts_it_back(pub):
    mock = pub["mock"]
    mock.on("POST", "https://graph-video.facebook.com/v26.0/1234/videos", {"error": {"code": 100, "message": "Invalid video"}}, status=400)
    post = _create(pub, scheduled_at="2026-11-17T02:50:00Z").json()
    failed = _run(pub)
    assert (failed.publish_state, failed.status) == ("failed", "failed") and "Invalid video" in failed.publish_error
    retry = pub["cal"].patch(f"/api/calendar/posts/{post['id']}", json={"auto_publish": True}).json()
    assert (retry["publish_state"], retry["status"], retry["publish_error"]) == ("pending", "scheduled", "")


def test_an_upload_cut_by_a_restart_is_not_retried_blindly(pub):
    post = _create(pub, scheduled_at="2026-11-17T05:00:00Z").json()
    with pub["Session"]() as db:
        db.get(ScheduledPost, post["id"]).publish_state = "uploading"
        db.commit()
        publishing.recover_interrupted(db)
        assert db.get(ScheduledPost, post["id"]).publish_state == "failed"


def test_cancelling_a_post_stops_publishing_and_removes_the_facebook_copy(pub):
    mock = pub["mock"]
    mock.on("POST", "https://graph-video.facebook.com/v26.0/1234/videos", {"id": "vid1"})
    mock.on("DELETE", "https://graph.facebook.com/v26.0/vid1", {"success": True})
    post = _create(pub, scheduled_at="2026-11-17T05:00:00Z").json()
    _run(pub)
    cancelled = pub["cal"].patch(f"/api/calendar/posts/{post['id']}", json={"status": "cancelled"}).json()
    assert (cancelled["auto_publish"], cancelled["publish_state"], cancelled["remote_id"]) == (False, "", "")
    assert len(mock.find("DELETE", "vid1")) == 1
    _run(pub, NOW + timedelta(hours=3))
    assert len(mock.find("POST", "/videos")) == 1
