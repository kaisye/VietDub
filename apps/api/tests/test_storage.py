from pathlib import Path

from app.services import storage


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_cleanup_treats_inactive_raw_videos_as_temporary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path)
    active_id = "active-job"
    finished_id = "finished-job"

    active_raw = tmp_path / "raw-videos" / f"{active_id}.mp4"
    finished_raw = tmp_path / "raw-videos" / f"{finished_id}.mp4"
    finished_sidecar = tmp_path / "raw-videos" / f"{finished_id}.vi.vtt"
    rendered_output = tmp_path / "rendered-outputs" / f"{finished_id}.mp4"
    source_upload = tmp_path / "source-uploads" / f"{finished_id}.mov"
    for path in (active_raw, finished_raw, finished_sidecar, rendered_output, source_upload):
        _write(path, 8)

    removed = storage.cleanup_temporary_artifacts([active_id])

    assert removed > 0
    assert active_raw.exists()
    assert not finished_raw.exists()
    assert not finished_sidecar.exists()
    assert rendered_output.exists()
    assert source_upload.exists()
