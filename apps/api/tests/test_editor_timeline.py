"""Timeline (NLE) editor additions: split at the playhead, multi-delete, filmstrip thumbnails."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import MediaJob, MediaJobStatus
from app.services import editor

from test_editor_workspace import job_storage  # noqa: F401  (fixture)


def test_split_at_time_cuts_timing_at_the_playhead_and_text_at_a_word(job_storage) -> None:  # noqa: F811
    job, config = job_storage
    workspace = editor.patch_workspace(
        editor.build_workspace(job, config),
        [{"id": "seg-0001", "text": "Một hai ba bốn năm sáu"}],
        None,
    )

    workspace = editor.split_segment(workspace, "seg-0001", at_time=0.5)
    first, second = workspace["segments"][:2]

    assert first["end"] == 0.5 and second["start"] == 0.5 and second["end"] == 2.0
    # 25 % of the way through the cue → the word break nearest a quarter of the text.
    assert first["text"] == "Một hai" and second["text"] == "ba bốn năm sáu"
    assert second["shares_audio_with"] == "seg-0001"


def test_split_by_text_follows_the_voice_not_the_display_slot(job_storage) -> None:  # noqa: F811
    job, config = job_storage
    workspace = editor.build_workspace(job, config)
    segment = workspace["segments"][0]
    # A long display slot with a short take at its start (a pause after the voice).
    segment.update(start=0.0, end=10.0, text="Một hai ba, bốn năm sáu", audio_start=0.0, audio_duration=2.0)
    segment["audio_url"] = segment.get("audio_url") or "/storage/editor/take.mp3"

    workspace = editor.split_segment(workspace, segment["id"], at_char=len("Một hai ba,"))
    first, second = workspace["segments"][:2]

    # Cut inside the take (~half of 2 s), not at half of the 10 s slot.
    assert 0.8 <= first["end"] <= 1.2 and second["start"] == first["end"]
    assert second["end"] == 10.0 and second["shares_audio_with"] == segment["id"]


def test_split_at_time_rejects_a_playhead_outside_the_cue(job_storage) -> None:  # noqa: F811
    job, config = job_storage
    workspace = editor.build_workspace(job, config)

    with pytest.raises(ValueError):
        editor.split_segment(workspace, "seg-0001", at_time=1.95)
    with pytest.raises(ValueError):  # A single word has no break to cut at.
        editor.split_segment(workspace, "seg-0003", at_time=4.0)


def test_delete_segments_removes_several_cues_in_one_save(job_storage) -> None:  # noqa: F811
    job, config = job_storage
    workspace = editor.split_segment(
        editor.patch_workspace(editor.build_workspace(job, config), [{"id": "seg-0001", "text": "Xin chào, bạn"}], None),
        "seg-0001",
    )
    part = workspace["segments"][1]
    assert part["shares_audio_with"] == "seg-0001"

    workspace = editor.delete_segments(workspace, ["seg-0001", "seg-0003", "missing"])

    assert [segment["id"] for segment in workspace["segments"]] == [part["id"], "seg-0002"]
    assert "shares_audio_with" not in workspace["segments"][0]
    assert len(editor.load_workspace(job)["segments"]) == 2


def test_thumbnails_build_a_cached_sprite(job_storage) -> None:  # noqa: F811
    job, config = job_storage
    workspace = editor.build_workspace(job, config)

    data = editor.thumbnails(workspace)

    assert data["url"] == f"/storage/editor/{job}/thumbs.jpg"
    assert data["interval"] == 1.0 and 1 <= data["count"] <= 7
    assert data["height"] == editor.THUMBNAIL_HEIGHT and data["width"] > 0
    sprite = editor.workspace_dir(job) / "thumbs.jpg"
    assert sprite.stat().st_size > 0
    sprite.write_bytes(b"cached")  # Second call must not regenerate.
    assert editor.thumbnails(workspace) == data
    assert sprite.read_bytes() == b"cached"


@pytest.fixture
def client(job_storage):  # noqa: F811
    from app.main import app

    job, _config = job_storage
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with Session() as db:
        db.add(MediaJob(id=job, video_url="https://example.com/v", status=MediaJobStatus.ready))
        db.commit()

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app), job
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_timeline_endpoints(client) -> None:
    http, job = client
    workspace = http.get(f"/jobs/{job}/editor").json()
    assert len(workspace["segments"]) == 3

    split = http.post(f"/jobs/{job}/editor/segments/seg-0002/split", json={"at_time": 2.5})
    assert split.status_code == 200
    ids = [segment["id"] for segment in split.json()["segments"]]
    assert len(ids) == 4 and split.json()["segments"][1]["end"] == 2.5

    bad = http.post(f"/jobs/{job}/editor/segments/seg-0002/split", json={"at_time": 9})
    assert bad.status_code == 409

    deleted = http.post(f"/jobs/{job}/editor/segments/delete", json={"ids": ids[:2]})
    assert deleted.status_code == 200
    assert [segment["id"] for segment in deleted.json()["segments"]] == ids[2:]
    assert http.post(f"/jobs/{job}/editor/segments/delete", json={"ids": []}).status_code == 422

    thumbs = http.get(f"/jobs/{job}/editor/thumbnails")
    assert thumbs.status_code == 200 and thumbs.json()["count"] >= 1
    assert http.get(thumbs.json()["url"]).status_code in (200, 404)  # Static mount points at the real storage root.
