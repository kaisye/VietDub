"""Shared fixtures for the channels tests (isolated DB, storage, mocked platform HTTP).

Imported by the test_channels_*.py modules; contains no tests itself.
"""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database
from app.channels_api import router
from app.services import storage
from app.services.channels import crypto, sync
from app.services.channels.providers import base as provider_base

ENV_VARS = [
    "AETHER_CHANNELS_SECRET_KEY",
    "AETHER_CHANNELS_CONFIG_PATH",
    "AETHER_CHANNELS_REDIRECT_BASE_URL",
    "AETHER_API_PORT",
    "AETHER_FACEBOOK_PKCE",
    "AETHER_FACEBOOK_GRAPH_VERSION",
    "AETHER_TIKTOK_CLIENT_KEY",
] + [f"AETHER_{p}_{f}" for p in ("YOUTUBE", "FACEBOOK", "TIKTOK") for f in ("CLIENT_ID", "CLIENT_SECRET")]


class MockPlatform:
    """Route table for httpx.MockTransport: {(METHOD, 'host/path'): handler}."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], Any]] = {}
        self.requests: list[httpx.Request] = []

    def on(self, method: str, url: str, response: Any = None, status: int = 200):
        key = (method.upper(), url.split("://", 1)[-1].split("?", 1)[0])

        def handler(request: httpx.Request):
            if callable(response):
                return response(request)
            return httpx.Response(status, json=response if response is not None else {})

        self.routes[key] = handler
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, f"{request.url.host}{request.url.path}")
        handler = self.routes.get(key)
        if handler is None:
            return httpx.Response(404, json={"error": {"message": f"unmocked {key}"}})
        result = handler(request)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)

    def find(self, method: str, path_fragment: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and path_fragment in str(r.url)]


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def query(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}


def body_json(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode() or "{}")


@pytest.fixture
def channels_env(tmp_path, monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path / "storage")
    crypto.reset_cache_for_tests()
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}", connect_args={"check_same_thread": False}, future=True
    )
    database.Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(sync, "session_factory", session_factory)
    mock = MockPlatform()
    monkeypatch.setattr(
        provider_base, "client_factory", lambda: httpx.Client(transport=httpx.MockTransport(mock))
    )

    app = FastAPI()
    app.include_router(router)

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[database.get_db] = override_db
    client = TestClient(app, base_url="http://127.0.0.1:8386")
    yield {"client": client, "mock": mock, "Session": session_factory, "tmp": tmp_path}
    sync.wait_for_idle(10)
    crypto.reset_cache_for_tests()
    engine.dispose()


# ---------------------------------------------------------------------------------------
# Realistic sample payloads (shapes copied from the official API reference pages).
# ---------------------------------------------------------------------------------------
YT_CHANNEL = {
    "kind": "youtube#channelListResponse",
    "items": [
        {
            "kind": "youtube#channel",
            "id": "UCabc123",
            "snippet": {
                "title": "VietDub Studio",
                "customUrl": "@vietdub",
                "publishedAt": "2020-01-01T00:00:00Z",
                "thumbnails": {
                    "default": {"url": "https://yt3.ggpht.com/a=s88"},
                    "high": {"url": "https://yt3.ggpht.com/a=s800"},
                },
            },
            "contentDetails": {"relatedPlaylists": {"likes": "", "uploads": "UUabc123"}},
            "statistics": {
                "viewCount": "1234567",
                "subscriberCount": "45600",
                "hiddenSubscriberCount": False,
                "videoCount": "321",
            },
        }
    ],
}
YT_PLAYLIST = {
    "items": [
        {"contentDetails": {"videoId": "vid1", "videoPublishedAt": "2026-10-20T10:00:00Z"}},
        {"contentDetails": {"videoId": "vid2", "videoPublishedAt": "2026-09-01T10:00:00Z"}},
    ]
}
YT_VIDEOS = {
    "items": [
        {
            "id": "vid1",
            "snippet": {
                "publishedAt": "2026-10-20T10:00:00Z",
                "title": "Phim lồng tiếng tập 1",
                "thumbnails": {"medium": {"url": "https://i.ytimg.com/vi/vid1/mqdefault.jpg"},
                               "maxres": {"url": "https://i.ytimg.com/vi/vid1/maxresdefault.jpg"}},
            },
            "contentDetails": {"duration": "PT1H2M3S"},
            "statistics": {"viewCount": "1000", "likeCount": "100", "commentCount": "10", "favoriteCount": "0"},
        },
        {
            "id": "vid2",
            "snippet": {"publishedAt": "2026-09-01T10:00:00Z", "title": "Short", "thumbnails": {}},
            "contentDetails": {"duration": "PT45S"},
            "statistics": {"viewCount": "500"},
        },
    ]
}
GOOGLE_TOKEN = {
    "access_token": "ya29.google-access",
    "expires_in": 3599,
    "refresh_token": "1//google-refresh",
    "scope": "https://www.googleapis.com/auth/youtube.readonly",
    "token_type": "Bearer",
}

FB_SHORT = {"access_token": "fb-short", "token_type": "bearer", "expires_in": 5183}
FB_LONG = {"access_token": "fb-long-user", "token_type": "bearer", "expires_in": 5183944}
FB_ME = {"id": "10001", "name": "Phong"}
FB_ACCOUNTS = {
    "data": [
        {
            "id": "p1",
            "name": "VietDub Page",
            "username": "vietdubpage",
            "link": "https://www.facebook.com/vietdubpage",
            "picture": {"data": {"url": "https://scontent.xx.fbcdn.net/p1.jpg"}},
            "access_token": "page-token-1",
            "followers_count": 1200,
            "fan_count": 1100,
        },
        {
            "id": "p2",
            "name": "Second Page",
            "picture": {"data": {"url": "https://scontent.xx.fbcdn.net/p2.jpg"}},
            "access_token": "page-token-2",
            "fan_count": 50,
        },
    ],
    "paging": {"cursors": {"before": "a", "after": "b"}},
}
FB_PAGE = {k: v for k, v in FB_ACCOUNTS["data"][0].items() if k != "access_token"}
FB_VIDEOS = {
    "data": [
        {
            "id": "v1",
            "title": "Video lồng tiếng",
            "description": "desc",
            "created_time": "2026-10-21T08:00:00+0000",
            "length": 95.4,
            "permalink_url": "/vietdubpage/videos/v1/",
            "picture": "https://scontent.xx.fbcdn.net/v1.jpg",
            "likes": {"data": [], "summary": {"total_count": 30, "can_like": True, "has_liked": False}},
            "comments": {"data": [], "summary": {"order": "ranked", "total_count": 4, "can_comment": True}},
        }
    ],
    "paging": {},
}
FB_VIDEO_INSIGHTS = {
    "data": [
        {
            "name": "total_video_views",
            "period": "lifetime",
            "values": [{"value": 777}],
            "title": "Lifetime Total Video Views",
            "id": "v1/video_insights/total_video_views/lifetime",
        }
    ]
}
FB_POSTS = {
    "data": [
        {
            "id": "p1_555",
            "message": "Bài viết mới",
            "created_time": "2026-10-22T09:00:00+0000",
            "permalink_url": "https://www.facebook.com/vietdubpage/posts/555",
            "full_picture": "https://scontent.xx.fbcdn.net/555.jpg",
            "shares": {"count": 3},
            "reactions": {"data": [], "summary": {"total_count": 12, "viewer_reaction": "NONE"}},
            "comments": {"data": [], "summary": {"total_count": 2}},
        }
    ]
}

TT_TOKEN = {
    "access_token": "act.tiktok-access",
    "expires_in": 86400,
    "open_id": "afd97af1-b87b-48b9-ac98-410aghda5344",
    "refresh_expires_in": 31536000,
    "refresh_token": "rft.tiktok-refresh",
    "scope": "user.info.basic,user.info.profile,user.info.stats,video.list",
    "token_type": "Bearer",
}
TT_OK = {"code": "ok", "message": "", "log_id": "20260101"}
TT_USER = {
    "data": {
        "user": {
            "open_id": "afd97af1-b87b-48b9-ac98-410aghda5344",
            "union_id": "u-1",
            "avatar_url": "https://p16-sign.tiktokcdn-us.com/avatar.jpeg",
            "display_name": "VietDub TT",
            "username": "vietdub.tt",
            "profile_deep_link": "https://vm.tiktok.com/ZMabc/",
            "follower_count": 9000,
            "following_count": 12,
            "likes_count": 150000,
            "video_count": 88,
        }
    },
    "error": TT_OK,
}
TT_VIDEOS = {
    "data": {
        "videos": [
            {
                "id": "7300000000000000001",
                "title": "Clip 1",
                "video_description": "Clip 1 #dub",
                "create_time": 1761300000,
                "cover_image_url": "https://p16-sign.tiktokcdn-us.com/cover1.jpeg",
                "share_url": "https://www.tiktok.com/@vietdub.tt/video/7300000000000000001",
                "duration": 31,
                "view_count": 20000,
                "like_count": 1500,
                "comment_count": 40,
                "share_count": 25,
            },
            {
                "id": "7300000000000000002",
                "title": "",
                "video_description": "Only description",
                "create_time": 1760000000,
                "cover_image_url": "https://p16-sign.tiktokcdn-us.com/cover2.jpeg",
                "share_url": "https://www.tiktok.com/@vietdub.tt/video/7300000000000000002",
                "duration": 15,
                "view_count": 5000,
                "like_count": 300,
                "comment_count": 5,
                "share_count": 1,
            },
        ],
        "cursor": 1760000000000,
        "has_more": False,
    },
    "error": TT_OK,
}


def mock_youtube(mock: MockPlatform) -> None:
    mock.on("POST", "https://oauth2.googleapis.com/token", GOOGLE_TOKEN)
    mock.on("POST", "https://oauth2.googleapis.com/revoke", {})
    mock.on("GET", "https://www.googleapis.com/youtube/v3/channels", YT_CHANNEL)
    mock.on("GET", "https://www.googleapis.com/youtube/v3/playlistItems", YT_PLAYLIST)
    mock.on("GET", "https://www.googleapis.com/youtube/v3/videos", YT_VIDEOS)


def mock_facebook(mock: MockPlatform, version: str = "v26.0") -> None:
    graph = f"https://graph.facebook.com/{version}"

    def access_token(request: httpx.Request):
        return FB_LONG if query(request).get("grant_type") == "fb_exchange_token" else FB_SHORT

    mock.on("GET", f"{graph}/oauth/access_token", access_token)
    mock.on("GET", f"{graph}/me", FB_ME)
    mock.on("GET", f"{graph}/me/accounts", FB_ACCOUNTS)
    mock.on("DELETE", f"{graph}/me/permissions", {"success": True})
    mock.on("GET", f"{graph}/p1", FB_PAGE)
    mock.on("GET", f"{graph}/p1/videos", FB_VIDEOS)
    mock.on("GET", f"{graph}/p1/posts", FB_POSTS)
    mock.on("GET", f"{graph}/v1/video_insights", FB_VIDEO_INSIGHTS)


def mock_tiktok(mock: MockPlatform) -> None:
    mock.on("POST", "https://open.tiktokapis.com/v2/oauth/token/", TT_TOKEN)
    mock.on("POST", "https://open.tiktokapis.com/v2/oauth/revoke/", {})
    mock.on("GET", "https://open.tiktokapis.com/v2/user/info/", TT_USER)
    mock.on("POST", "https://open.tiktokapis.com/v2/video/list/", TT_VIDEOS)


def configure(client: TestClient, platform: str, client_id: str = "cid-123", secret: str = "super-secret-value"):
    response = client.put(f"/api/channels/credentials/{platform}", json={"client_id": client_id, "client_secret": secret})
    assert response.status_code == 200, response.text
    return response.json()


def connect(env, platform: str, code: str = "auth-code") -> dict:
    """Run start -> callback -> status and return the status payload."""
    client = env["client"]
    configure(client, platform)
    start = client.post(f"/api/channels/oauth/{platform}/start").json()
    callback = client.get(
        f"/api/channels/oauth/{platform}/callback", params={"state": start["state"], "code": code}
    )
    assert callback.status_code == 200, callback.text
    sync.wait_for_idle(10)
    status = client.get(f"/api/channels/oauth/status/{start['state']}").json()
    return {"start": start, "callback": callback, "status": status}
