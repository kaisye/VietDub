from pathlib import Path
from types import ModuleType, SimpleNamespace

from app.services import downloader


def test_decompression_error_retries_without_http_compression(monkeypatch, tmp_path):
    attempts: list[dict] = []
    raw_dir = tmp_path / "raw-videos"
    raw_dir.mkdir()

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, _urls):
            attempts.append(self.options)
            if len(attempts) == 1:
                raise RuntimeError("Error -3 while decompressing data: incorrect header check")
            (raw_dir / "preview-1.mp4").write_bytes(b"video")

    fake_module = ModuleType("yt_dlp")
    fake_module.YoutubeDL = FakeYoutubeDL
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake_module)
    monkeypatch.setattr(downloader, "_format_selectors_for_quality", lambda _quality: ["best"])
    monkeypatch.setattr(downloader, "_reject_unsupported_live_source", lambda *_args: None)
    monkeypatch.setattr(downloader, "_validate_video_file", lambda _path: None)

    result = downloader._download_with_ytdlp(
        "https://example.net/watch/123",
        tmp_path,
        SimpleNamespace(id="preview-1", download_quality="reliable", source_language="auto"),
        download_subtitles=False,
    )

    assert result == raw_dir / "preview-1.mp4"
    assert len(attempts) == 2
    assert attempts[1]["http_headers"]["Accept-Encoding"] == "identity"


def test_unrelated_download_error_does_not_trigger_identity_retry():
    assert downloader._is_http_decompression_error(RuntimeError("HTTP Error 403")) is False


def test_nested_decompression_error_is_detected():
    try:
        try:
            raise RuntimeError("incorrect header check")
        except RuntimeError as cause:
            raise RuntimeError("yt-dlp failed") from cause
    except RuntimeError as error:
        assert downloader._is_http_decompression_error(error) is True
