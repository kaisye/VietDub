"""Music library API: uploads, edits, Openverse / Jamendo (mocked) and project credits."""
from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import editor, music_library, storage

OPENVERSE_ITEM = {
    "id": "94594ba3-4ba4-4751-af12-6b211ef9b0fe",
    "title": "Morning Lofi",
    "foreign_landing_url": "https://www.jamendo.com/track/1",
    "url": "https://prod-1.storage.jamendo.com/?trackid=1&format=mp32",
    "creator": "JekK",
    "license": "by",
    "license_version": "4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "source": "jamendo",
    "category": "music",
    "filetype": "mp32",
    "tags": [{"name": "chill"}, {"name": "lofi"}],
    "attribution": '"Morning Lofi" by JekK is licensed under CC BY 4.0.',
    "duration": 164000,
}


def _tone(path: Path, seconds: float = 2.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=330:d={seconds}", str(path)],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path)
    storage.ensure_storage()
    music_library._SEARCH_CACHE.clear()
    return TestClient(app)


def test_upload_list_rename_tag_delete(client, tmp_path) -> None:
    wav = _tone(tmp_path / "Nhạc nền.wav")
    with wav.open("rb") as handle:
        response = client.post(
            "/music-library/upload",
            files=[("files", ("Nhạc nền.wav", handle, "audio/wav")), ("files", ("notes.txt", b"hi", "text/plain"))],
        )
    assert response.status_code == 200, response.text
    body = response.json()
    [asset] = body["items"]
    assert asset["title"] == "Nhạc nền" and asset["license"] == "own" and asset["commercial_ok"]
    assert body["errors"][0]["filename"] == "notes.txt"
    assert music_library.asset_path(asset["id"]).stat().st_size > 0  # served by the /storage mount

    patched = client.patch(f"/music-library/{asset['id']}", json={"title": "Intro", "tags": ["Vui tươi"]}).json()
    assert patched["title"] == "Intro" and patched["tags"] == ["vui tươi"]
    listing = client.get("/music-library", params={"q": "vui tuoi"}).json()
    assert [item["id"] for item in listing["items"]] == [asset["id"]] and listing["tags"] == ["vui tươi"]
    assert client.get(f"/music-library/{asset['id']}/peaks").json()["peaks"]
    assert client.delete(f"/music-library/{asset['id']}").status_code == 204
    assert client.get("/music-library").json()["items"] == []
    assert client.delete(f"/music-library/{asset['id']}").status_code == 404


def test_openverse_search_filters_commercial_licenses_and_caches(client, monkeypatch) -> None:
    calls = []

    def fake_get(url, params, provider):
        calls.append((url, params))
        return {"result_count": 1, "page_count": 1, "page": 1, "results": [OPENVERSE_ITEM]}

    monkeypatch.setattr(music_library, "_get_json", fake_get)
    first = client.get("/music-library/openverse/search", params={"q": "lofi"}).json()
    client.get("/music-library/openverse/search", params={"q": "lofi"})
    assert len(calls) == 1  # cached: anonymous Openverse allows 20 requests/minute
    url, params = calls[0]
    assert url == "https://api.openverse.org/v1/audio/"
    assert params["license"] == "cc0,pdm,by,by-sa" and params["category"] == "music"
    [item] = first["items"]
    assert item["duration"] == 164.0 and item["license_label"] == "CC BY 4.0" and item["attribution_required"]
    client.get("/music-library/openverse/search", params={"q": "lofi", "commercial_only": False, "category": "sound_effect"})
    assert "license" not in calls[1][1] and calls[1][1]["source"] == "freesound" and "category" not in calls[1][1]


def test_openverse_rate_limit_is_reported(client, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "throttled"})

    monkeypatch.setattr(
        music_library, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    response = client.get("/music-library/openverse/search", params={"q": "epic"})
    assert response.status_code == 429 and "20 lượt/phút" in response.json()["detail"]


def test_openverse_import_saves_license_and_attribution_once(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(music_library, "_get_json", lambda url, params, provider: OPENVERSE_ITEM)

    def fake_download(url, suffix):
        folder = Path(music_library.library_dir()) / "dl-test"
        folder.mkdir(exist_ok=True)
        return _tone(folder / "download.mp3", 1.5)

    monkeypatch.setattr(music_library, "_download", fake_download)
    asset = client.post("/music-library/openverse/import", json={"id": OPENVERSE_ITEM["id"]}).json()
    assert asset["source"] == "openverse" and asset["license"] == "by" and asset["license_version"] == "4.0"
    assert asset["attribution"].startswith('"Morning Lofi" by JekK') and asset["source_url"].endswith("/track/1")
    again = client.post("/music-library/openverse/import", json={"id": OPENVERSE_ITEM["id"]}).json()
    assert again["id"] == asset["id"]  # no duplicate download
    assert client.post("/music-library/openverse/import", json={"id": "../etc"}).status_code == 400


def test_jamendo_needs_a_client_id_and_filters_nc(client, monkeypatch) -> None:
    assert client.get("/music-library/jamendo/search", params={"q": "epic"}).status_code == 400
    saved = client.put("/music-library/settings", json={"jamendo_client_id": "abcd1234"}).json()
    assert saved == {"jamendo_configured": True, "jamendo_client_id": "ab…34"}  # masked
    tracks = [
        {"id": "11", "name": "Free", "artist_name": "A", "duration": 90, "audio": "https://x/1.mp3",
         "license_ccurl": "http://creativecommons.org/licenses/by-sa/3.0/", "shareurl": "https://jamendo.com/t/11"},
        {"id": "12", "name": "NC", "artist_name": "B", "duration": 90, "audio": "https://x/2.mp3",
         "license_ccurl": "http://creativecommons.org/licenses/by-nc-sa/3.0/"},
    ]
    seen = {}

    def fake_get(url, params, provider):
        seen.update(params)
        return {"headers": {"status": "success", "results_count": 2}, "results": tracks}

    monkeypatch.setattr(music_library, "_get_json", fake_get)
    items = client.get("/music-library/jamendo/search", params={"q": "epic"}).json()["items"]
    assert [item["id"] for item in items] == ["11"] and items[0]["license_label"] == "CC BY-SA 3.0"
    assert seen["client_id"] == "abcd1234"
    assert "Free" in items[0]["attribution"]


def test_project_credits_list_attribution_licenses_only(client, tmp_path) -> None:
    own = music_library.import_file(_tone(tmp_path / "a.wav"), original_filename="Mine.wav")
    cc = music_library.import_file(
        _tone(tmp_path / "b.wav"), title="Epic Rise", artist="Ann", license_code="by", license_version="4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/", source_name="openverse",
        source_url="https://freesound.org/s/1", attribution='"Epic Rise" by Ann is licensed under CC BY 4.0.',
    )
    job = "job-credits"
    workspace = {
        "job_id": job, "duration": 10, "segments": [],
        "audio_tracks": [{"clips": [{"asset_id": own["id"]}, {"asset_id": cc["id"]}, {"asset_id": cc["id"]}, {"asset_id": "mus-gone"}]}],
        "render": {"status": "idle"},
    }
    editor.save_workspace(workspace)
    credits = client.get(f"/jobs/{job}/editor/music-credits").json()
    assert credits["credited_count"] == 1 and credits["missing"] == ["mus-gone"]
    assert credits["text"].splitlines() == [
        "Nhạc sử dụng trong video:",
        '- "Epic Rise" by Ann is licensed under CC BY 4.0. Source: https://freesound.org/s/1',
    ]
    assert client.get("/jobs/nope/editor/music-credits").status_code == 404


def test_openverse_import_falls_back_to_the_search_record_when_detail_fails(client, monkeypatch) -> None:
    def fake_get(url, params, provider):
        if url.endswith("/audio/"):
            return {"result_count": 1, "page_count": 1, "page": 1, "results": [OPENVERSE_ITEM]}
        raise music_library.ProviderError("Openverse trả lỗi 500.")

    monkeypatch.setattr(music_library, "_get_json", fake_get)
    monkeypatch.setattr(
        music_library, "_download", lambda url, suffix: _tone(Path(music_library.library_dir()) / "dl.mp3", 1.0)
    )
    # Never searched → the detail error is reported.
    assert client.post("/music-library/openverse/import", json={"id": "11111111-2222"}).status_code == 502
    client.get("/music-library/openverse/search", params={"q": "lofi"})
    asset = client.post("/music-library/openverse/import", json={"id": OPENVERSE_ITEM["id"]}).json()
    assert asset["title"] == "Morning Lofi" and asset["license"] == "by"
