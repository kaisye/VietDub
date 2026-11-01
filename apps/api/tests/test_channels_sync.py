from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.models import Channel, ChannelSnapshot, ChannelVideo
from app.services.channels import sync
from app.services.channels.providers.facebook import FacebookProvider
from app.services.channels.providers.tiktok import TikTokProvider
from app.services.channels.providers.youtube import YouTubeProvider, parse_iso_duration
from test_channels_support import (  # noqa: F401  (fixture import)
    FB_POSTS,
    FB_VIDEOS,
    TT_VIDEOS,
    YT_VIDEOS,
    body_json,
    channels_env,
    connect,
    mock_facebook,
    mock_tiktok,
    mock_youtube,
    query,
)


# -- Pure mapping -------------------------------------------------------------------------
def test_youtube_video_mapping():
    video = YouTubeProvider.map_video(YT_VIDEOS["items"][0])
    assert video.external_id == "vid1" and video.title == "Phim lồng tiếng tập 1"
    assert video.published_at == datetime(2026, 10, 20, 10, 0)
    assert (video.views, video.likes, video.comments) == (1000, 100, 10)
    assert video.duration_seconds == 3723
    assert video.thumbnail_url.endswith("maxresdefault.jpg")
    assert video.url == "https://www.youtube.com/watch?v=vid1"
    hidden = YouTubeProvider.map_video(YT_VIDEOS["items"][1])
    assert hidden.likes is None and hidden.duration_seconds == 45
    assert parse_iso_duration("P1DT1S") == 86401 and parse_iso_duration("bogus") is None


def test_facebook_video_and_post_mapping():
    video = FacebookProvider.map_video(FB_VIDEOS["data"][0])
    assert video.published_at == datetime(2026, 10, 21, 8, 0)
    assert (video.likes, video.comments, video.duration_seconds) == (30, 4, 95)
    assert video.url == "https://www.facebook.com/vietdubpage/videos/v1/"
    post = FacebookProvider.map_post(FB_POSTS["data"][0])
    assert post.kind == "post" and post.title == "Bài viết mới"
    assert (post.likes, post.comments, post.shares) == (12, 2, 3)


def test_tiktok_video_mapping():
    first, second = (TikTokProvider.map_video(v) for v in TT_VIDEOS["data"]["videos"])
    assert first.published_at == datetime.fromtimestamp(1761300000, timezone.utc).replace(tzinfo=None)
    assert (first.views, first.likes, first.comments, first.shares) == (20000, 1500, 40, 25)
    assert first.url.startswith("https://www.tiktok.com/@vietdub.tt/video/")
    assert second.title == "Only description"


# -- Full sync per platform ----------------------------------------------------------------
def _channel(env, platform, external_id=None):
    with env["Session"]() as db:
        query_ = db.query(Channel).filter(Channel.platform == platform)
        if external_id:
            query_ = query_.filter(Channel.external_id == external_id)
        return query_.first().id


def _snapshot(env, channel_id):
    with env["Session"]() as db:
        return db.query(ChannelSnapshot).filter(ChannelSnapshot.channel_id == channel_id).one()


def test_youtube_sync_creates_snapshot_and_videos(channels_env):
    mock_youtube(channels_env["mock"])
    connect(channels_env, "youtube")
    channel_id = _channel(channels_env, "youtube")
    snap = _snapshot(channels_env, channel_id)
    assert (snap.followers, snap.views, snap.video_count) == (45600, 1234567, 321)
    assert snap.likes == 100 and snap.comments == 10  # summed from recent videos
    playlist = channels_env["mock"].find("GET", "/playlistItems")[0]
    assert query(playlist)["playlistId"] == "UUabc123"
    assert channels_env["mock"].find("GET", "/youtube/v3/channels")[0].headers["Authorization"] == "Bearer ya29.google-access"
    videos = channels_env["client"].get(f"/api/channels/{channel_id}/videos", params={"sort": "views"}).json()
    assert [v["external_id"] for v in videos] == ["vid1", "vid2"]


def test_youtube_hidden_subscriber_count(channels_env):
    mock = channels_env["mock"]
    mock_youtube(mock)
    from test_channels_support import YT_CHANNEL
    import copy

    hidden = copy.deepcopy(YT_CHANNEL)
    hidden["items"][0]["statistics"]["hiddenSubscriberCount"] = True
    mock.on("GET", "https://www.googleapis.com/youtube/v3/channels", hidden)
    connect(channels_env, "youtube")
    assert _snapshot(channels_env, _channel(channels_env, "youtube")).followers is None


def test_facebook_sync_page_metrics_and_insights(channels_env):
    mock_facebook(channels_env["mock"])
    connect(channels_env, "facebook")
    p1 = _channel(channels_env, "facebook", "p1")
    snap = _snapshot(channels_env, p1)
    assert snap.followers == 1200
    assert snap.views == 777  # video insights total_video_views
    assert snap.likes == 42 and snap.comments == 6 and snap.shares == 3
    with channels_env["Session"]() as db:
        kinds = {v.external_id: v.kind for v in db.query(ChannelVideo).filter(ChannelVideo.channel_id == p1)}
        assert kinds == {"v1": "video", "p1_555": "post"}
        # p2 has no mocked page endpoint -> sync error recorded, app keeps running.
        p2 = db.query(Channel).filter(Channel.external_id == "p2").one()
        assert p2.status == "error" and p2.last_error


def test_tiktok_sync_uses_stats_and_video_list(channels_env):
    mock_tiktok(channels_env["mock"])
    connect(channels_env, "tiktok")
    snap = _snapshot(channels_env, _channel(channels_env, "tiktok"))
    assert (snap.followers, snap.video_count, snap.likes) == (9000, 88, 150000)
    assert snap.views == 25000  # sum of recent video views (TikTok has no lifetime total)
    video_list = channels_env["mock"].find("POST", "/v2/video/list/")[0]
    assert "view_count" in query(video_list)["fields"]
    assert body_json(video_list)["max_count"] == 20


def test_tiktok_video_list_paginates(channels_env):
    mock = channels_env["mock"]
    mock_tiktok(mock)
    pages = iter([
        {**TT_VIDEOS, "data": {**TT_VIDEOS["data"], "has_more": True, "cursor": 123}},
        TT_VIDEOS,
    ])
    mock.on("POST", "https://open.tiktokapis.com/v2/video/list/", lambda r: next(pages))
    connect(channels_env, "tiktok")
    requests = mock.find("POST", "/v2/video/list/")
    assert len(requests) == 2 and body_json(requests[1])["cursor"] == 123


def test_sync_retries_once_after_401_with_refresh(channels_env):
    mock = channels_env["mock"]
    mock_youtube(mock)
    connect(channels_env, "youtube")
    calls = {"n": 0}

    def channels(request):
        calls["n"] += 1
        if request.headers["Authorization"] == "Bearer ya29.google-access":
            return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials"}})
        from test_channels_support import YT_CHANNEL

        return YT_CHANNEL

    mock.on("GET", "https://www.googleapis.com/youtube/v3/channels", channels)
    mock.on("POST", "https://oauth2.googleapis.com/token", {"access_token": "ya29.fresh", "expires_in": 3599})
    channel_id = _channel(channels_env, "youtube")
    with channels_env["Session"]() as db:
        result = sync.sync_channel(db, db.get(Channel, channel_id))
    assert result["ok"] is True


def test_sync_quota_error_is_recorded_not_raised(channels_env):
    mock = channels_env["mock"]
    mock_youtube(mock)
    connect(channels_env, "youtube")
    mock.on(
        "GET",
        "https://www.googleapis.com/youtube/v3/channels",
        {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}},
        status=403,
    )
    channel_id = _channel(channels_env, "youtube")
    response = channels_env["client"].post(f"/api/channels/{channel_id}/sync")
    assert response.status_code == 202
    sync.wait_for_idle(10)
    status = channels_env["client"].get("/api/channels/sync/status").json()
    assert status["running"] is False
    result = next(r for r in status["results"] if r["channel_id"] == channel_id)
    assert result["ok"] is False and "quota" in result["error"]
    assert channels_env["client"].get(f"/api/channels/{channel_id}").json()["status"] == "error"


def test_sync_crash_in_provider_never_propagates(channels_env, monkeypatch):
    mock_tiktok(channels_env["mock"])
    connect(channels_env, "tiktok")

    def boom(self, *args, **kwargs):
        raise KeyError("unexpected payload")

    monkeypatch.setattr(TikTokProvider, "fetch_stats", boom)
    channel_id = _channel(channels_env, "tiktok")
    with channels_env["Session"]() as db:
        result = sync.sync_channel(db, db.get(Channel, channel_id))
    assert result["ok"] is False and result["error"] == "tiktok: unexpected sync error."


def test_sync_all_skips_manual_and_only_stale(channels_env):
    client = channels_env["client"]
    mock_tiktok(channels_env["mock"])
    connect(channels_env, "tiktok")
    client.post("/api/channels/manual", json={"platform": "youtube", "name": "Manual"})
    fresh = client.post("/api/channels/sync", json={"only_stale_hours": 6}).json()
    assert fresh["queued_channel_ids"] == []  # just synced after connect
    everything = client.post("/api/channels/sync", json={}).json()
    assert len(everything["queued_channel_ids"]) == 1
    sync.wait_for_idle(10)


def test_same_day_sync_updates_single_snapshot(channels_env):
    mock_tiktok(channels_env["mock"])
    connect(channels_env, "tiktok")
    channel_id = _channel(channels_env, "tiktok")
    channels_env["client"].post(f"/api/channels/{channel_id}/sync")
    sync.wait_for_idle(10)
    with channels_env["Session"]() as db:
        assert db.query(ChannelSnapshot).filter(ChannelSnapshot.channel_id == channel_id).count() == 1
        assert db.query(ChannelVideo).filter(ChannelVideo.channel_id == channel_id).count() == 2
