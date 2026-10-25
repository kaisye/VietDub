from __future__ import annotations

import subprocess

from app.services import ffmpeg_compat


def _completed(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["ffmpeg"], 0, stdout=stdout, stderr=stderr)


def test_uses_legacy_filter_script_option_when_advertised(monkeypatch) -> None:
    ffmpeg_compat.complex_filter_file_option.cache_clear()
    monkeypatch.setattr(
        ffmpeg_compat.subprocess,
        "run",
        lambda *args, **kwargs: _completed(stderr="-filter_complex_script filename"),
    )

    assert ffmpeg_compat.complex_filter_file_option() == "-filter_complex_script"


def test_uses_modern_filter_file_option_for_ffmpeg_9(monkeypatch) -> None:
    ffmpeg_compat.complex_filter_file_option.cache_clear()
    monkeypatch.setattr(
        ffmpeg_compat.subprocess,
        "run",
        lambda *args, **kwargs: _completed(stderr="-filter_complex <graph_description>"),
    )

    assert ffmpeg_compat.complex_filter_file_option() == "-/filter_complex"


def test_defaults_to_modern_option_when_detection_fails(monkeypatch) -> None:
    ffmpeg_compat.complex_filter_file_option.cache_clear()

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 15)

    monkeypatch.setattr(ffmpeg_compat.subprocess, "run", fail)

    assert ffmpeg_compat.complex_filter_file_option() == "-/filter_complex"
