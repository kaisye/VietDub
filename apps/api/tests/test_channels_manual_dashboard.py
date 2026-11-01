from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import Channel, ChannelSnapshot, ChannelVideo, MediaJob
from app.services.channels import dashboard, manual
from app.services.channels.providers.base import utcnow
from test_channels_support import channels_env  # noqa: F401  (fixture import)


def test_parse_number_variants():
    assert manual.parse_number("1,234") == 1234
    assert manual.parse_number("1.234.567") == 1234567
    assert manual.parse_number("1.2K") == 1200
    assert manual.parse_number("3,4M") == 3400000
    assert manual.parse_number("2 triệu") == 2000000
    assert manual.parse_number("") is None and manual.parse_number(None) is None
    with pytest.raises(manual.ManualInputError):
        manual.parse_number("abc")


def test_normalize_date_formats():
    assert manual.normalize_date("2026-10-01") == "2026-10-01"
    assert manual.normalize_date("01/10/2026") == "2026-10-01"  # Vietnamese dd/mm/yyyy
    with pytest.raises(manual.ManualInputError):
        manual.normalize_date("yesterday")


def test_manual_channel_lifecycle(channels_env):
    client = channels_env["client"]
    created = client.post(
        "/api/channels/manual",
        json={
            "platform": "youtube",
            "name": "Kênh thủ công",
            "url": "https://www.youtube.com/@manual",
            "metrics": {"followers": "12.5K", "views": 1000000, "video_count": 40},
        },
    )
    assert created.status_code == 201
    channel = created.json()
    assert channel["source"] == "manual" and channel["status"] == "manual"
    assert channel["latest"]["followers"] == 12500
    assert client.post("/api/channels/manual", json={"platform": "myspace", "name": "x"}).status_code == 422
    assert client.post("/api/channels/manual", json={"platform": "tiktok", "name": "x", "url": "javascript:1"}).status_code == 400
    assert client.post(f"/api/channels/{channel['id']}/sync").status_code == 400
    assert client.post(f"/api/channels/{channel['id']}/disconnect").status_code == 400

    patched = client.patch(f"/api/channels/{channel['id']}", json={"name": "Đổi tên", "handle": "@manual"}).json()
    assert patched["name"] == "Đổi tên" and patched["handle"] == "@manual"

    snap = client.post(f"/api/channels/{channel['id']}/snapshots", json={"date": "2026-10-01", "followers": 12000})
    assert snap.status_code == 200
    history = client.get(f"/api/channels/{channel['id']}/snapshots", params={"days": 3650}).json()
    assert [h["date"] for h in history][0] == "2026-10-01"

    video = client.post(
        f"/api/channels/{channel['id']}/videos",
        json={"title": "Video tay", "views": "5K", "published_at": "2026-10-20", "url": "https://youtu.be/x"},
    )
    assert video.status_code == 201 and video.json()["views"] == 5000

    assert client.delete(f"/api/channels/{channel['id']}").json()["deleted"] is True
    with channels_env["Session"]() as db:
        assert db.query(ChannelSnapshot).count() == 0 and db.query(ChannelVideo).count() == 0


def test_csv_import_snapshots_and_videos(channels_env):
    client = channels_env["client"]
    channel_id = client.post("/api/channels/manual", json={"platform": "facebook", "name": "Page"}).json()["id"]
    csv_text = (
        "Date;Followers;Views;Videos\n"
        "2026-10-01;1.000;50.000;10\n"
        "02/10/2026;1.050;52.000;11\n"
        "bad-date;1;2;3\n"
        ";;;\n"
    )
    result = client.post(
        f"/api/channels/{channel_id}/import",
        files={"file": ("metrics.csv", csv_text.encode("utf-8"), "text/csv")},
        data={"kind": "snapshots"},
    ).json()
    assert result["imported"] == 2 and result["skipped"] == 1
    assert result["errors"][0]["row"] == 3
    assert result["channel"]["latest"] == {
        "date": "2026-10-02", "followers": 1050, "views": 52000, "video_count": 11,
        "likes": None, "comments": None, "shares": None, "source": "csv",
    }
    videos_csv = "id,title,published_at,views,likes,duration,url\nabc,Clip A,2026-10-03,1200,50,1:05,https://fb.watch/a\n"
    imported = client.post(
        f"/api/channels/{channel_id}/import",
        files={"file": ("videos.csv", videos_csv, "text/csv")},
        data={"kind": "videos"},
    ).json()
    assert imported["imported"] == 1
    video = client.get(f"/api/channels/{channel_id}/videos").json()[0]
    assert video["duration_seconds"] == 65 and video["source"] == "csv"
    # Missing columns -> 400.
    bad = client.post(
        f"/api/channels/{channel_id}/import", files={"file": ("x.csv", "foo,bar\n1,2\n", "text/csv")}, data={"kind": "snapshots"}
    )
    assert bad.status_code == 400


def _seed_dashboard(env):
    now = utcnow()
    with env["Session"]() as db:
        yt = Channel(platform="youtube", source="manual", name="YT", status="manual", connected_at=now - timedelta(days=40))
        tt = Channel(platform="tiktok", source="oauth", external_id="o1", name="TT", status="connected", connected_at=now - timedelta(days=5))
        gone = Channel(platform="facebook", source="oauth", external_id="pg", name="Old", status="disconnected")
        db.add_all([yt, tt, gone])
        db.flush()

        def day(offset):
            return (now - timedelta(days=offset)).strftime("%Y-%m-%d")

        db.add_all([
            ChannelSnapshot(channel_id=yt.id, date=day(30), followers=900, views=9000),
            ChannelSnapshot(channel_id=yt.id, date=day(10), followers=1000, views=10000),
            ChannelSnapshot(channel_id=yt.id, date=day(0), followers=1100, views=12000, video_count=5),
            ChannelSnapshot(channel_id=tt.id, date=day(3), followers=500, views=4000),
            ChannelSnapshot(channel_id=tt.id, date=day(0), followers=600, views=5000, video_count=8),
            ChannelSnapshot(channel_id=gone.id, date=day(0), followers=99999),
        ])
        db.add_all([
            ChannelVideo(channel_id=yt.id, external_id="a", title="A", views=3000, likes=300, comments=30, published_at=now - timedelta(days=2)),
            ChannelVideo(channel_id=tt.id, external_id="b", title="B", views=7000, likes=700, shares=70, published_at=now - timedelta(days=1)),
            ChannelVideo(channel_id=tt.id, external_id="c", title="Old", views=99000, published_at=now - timedelta(days=200)),
        ])
        job = MediaJob(video_url="https://example.com/v")
        db.add(job)
        db.commit()
        return {"yt": yt.id, "tt": tt.id, "job": job.id}


def test_dashboard_aggregates(channels_env):
    ids = _seed_dashboard(channels_env)
    data = channels_env["client"].get("/api/channels/dashboard", params={"days": 28}).json()
    totals = data["totals"]
    assert totals["channels"] == 2  # disconnected excluded by default
    assert totals["followers"] == 1700 and totals["views"] == 17000 and totals["video_count"] == 13
    # YT: 1100 - 900 (value carried forward to window start); TT: 600 - 500 (first in window).
    assert totals["followers_change"] == 300
    assert totals["videos_in_period"] == 2 and totals["period_video_views"] == 10000
    assert totals["engagement_rate"] == round((300 + 30 + 700 + 70) / 10000, 4)
    platforms = {p["platform"]: p for p in data["per_platform"]}
    assert platforms["youtube"]["followers"] == 1100 and platforms["facebook"]["channels"] == 0
    assert len(data["series"]) == 28
    assert data["series"][0]["followers"] == 900  # YT carried forward, TT not yet present
    assert data["series"][-1]["followers"] == 1700
    assert data["series"][-1]["by_platform"]["tiktok"]["views"] == 5000
    assert data["top_videos_scope"] == "period"
    assert [v["title"] for v in data["top_videos"]] == ["B", "A"]
    types = [a["type"] for a in data["recent_activity"]]
    assert "video_published" in types and "channel_connected" in types

    seven = channels_env["client"].get("/api/channels/dashboard", params={"days": 7, "platform": "tiktok"}).json()
    assert seven["totals"]["channels"] == 1 and [p["platform"] for p in seven["per_platform"]] == ["tiktok"]
    assert len(seven["series"]) == 7
    assert channels_env["client"].get("/api/channels/dashboard", params={"platform": "x"}).status_code == 404
    with_gone = channels_env["client"].get("/api/channels/dashboard", params={"include_disconnected": True}).json()
    assert with_gone["totals"]["channels"] == 3


def test_dashboard_empty(channels_env):
    data = channels_env["client"].get("/api/channels/dashboard", params={"days": 90}).json()
    assert data["totals"]["channels"] == 0 and data["top_videos"] == [] and len(data["series"]) == 90
    assert data["totals"]["engagement_rate"] is None


def test_dashboard_top_videos_fallback_all_time(channels_env):
    now = utcnow()
    with channels_env["Session"]() as db:
        ch = Channel(platform="youtube", source="manual", name="Y", status="manual")
        db.add(ch)
        db.flush()
        db.add(ChannelVideo(channel_id=ch.id, external_id="z", title="Z", views=5, published_at=now - timedelta(days=400)))
        db.commit()
        data = dashboard.build_dashboard(db, days=7)
    assert data["top_videos_scope"] == "all_time" and data["top_videos"][0]["title"] == "Z"


def test_link_video_to_media_job(channels_env):
    ids = _seed_dashboard(channels_env)
    client = channels_env["client"]
    video_id = client.get(f"/api/channels/{ids['tt']}/videos").json()[0]["id"]
    assert client.patch(f"/api/channels/videos/{video_id}", json={"media_job_id": "missing"}).status_code == 404
    linked = client.patch(f"/api/channels/videos/{video_id}", json={"media_job_id": ids["job"]}).json()
    assert linked["media_job_id"] == ids["job"]
    assert [v["id"] for v in client.get(f"/api/channels/jobs/{ids['job']}/videos").json()] == [video_id]
    assert client.patch(f"/api/channels/videos/{video_id}", json={"media_job_id": None}).json()["media_job_id"] is None


def test_main_app_registers_channels_router():
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/channels/dashboard" in paths and "/api/channels/oauth/{platform}/callback" in paths


def test_list_channels_filter(channels_env):
    _seed_dashboard(channels_env)
    client = channels_env["client"]
    assert len(client.get("/api/channels").json()) == 3
    assert [c["name"] for c in client.get("/api/channels", params={"platform": "tiktok"}).json()] == ["TT"]
    assert client.get("/api/channels/nope").status_code == 404
    assert isinstance(datetime.fromisoformat(client.get("/api/channels").json()[0]["created_at"]), datetime)
