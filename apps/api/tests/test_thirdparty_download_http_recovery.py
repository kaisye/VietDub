from pathlib import Path

from app.services import thirdparty_download


class _FakeResponse:
    ok = True
    status_code = 200
    url = "https://cdn.example/video.mp4"
    headers = {"Content-Type": "video/mp4"}

    def __init__(self, chunks=None, content=b"{}"):
        self._chunks = chunks or [b"video-data"]
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    @property
    def content(self):
        if isinstance(self._content, Exception):
            raise self._content
        return self._content

    def iter_content(self, chunk_size):
        del chunk_size
        for chunk in self._chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def test_provider_api_retries_malformed_compression_with_identity(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if len(calls) == 1:
            return _FakeResponse(content=RuntimeError("incorrect header check"))
        return _FakeResponse(content=b'{"code": 0}')

    monkeypatch.setattr(thirdparty_download.requests, "request", fake_request)

    response = thirdparty_download._request_with_encoding_recovery(
        "GET", "https://api.example", headers={"Accept": "application/json"}
    )

    assert response.content == b'{"code": 0}'
    assert len(calls) == 2
    assert calls[1][2]["headers"]["Accept-Encoding"] == "identity"


def test_cdn_stream_retries_malformed_compression_with_identity(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return _FakeResponse(chunks=[b"partial", RuntimeError("Error -3 while decompressing data")])
        return _FakeResponse(chunks=[b"complete-video"])

    monkeypatch.setattr(thirdparty_download.requests, "get", fake_get)
    destination = tmp_path / "video.mp4"

    assert thirdparty_download._stream_cdn("https://cdn.example/video.mp4", destination)
    assert destination.read_bytes() == b"complete-video"
    assert calls[1][1]["headers"]["Accept-Encoding"] == "identity"


def test_unrelated_provider_error_is_not_treated_as_decoding_failure():
    assert thirdparty_download._is_content_decoding_error(RuntimeError("HTTP 403")) is False
