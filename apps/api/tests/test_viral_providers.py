"""Viral providers with mocked network (httpx MockTransport, fake yt-dlp)."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta

import httpx
import pytest

from app.services import storage
from app.services.channels import crypto
from app.services.viral import providers as p
from app.services.viral import settings as vs


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.delenv("AETHER_CHANNELS_SECRET_KEY", raising=False)
    crypto.reset_cache_for_tests()
    p.clear_cache()
    calls: list[httpx.Request] = []
    routes: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        for prefix, payload in routes.items():
            if str(request.url).startswith(prefix):
                if callable(payload):
                    return payload(request)
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": "no route"})

    monkeypatch.setattr(p, "client_factory", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    yield {"calls": calls, "routes": routes}
    crypto.reset_cache_for_tests()


NOW = p.utcnow()


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


YT_SEARCH = {"items": [{"id": {"kind": "youtube#video", "videoId": "aaaaaaaaaaa"}}, {"id": {"kind": "youtube#video", "videoId": "bbbbbbbbbbb"}}]}
YT_VIDEOS = {
    "items": [
        {
            "id": "aaaaaaaaaaa",
            "snippet": {
                "publishedAt": iso(NOW - timedelta(hours=10)),
                "channelId": "UC1",
                "title": "5 cooking hacks",
                "description": "tutorial",
                "channelTitle": "Chef A",
                "thumbnails": {"high": {"url": "https://i.ytimg.com/a.jpg"}},
                "liveBroadcastContent": "none",
            },
            "contentDetails": {"duration": "PT1M5S"},
            "statistics": {"viewCount": "120000", "likeCount": "5000", "commentCount": "300"},
        },
        {
            "id": "bbbbbbbbbbb",
            "snippet": {"publishedAt": iso(NOW - timedelta(hours=2)), "channelId": "UC2", "title": "LIVE", "liveBroadcastContent": "live"},
            "contentDetails": {"duration": "P0D"},
            "statistics": {},
        },
    ]
}
YT_CHANNELS = {"items": [{"id": "UC1", "statistics": {"subscriberCount": "10000", "hiddenSubscriberCount": False}}]}


def test_settings_encrypt_and_mask(env):
    vs.update_settings({"youtube_api_key": "AIzaSyD-very-secret-key-1234"})
    raw = (storage.STORAGE_ROOT / "viral-settings.json").read_text()
    assert "very-secret" not in raw and "fernet:" in raw
    loaded = vs.load_settings()
    assert loaded["youtube_api_key"] == "AIzaSyD-very-secret-key-1234"
    pub = vs.public_settings(loaded)
    assert "youtube_api_key" not in pub and pub["youtube_api_key_set"] is True
    assert pub["youtube_api_key_masked"].endswith("1234") and "very" not in pub["youtube_api_key_masked"]
    # masked echo keeps, "" clears
    vs.update_settings({"youtube_api_key": pub["youtube_api_key_masked"]})
    assert vs.load_settings()["youtube_api_key"]
    vs.update_settings({"youtube_api_key": ""})
    assert vs.load_settings()["youtube_api_key"] == ""
    with pytest.raises(vs.SettingsError):
        vs.update_settings({"youtube_mode": "nope"})


def test_parse_source_url():
    assert p.parse_source_url("https://youtu.be/dQw4w9WgXcQ?t=3")["id"] == "dQw4w9WgXcQ"
    assert p.parse_source_url("xem nè https://www.youtube.com/shorts/dQw4w9WgXcQ")["kind"] == "video"
    c = p.parse_source_url("https://www.youtube.com/@MrBeast/videos")
    assert c == {"platform": "youtube", "kind": "creator", "id": "@MrBeast", "url": "https://www.youtube.com/@MrBeast"}
    d = p.parse_source_url("https://www.douyin.com/jingxuan?modal_id=7679019544425418035")
    assert d["platform"] == "douyin" and d["id"] == "7679019544425418035"
    assert p.parse_source_url("https://www.douyin.com/user/MS4wLjABAAAA")["kind"] == "creator"
    assert p.parse_source_url("https://v.douyin.com/iAbCdEf/")["kind"] == "short_link"
    assert p.parse_source_url("https://example.com/x") is None


def test_parse_iso_duration():
    assert p.parse_iso_duration("PT1H2M3S") == 3723
    assert p.parse_iso_duration("PT45S") == 45
    assert p.parse_iso_duration("P0D") == 0
    assert p.parse_iso_duration(None) is None


def test_youtube_search_sp_encoding():
    sp = p.youtube_search_sp("views", "7d", "any")
    assert sp == "CAMSBAgDEAE="  # sort=view count, upload date=this week, type=video
    raw = base64.b64decode(p.youtube_search_sp("views", "24h", "short"))
    assert raw == b"\x08\x03\x12\x06\x08\x02\x10\x01\x18\x01"
    assert p.youtube_search_sp("relevance", "", "any") == "EgIQAQ=="


def test_youtube_api_search_counts_quota_and_caches(env):
    vs.update_settings({"youtube_api_key": "AIzaSyD-test-key-000000"})
    env["routes"].update({
        f"{p.YT_API}/search": YT_SEARCH,
        f"{p.YT_API}/videos": YT_VIDEOS,
        f"{p.YT_API}/channels": YT_CHANNELS,
    })
    provider = p.pick_provider("youtube")
    assert provider.id == "youtube_api"
    q = p.SearchQuery(keyword="cooking hacks", window="7d", region="US", limit=10)
    items = provider.search(q)
    assert [i.external_id for i in items] == ["aaaaaaaaaaa"]  # live skipped
    item = items[0]
    assert item.views == 120000 and item.followers == 10000 and item.duration == 65
    assert item.thumbnail.endswith("a.jpg")
    search_req = env["calls"][0]
    assert search_req.url.params["order"] == "viewCount"
    assert search_req.url.params["regionCode"] == "US"
    assert "publishedAfter" in search_req.url.params
    assert vs.quota_used_today() == 102
    provider.search(q)  # cached: no more calls, no more quota
    assert len(env["calls"]) == 3 and vs.quota_used_today() == 102
    assert p.estimate_youtube_quota(3, 20) == 306


def test_youtube_api_quota_error(env):
    vs.update_settings({"youtube_api_key": "AIzaSyD-test-key-000000"})
    env["routes"][f"{p.YT_API}/search"] = lambda r: httpx.Response(
        403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}}
    )
    with pytest.raises(p.SourceError) as err:
        p.YouTubeApiProvider().search(p.SearchQuery(keyword="x"))
    assert err.value.code == "quota_exceeded"


def test_ytdlp_provider_search_and_enrich(env, monkeypatch):
    seen = []

    def fake_extract(url, options):
        seen.append((url, options))
        if "results?" in url:
            return {"entries": [
                {"id": "ccccccccccc", "title": "Hack 1", "view_count": 50000, "duration": 50, "channel": "C", "channel_id": "UCc",
                 "thumbnails": [{"url": "https://i.ytimg.com/c.jpg"}]},
                {"id": "ddddddddddd", "title": "Long one", "view_count": 9000, "duration": 1500, "channel": "D"},
                {"id": "eeeeeeeeeee", "title": "Live", "live_status": "is_live"},
            ]}
        vid = url.rsplit("=", 1)[-1]
        if vid == "ddddddddddd":
            raise RuntimeError("Sign in to confirm you're not a bot")
        return {"id": vid, "like_count": 2500, "comment_count": 40, "channel_follower_count": 1200,
                "upload_date": (NOW - timedelta(days=1)).strftime("%Y%m%d"), "view_count": 51000}

    monkeypatch.setattr(p, "ytdlp_extract", fake_extract)
    vs.update_settings({"youtube_mode": "ytdlp"})
    provider = p.pick_provider("youtube")
    assert provider.id == "youtube_ytdlp"
    items = provider.search(p.SearchQuery(keyword="cooking hacks", window="7d", region="US", limit=10))
    by_id = {i.external_id: i for i in items}
    assert set(by_id) == {"ccccccccccc", "ddddddddddd"}
    c = by_id["ccccccccccc"]
    assert c.views == 51000 and c.likes == 2500 and c.followers == 1200 and c.published_at is not None
    assert by_id["ddddddddddd"].likes is None  # detail failed -> flat data kept
    search_url = seen[0][0]
    assert "sp=CAMSBAgDEAE%3D" in search_url and "gl=US" in search_url
    assert seen[0][1]["playlistend"] == 10


def test_douyin_public_search_needs_key_and_hot_words(env, monkeypatch):
    env["routes"][p.DOUYIN_HOT_URL] = {"status_code": 0, "word_list": [{"word": "做饭技巧", "hot_value": 123, "label": 1}]}
    provider = p.pick_provider("douyin")
    assert provider.id == "douyin_public"
    with pytest.raises(p.SourceError) as err:
        provider.search(p.SearchQuery(keyword="做饭"))
    assert err.value.code == "needs_key"
    assert p.douyin_hot_words()[0] == {"word": "做饭技巧", "hot_value": 123, "label": 1}
    monkeypatch.setattr(p, "snapvideotools_lookup", lambda url: {"code": 0, "data": {"title": "抓鱼", "cover": "https://p.jpg"}})
    items = provider.fetch_videos(["7676828582273912110"])
    assert items[0].title == "抓鱼" and items[0].views is None and items[0].thumbnail == "https://p.jpg"


AWEME = {
    "aweme_id": "7600000000000000001",
    "desc": "做饭小技巧 #美食",
    "create_time": None,  # set per test
    "author": {"nickname": "厨师", "uid": "1", "sec_uid": "MS4wABC", "follower_count": 20000},
    "statistics": {"play_count": 0, "digg_count": 90000, "comment_count": 1200, "share_count": 3000, "collect_count": 5000},
    "video": {"duration": 58000, "cover": {"url_list": ["https://cover.jpg"]}},
}


def test_tikhub_search_parses_awemes(env):
    import calendar

    aweme = dict(AWEME, create_time=calendar.timegm((NOW - timedelta(hours=30)).timetuple()))
    vs.update_settings({"douyin_mode": "tikhub", "tikhub_api_key": "tk-secret-123456"})
    env["routes"]["https://api.tikhub.io/api/v1/douyin/web/fetch_video_search_result"] = {
        "code": 200, "data": {"data": [{"type": 1, "aweme_info": aweme}, {"type": 2, "user_list": []}]}
    }
    provider = p.pick_provider("douyin")
    assert provider.id == "douyin_tikhub"
    items = provider.search(p.SearchQuery(keyword="做饭", window="7d", limit=10))
    assert len(items) == 1
    it = items[0]
    assert it.views is None and it.likes == 90000 and it.collects == 5000
    assert it.duration == 58 and it.followers == 20000 and it.creator_url.endswith("MS4wABC")
    req = env["calls"][0]
    assert req.headers["Authorization"] == "Bearer tk-secret-123456"
    assert req.url.params["publish_time"] == "7"


def test_tikhub_payment_required(env):
    vs.update_settings({"douyin_mode": "tikhub", "tikhub_api_key": "tk"})
    env["routes"]["https://api.tikhub.io"] = lambda r: httpx.Response(402, json={})
    with pytest.raises(p.SourceError) as err:
        p.TikHubDouyinProvider().search(p.SearchQuery(keyword="x"))
    assert err.value.code == "payment_required"


def test_availability_report(env):
    report = {r["id"]: r for r in p.availability_report()}
    assert report["youtube_api"]["available"] is False and report["youtube_api"]["reason"] == "no_key"
    assert report["youtube_ytdlp"]["available"] is True
    assert report["douyin_public"]["capabilities"] == ["hot_words", "video_basic"]
    assert report["douyin_tikhub"]["reason"] == "no_key"
