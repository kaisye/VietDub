"""Viral discovery API + scan flow (no network: fake providers and LLM)."""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database
from app.models import ScheduledPost, ViralSnapshot, ViralVideo
from app.services import storage
from app.services.channels import crypto
from app.services.viral import llm, providers as p, scan
from app.services.viral import settings as vs
from app.viral_api import router

NOW = p.utcnow()


class FakeYouTube(p.Provider):
    id = "youtube_ytdlp"
    platform = "youtube"
    label = "fake yt"
    views = 100_000

    def search(self, q):
        return [
            p.VideoItem(platform="youtube", external_id="vid1", url="https://www.youtube.com/watch?v=vid1",
                        title="Cooking hacks tutorial", creator_name="Chef", followers=5_000, views=FakeYouTube.views,
                        likes=6_000, comments=400, published_at=NOW - timedelta(hours=20), duration=60, source=self.id),
            p.VideoItem(platform="youtube", external_id="vid2", url="https://www.youtube.com/watch?v=vid2",
                        title="Kitchen music beat (Official Music Video)", creator_name="Big", followers=5_000_000,
                        views=8_000, likes=50, comments=3, published_at=NOW - timedelta(days=5), duration=240, source=self.id),
        ]

    def fetch_videos(self, ids):
        return [it for it in self.search(None) if it.external_id in ids] or [
            p.VideoItem(platform="youtube", external_id=i, url=f"https://www.youtube.com/watch?v={i}", title="Pasted",
                        views=1000, published_at=NOW - timedelta(hours=5), source=self.id) for i in ids
        ]

    def fetch_creator(self, ident, limit=20):
        return self.search(None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.delenv("AETHER_CHANNELS_SECRET_KEY", raising=False)
    crypto.reset_cache_for_tests()
    p.clear_cache()
    scan.reset_for_tests()
    monkeypatch.setattr(scan, "scheduler_allowed", False)
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}", connect_args={"check_same_thread": False}, future=True)
    database.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(scan, "session_factory", Session)

    def pick(platform, settings=None):
        if platform == "youtube":
            return FakeYouTube(settings or {})
        return p.DouyinPublicProvider(settings or {})

    monkeypatch.setattr(scan, "pick_provider", pick)
    monkeypatch.setattr(scan, "douyin_hot_words", lambda limit=20: [{"word": "做饭技巧", "hot_value": 9, "label": 0}])
    llm_calls: list[str] = []

    async def fake_complete(system, user):
        llm_calls.append(user)
        if "Topic:" in user:
            return '{"youtube": ["cooking hacks", "kitchen tips"], "douyin": ["做饭技巧"], "synonyms_vi": ["mẹo bếp"]}'
        if "Translate each title" in user:
            return '{"0": "Mẹo nấu ăn", "1": "Nhạc bếp"}'
        return ('{"why_viral": ["Hook mạnh"], "hook": "Cắt cà chua", "title_vi": "5 mẹo bếp cực hay", '
                '"alt_titles_vi": ["A", "B"], "angle": "Tiết kiệm thời gian", "dub_tips": ["Giọng vui"], "risks": []}')

    monkeypatch.setattr(llm, "_complete", fake_complete)
    monkeypatch.setattr(llm, "llm_status", lambda: {"configured": True, "reason": "", "provider": "openai_compatible", "model": "qwen"})
    app = FastAPI()
    app.include_router(router)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[database.get_db] = override_db
    yield {"client": TestClient(app), "Session": Session, "llm_calls": llm_calls}
    scan.wait_for_idle(10)
    crypto.reset_cache_for_tests()
    engine.dispose()


def _scan(client, **body):
    res = client.post("/api/viral/scan", json=body)
    assert res.status_code == 202, res.text
    scan_id = res.json()["id"]
    assert scan.wait_for_idle(10)
    data = client.get(f"/api/viral/scans/{scan_id}").json()
    return data


def test_expand_keywords_llm_and_fallback(env, monkeypatch):
    res = env["client"].post("/api/viral/expand-keywords", json={"topic": "mẹo nấu ăn"})
    assert res.json()["youtube"] == ["cooking hacks", "kitchen tips"]
    assert res.json()["source"] == "llm"

    async def broken(system, user):
        raise llm.LLMUnavailable("not_configured", "Chưa nhập API key.")

    monkeypatch.setattr(llm, "_complete", broken)
    res = env["client"].post("/api/viral/expand-keywords", json={"topic": "mẹo nấu ăn"})
    data = res.json()
    assert data["source"] == "fallback" and data["youtube"] == ["mẹo nấu ăn"] and data["error"] == "not_configured"


def test_topic_crud(env):
    c = env["client"]
    res = c.post("/api/viral/topics", json={"name": "Mẹo nấu ăn", "keywords": {"youtube": ["cooking hacks", " cooking hacks "], "douyin": ["做饭"]},
                                            "filters": {"window": "7d", "platforms": ["youtube"]}})
    assert res.status_code == 201
    topic = res.json()
    assert topic["keywords"]["youtube"] == ["cooking hacks"]
    res = c.patch(f"/api/viral/topics/{topic['id']}", json={"name": "Nấu ăn", "watched": False})
    assert res.json()["name"] == "Nấu ăn" and res.json()["watched"] is False
    assert len(c.get("/api/viral/topics").json()) == 1
    assert c.delete(f"/api/viral/topics/{topic['id']}").status_code == 204
    assert c.get("/api/viral/topics").json() == []
    assert c.patch("/api/viral/topics/nope", json={}).status_code == 404


def test_scan_flow_scores_translates_and_douyin_degrades(env):
    c = env["client"]
    topic = c.post("/api/viral/topics", json={"name": "Mẹo nấu ăn", "keywords": {"youtube": ["cooking hacks"], "douyin": ["做饭"]}}).json()
    data = _scan(c, topic_id=topic["id"], platforms=["youtube", "douyin"], window="7d")
    assert data["state"] == "done"
    assert data["providers"]["youtube"]["ok"] is True
    dy = data["providers"]["douyin"]
    assert dy["ok"] is False and dy["error_code"] == "needs_key"
    assert data["douyin_hot"][0]["word"] == "做饭技巧"
    assert data["found"] == 2 and data["new"] == 2

    listing = c.get("/api/viral/videos", params={"topic_id": topic["id"], "sort": "score"}).json()
    assert listing["total"] == 2
    first, second = listing["items"]
    assert first["external_id"] == "vid1" and first["score"] > second["score"]
    assert "outlier" in first["badges"] and "dub_fit" in first["badges"]
    assert second["dub"]["music_only"] is True
    assert first["title_vi"] == "Mẹo nấu ăn"
    assert c.get("/api/viral/topics").json()[0]["last_scan_count"] == 2

    detail = c.get(f"/api/viral/videos/{first['id']}").json()
    assert detail["breakdown"]["weights"]["velocity"] == 0.35
    assert len(detail["snapshots"]) == 1


def test_rescan_creates_snapshot_and_momentum(env):
    c = env["client"]
    _scan(c, query="cooking", platforms=["youtube"])
    with env["Session"]() as db:
        video = db.query(ViralVideo).filter_by(external_id="vid1").one()
        snap = db.query(ViralSnapshot).filter_by(video_id=video.id).one()
        snap.scanned_at = snap.scanned_at - timedelta(hours=2)  # pretend the first scan was 2h ago
        snap.views = 40_000
        db.commit()
    FakeYouTube.views = 100_000  # +60k in 2h vs 100k over 20h -> accelerating
    p.clear_cache()
    _scan(c, query="cooking", platforms=["youtube"])
    with env["Session"]() as db:
        video = db.query(ViralVideo).filter_by(external_id="vid1").one()
        assert len(video.snapshots) == 2
    detail = c.get(f"/api/viral/videos/{video.id}").json()
    assert detail["metrics"]["momentum"]["delta_views"] == 60_000
    assert "accelerating" in detail["badges"]


def test_scan_requires_keywords(env):
    res = env["client"].post("/api/viral/scan", json={"platforms": ["youtube"]})
    assert res.status_code == 422


def test_video_actions_status_analysis_idea(env):
    c = env["client"]
    _scan(c, query="cooking", platforms=["youtube"])
    vid = c.get("/api/viral/videos").json()["items"][0]
    res = c.patch(f"/api/viral/videos/{vid['id']}", json={"status": "dismissed"})
    assert res.json()["status"] == "dismissed"
    assert all(v["id"] != vid["id"] for v in c.get("/api/viral/videos").json()["items"])
    assert c.get("/api/viral/videos", params={"status": "dismissed"}).json()["total"] == 1

    res = c.post(f"/api/viral/videos/{vid['id']}/analysis")
    assert res.status_code == 200 and res.json()["cached"] is False
    assert res.json()["analysis"]["title_vi"] == "5 mẹo bếp cực hay"
    calls = len(env["llm_calls"])
    res = c.post(f"/api/viral/videos/{vid['id']}/analysis")
    assert res.json()["cached"] is True and len(env["llm_calls"]) == calls

    res = c.post(f"/api/viral/videos/{vid['id']}/idea", json={})
    assert res.status_code == 201
    with env["Session"]() as db:
        post = db.get(ScheduledPost, res.json()["post_id"])
        assert post.status == "idea" and vid["url"] in post.notes and post.title == "5 mẹo bếp cực hay"

    res = c.patch(f"/api/viral/videos/{vid['id']}", json={"media_job_id": "job-1"})
    assert res.json()["status"] == "dubbed"


def test_analyze_urls_videos_and_creator(env):
    c = env["client"]
    res = c.post("/api/viral/analyze", json={"urls": [
        "https://youtu.be/vid1xxxxxxx",
        "https://www.youtube.com/@chef",
        "not a link",
    ]})
    data = res.json()
    assert len(data["videos"]) == 1 and data["videos"][0]["watched"] is True
    assert data["creators"][0]["kind"] == "creator" and data["creators"][0]["source_url"].endswith("@chef")
    assert data["errors"][0]["code"] == "bad_url"
    assert scan.wait_for_idle(10)
    scan_data = c.get(f"/api/viral/scans/{data['scans'][0]['id']}").json()
    assert scan_data["state"] == "done" and scan_data["found"] == 2
    watched = c.get("/api/viral/videos", params={"status": "watched"}).json()
    assert watched["total"] == 1
    res = c.post("/api/viral/videos/refresh", json={})
    assert res.json()["updated"] == 1


def test_settings_endpoint_masks_and_reports(env):
    c = env["client"]
    res = c.put("/api/viral/settings", json={"youtube_api_key": "AIzaSyD-secret-abcdef", "auto_rescan_enabled": True, "auto_rescan_hours": 3})
    data = res.json()
    assert "AIzaSyD-secret" not in str(data)
    assert data["youtube_api_key_set"] is True and data["scheduler"]["enabled"] is True
    assert {pr["id"] for pr in data["providers"]} == {"youtube_api", "youtube_ytdlp", "douyin_public", "douyin_tikhub"}
    assert c.put("/api/viral/settings", json={"default_region": "USA"}).status_code == 422


def test_periodic_rescan_queues_watched_topics(env):
    c = env["client"]
    c.post("/api/viral/topics", json={"name": "A", "keywords": {"youtube": ["a"]}, "filters": {"platforms": ["youtube"]}})
    c.post("/api/viral/topics", json={"name": "B", "keywords": {"youtube": ["b"]}, "watched": False})
    assert scan.run_periodic_once() == 1
    assert scan.wait_for_idle(10)
    assert scan.list_scans()[0]["trigger"] == "schedule"


def test_top_endpoint(env):
    c = env["client"]
    _scan(c, query="cooking", platforms=["youtube"])
    top = c.get("/api/viral/top", params={"limit": 1}).json()
    assert len(top["items"]) == 1 and top["items"][0]["external_id"] == "vid1"
