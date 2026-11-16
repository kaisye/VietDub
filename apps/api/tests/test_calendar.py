from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.calendar_api import router as calendar_router
from app.models import Channel, ChannelVideo, MediaJob, ScheduledPost
from app.services import content_calendar as cc
from app.services.channels import sync
from test_channels_support import channels_env, connect, mock_youtube  # noqa: F401  (fixture import)


@pytest.fixture
def cal(channels_env):
    """Calendar router on the isolated channels DB (channels client kept for sync tests)."""
    app = FastAPI()
    app.include_router(calendar_router)
    Session = channels_env["Session"]

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[database.get_db] = override_db
    return {**channels_env, "cal": TestClient(app)}


def _manual_channel(env, platform="youtube", name="Kênh A") -> str:
    res = env["client"].post("/api/channels/manual", json={"platform": platform, "name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _job(env, title="Video gốc") -> str:
    with env["Session"]() as db:
        job = MediaJob(video_url="https://youtu.be/abc", source_title=title)
        db.add(job)
        db.commit()
        return job.id


# -- Pure helpers ------------------------------------------------------------------------------
def test_normalize_hashtags():
    assert cc.normalize_hashtags("#review, phim  #Review lồng_tiếng") == ["#review", "#phim", "#lồng_tiếng"]
    assert cc.normalize_hashtags(["a", "#b", "", "  "]) == ["#a", "#b"]
    assert cc.normalize_hashtags(None) == []


def test_title_similarity():
    assert cc.title_similarity("Phim lồng tiếng tập 1", "phim long tieng tap 1") == 1.0
    assert cc.title_similarity("Review iPhone 17", "Review iPhone 17 Pro sau 30 ngày #shorts") >= 0.6
    assert cc.title_similarity("Nấu phở bò", "Lắp PC gaming") < 0.3
    assert cc.title_similarity("", "abc") == 0.0


# -- CRUD ----------------------------------------------------------------------------------------
def test_create_list_update_delete(cal):
    client = cal["cal"]
    channel_id = _manual_channel(cal)
    job_id = _job(cal)
    created = client.post(
        "/api/calendar/posts",
        json={
            "title": "  Review iPhone  ",
            "channel_id": channel_id,
            "media_job_id": job_id,
            "scheduled_at": "2026-11-02T19:00:00+07:00",
            "hashtags": "#review iphone",
            "caption": "Mô tả",
            "color": "violet",
        },
    )
    assert created.status_code == 201, created.text
    post = created.json()
    assert post["title"] == "Review iPhone"
    assert post["status"] == "scheduled"  # has a date -> scheduled by default
    assert post["platform"] == "youtube"  # taken from the channel
    assert post["scheduled_at"] == "2026-11-02T12:00:00"  # converted to naive UTC
    assert post["hashtags"] == ["#review", "#iphone"]
    assert post["channel"]["name"] == "Kênh A"
    assert post["media_job"]["title"] == "Video gốc"

    idea = client.post("/api/calendar/posts", json={"title": "Ý tưởng", "platform": "tiktok"}).json()
    assert idea["status"] == "idea" and idea["scheduled_at"] is None

    in_range = client.get("/api/calendar/posts", params={"start": "2026-11-01T00:00:00Z", "end": "2026-11-08T00:00:00Z"}).json()
    assert [p["id"] for p in in_range] == [post["id"]]
    assert [p["id"] for p in client.get("/api/calendar/posts", params={"unscheduled": "true"}).json()] == [idea["id"]]
    assert [p["id"] for p in client.get("/api/calendar/posts", params={"platform": "tiktok"}).json()] == [idea["id"]]
    assert [p["id"] for p in client.get("/api/calendar/posts", params={"status": "idea,draft"}).json()] == [idea["id"]]
    assert [p["id"] for p in client.get("/api/calendar/posts", params={"channel_id": channel_id}).json()] == [post["id"]]
    assert len(client.get("/api/calendar/posts").json()) == 2

    # Reschedule (drag & drop) keeps everything else.
    moved = client.patch(f"/api/calendar/posts/{post['id']}", json={"scheduled_at": "2026-11-04T12:00:00"}).json()
    assert moved["scheduled_at"] == "2026-11-04T12:00:00" and moved["status"] == "scheduled"
    assert moved["hashtags"] == ["#review", "#iphone"]

    # Dropping an idea onto the calendar turns it into a draft.
    dropped = client.patch(f"/api/calendar/posts/{idea['id']}", json={"scheduled_at": "2026-11-05T02:00:00Z"}).json()
    assert dropped["status"] == "draft" and dropped["scheduled_at"] == "2026-11-05T02:00:00"
    # ...and back to the backlog.
    back = client.patch(f"/api/calendar/posts/{idea['id']}", json={"scheduled_at": None, "status": "idea"}).json()
    assert back["status"] == "idea" and back["scheduled_at"] is None

    # Unlink job and channel.
    unlinked = client.patch(f"/api/calendar/posts/{post['id']}", json={"media_job_id": None, "channel_id": None}).json()
    assert unlinked["media_job"] is None and unlinked["channel"] is None and unlinked["platform"] == "youtube"

    assert client.delete(f"/api/calendar/posts/{post['id']}").status_code == 204
    assert client.get(f"/api/calendar/posts/{post['id']}").status_code == 404
    assert client.delete(f"/api/calendar/posts/{post['id']}").status_code == 404


def test_validation_errors(cal):
    client = cal["cal"]
    tiktok = _manual_channel(cal, "tiktok", "TT")
    assert client.post("/api/calendar/posts", json={"title": "   "}).status_code == 422
    assert client.post("/api/calendar/posts", json={"title": "x", "platform": "myspace"}).status_code == 422
    assert client.post("/api/calendar/posts", json={"title": "x", "status": "maybe"}).status_code == 422
    assert client.post("/api/calendar/posts", json={"title": "x", "color": "orange"}).status_code == 422
    assert client.post("/api/calendar/posts", json={"title": "x", "scheduled_at": "mai"}).status_code == 400
    assert client.post("/api/calendar/posts", json={"title": "x", "status": "scheduled"}).status_code == 400
    assert client.post("/api/calendar/posts", json={"title": "x", "channel_id": "nope"}).status_code == 404
    assert client.post("/api/calendar/posts", json={"title": "x", "media_job_id": "nope"}).status_code == 404
    assert (
        client.post("/api/calendar/posts", json={"title": "x", "platform": "youtube", "channel_id": tiktok}).status_code
        == 400
    )
    assert client.post("/api/calendar/posts", json={"title": "x", "published_url": "javascript:alert(1)"}).status_code == 400
    assert client.get("/api/calendar/posts", params={"status": "bogus"}).status_code == 400
    assert client.get("/api/calendar/posts", params={"platform": "bogus"}).status_code == 400
    assert client.get("/api/calendar/posts", params={"start": "2026-11-02", "end": "2026-11-01"}).status_code == 400
    assert client.get("/api/calendar/posts", params={"start": "2026-01-01", "end": "2028-01-01"}).status_code == 400
    assert client.patch("/api/calendar/posts/missing", json={"title": "y"}).status_code == 404


def test_switching_platform_drops_foreign_channel(cal):
    client = cal["cal"]
    yt = _manual_channel(cal, "youtube", "YT")
    post = client.post("/api/calendar/posts", json={"title": "x", "channel_id": yt}).json()
    switched = client.patch(f"/api/calendar/posts/{post['id']}", json={"platform": "facebook"}).json()
    assert switched["platform"] == "facebook" and switched["channel_id"] is None


def test_duplicate_and_publish(cal):
    client = cal["cal"]
    channel_id = _manual_channel(cal)
    post = client.post(
        "/api/calendar/posts",
        json={"title": "Bài A", "channel_id": channel_id, "scheduled_at": "2026-11-02T12:00:00", "notes": "n"},
    ).json()
    published = client.post(
        f"/api/calendar/posts/{post['id']}/publish", json={"published_url": "https://youtu.be/xyz"}
    ).json()
    assert published["status"] == "published" and published["published_url"] == "https://youtu.be/xyz"
    assert published["published_at"]
    assert client.post(f"/api/calendar/posts/{post['id']}/publish", json={"published_url": "ftp://x"}).status_code == 400

    copy = client.post(f"/api/calendar/posts/{post['id']}/duplicate").json()
    assert copy["id"] != post["id"] and copy["status"] == "draft"  # published -> draft copy
    assert copy["published_url"] == "" and copy["notes"] == "n" and copy["channel_id"] == channel_id
    moved_copy = client.post(f"/api/calendar/posts/{post['id']}/duplicate", json={"scheduled_at": "2026-11-09T12:00:00"}).json()
    assert moved_copy["scheduled_at"] == "2026-11-09T12:00:00"

    # Link a ChannelVideo when marking published.
    video = cal["client"].post(
        f"/api/channels/{channel_id}/videos",
        json={"title": "Bài A", "published_at": "2026-11-09", "url": "https://youtu.be/v1"},
    ).json()
    linked = client.post(f"/api/calendar/posts/{moved_copy['id']}/publish", json={"channel_video_id": video["id"]}).json()
    assert linked["channel_video"]["id"] == video["id"] and linked["published_url"] == "https://youtu.be/v1"
    assert client.post(f"/api/calendar/posts/{copy['id']}/publish", json={"channel_video_id": "nope"}).status_code == 404

    candidates = client.get(f"/api/calendar/posts/{copy['id']}/video-candidates").json()
    assert candidates[0]["id"] == video["id"] and candidates[0]["score"] == 1.0

    # Reverting to draft unlinks the video.
    reverted = client.patch(f"/api/calendar/posts/{moved_copy['id']}", json={"status": "draft"}).json()
    assert reverted["status"] == "draft" and reverted["channel_video_id"] is None and reverted["published_at"] is None


def test_upcoming_and_suggestions(cal):
    client = cal["cal"]
    now = cc.utcnow()
    for i, status in enumerate(["scheduled", "draft", "published", "cancelled", "scheduled"]):
        client.post(
            "/api/calendar/posts",
            json={"title": f"P{i}", "status": status, "scheduled_at": (now + timedelta(days=i + 1)).isoformat()},
        )
    client.post("/api/calendar/posts", json={"title": "old", "scheduled_at": (now - timedelta(days=3)).isoformat()})
    upcoming = client.get("/api/calendar/upcoming", params={"limit": 5}).json()
    assert [p["title"] for p in upcoming] == ["P0", "P1", "P4"]

    channel_id = _manual_channel(cal)
    with cal["Session"]() as db:
        for d in range(8):  # 13:00 UTC = 20:00 at UTC+7 performs best
            db.add(ChannelVideo(channel_id=channel_id, external_id=f"a{d}", title="x",
                                published_at=datetime(2026, 9, 1 + d, 13), views=10000))
            db.add(ChannelVideo(channel_id=channel_id, external_id=f"b{d}", title="y",
                                published_at=datetime(2026, 9, 1 + d, 3), views=100))
        db.commit()
    res = client.get("/api/calendar/suggestions", params={"channel_id": channel_id, "tz_offset_minutes": 420})
    assert res.status_code == 200
    data = res.json()
    assert data["basis"] == "history" and data["hours"][0] == 20
    assert data["slots"] and all(datetime.fromisoformat(s["scheduled_at"]) > now for s in data["slots"])
    assert client.get("/api/calendar/suggestions", params={"platform": "tiktok"}).json()["basis"] == "default"
    assert client.get("/api/calendar/suggestions", params={"channel_id": "nope"}).status_code == 404


def test_deleted_channel_is_reported_as_null(cal):
    client = cal["cal"]
    channel_id = _manual_channel(cal)
    post = client.post("/api/calendar/posts", json={"title": "x", "channel_id": channel_id}).json()
    assert cal["client"].delete(f"/api/channels/{channel_id}").status_code == 200
    again = client.get(f"/api/calendar/posts/{post['id']}").json()
    assert again["channel"] is None and again["channel_id"] is None and again["platform"] == "youtube"


# -- Auto-match after channel sync ------------------------------------------------------------
def test_match_synced_videos_unit(cal):
    channel_id = _manual_channel(cal)
    with cal["Session"]() as db:
        near = ScheduledPost(title="Phim lồng tiếng tập 1", channel_id=channel_id, platform="youtube",
                             status="scheduled", scheduled_at=datetime(2026, 10, 21, 12))
        far = ScheduledPost(title="Phim lồng tiếng tập 1", channel_id=channel_id, platform="youtube",
                            status="scheduled", scheduled_at=datetime(2026, 10, 25, 12))
        other = ScheduledPost(title="Hoàn toàn khác", channel_id=channel_id, platform="youtube",
                              status="scheduled", scheduled_at=datetime(2026, 10, 20, 12))
        db.add_all([near, far, other])
        video = ChannelVideo(channel_id=channel_id, external_id="v", title="PHIM LỒNG TIẾNG TẬP 1 #shorts",
                             published_at=datetime(2026, 10, 20, 10), url="https://youtu.be/v")
        db.add(video)
        db.flush()
        assert cc.match_synced_videos(db, channel_id, [video]) == 1
        db.commit()
        db.refresh(near), db.refresh(far), db.refresh(other)
        assert near.status == "published" and near.channel_video_id == video.id
        assert near.published_url == "https://youtu.be/v" and near.published_at == datetime(2026, 10, 20, 10)
        assert far.status == "scheduled" and other.status == "scheduled"
        # A video already linked is not matched twice.
        assert cc.match_synced_videos(db, channel_id, [video]) == 0
        assert cc.match_synced_videos(db, channel_id, []) == 0


def test_sync_auto_marks_planned_post_published(cal):
    mock_youtube(cal["mock"])
    connect(cal, "youtube")
    with cal["Session"]() as db:
        channel = db.query(Channel).filter(Channel.platform == "youtube").one()
        channel_id = channel.id
        # The first sync already stored vid1; forget it so the next sync brings it "new".
        db.query(ChannelVideo).delete()
        db.commit()
    post = cal["cal"].post(
        "/api/calendar/posts",
        json={"title": "Phim lồng tiếng tập 1", "channel_id": channel_id, "scheduled_at": "2026-10-21T08:00:00Z"},
    ).json()
    assert cal["client"].post(f"/api/channels/{channel_id}/sync").status_code == 202
    sync.wait_for_idle(10)
    after = cal["cal"].get(f"/api/calendar/posts/{post['id']}").json()
    assert after["status"] == "published"
    assert after["channel_video"]["title"] == "Phim lồng tiếng tập 1"
    assert after["published_url"] == "https://www.youtube.com/watch?v=vid1"


def test_post_with_a_local_video_file(cal, tmp_path):
    client = cal["cal"]
    video = tmp_path / "Clip cuối tuần.mp4"
    video.write_bytes(b"0" * 1234)
    post = client.post("/api/calendar/posts", json={"title": "Bài video", "video_path": str(video)}).json()
    assert post["video_path"] == str(video)
    assert post["video_file"] == {"path": str(video), "name": "Clip cuối tuần.mp4", "exists": True, "size": 1234}

    copy = client.post(f"/api/calendar/posts/{post['id']}/duplicate", json={}).json()
    assert copy["video_path"] == str(video)

    # A moved / deleted file stays linked but is reported missing.
    video.unlink()
    again = client.get(f"/api/calendar/posts/{post['id']}").json()
    assert again["video_file"]["exists"] is False and again["video_file"]["size"] is None

    assert client.patch(f"/api/calendar/posts/{post['id']}", json={"video_path": ""}).json()["video_file"] is None
    bad = [str(video), "relative/clip.mp4", str(tmp_path / "notes.txt")]
    (tmp_path / "notes.txt").write_text("x")
    assert [client.patch(f"/api/calendar/posts/{post['id']}", json={"video_path": p}).status_code for p in bad] == [404, 400, 400]
