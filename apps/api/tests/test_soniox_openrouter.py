from pathlib import Path
from types import SimpleNamespace

import httpx

from app.services import subtitle, translator


def _token(text: str, start_ms: int, end_ms: int, **extra) -> dict:
    return {"text": text, "start_ms": start_ms, "end_ms": end_ms, **extra}


def test_soniox_tokens_group_into_sentences_and_words() -> None:
    tokens = [
        _token("Hel", 0, 200, language="en"),
        _token("lo", 200, 400, language="en"),
        _token(" world.", 450, 1200, language="en"),
        _token(" Next", 1300, 1600, language="en"),
        _token(" line", 1600, 1900, language="en"),
        # A long pause closes the span even without punctuation.
        _token(" after", 3500, 3800, language="en"),
        _token(" pause", 3800, 4100, language="en"),
    ]

    spans = subtitle._soniox_tokens_to_spans(tokens)

    assert [span.raw_text for span in spans] == ["Hello world.", "Next line", "after pause"]
    assert spans[0].start == 0.0 and spans[0].end == 1.2
    assert [word.text for word in spans[0].words] == ["Hello", "world."]
    assert spans[0].detected_language == "en"
    assert all(span.timestamp_quality == "observed" for span in spans)


def test_soniox_tokens_split_on_speaker_change_and_skip_translations() -> None:
    tokens = [
        _token("你好", 0, 500, speaker="1"),
        _token("世界", 500, 900, speaker="2"),
        _token(" hello", 500, 900, translation_status="translation"),
    ]

    spans = subtitle._soniox_tokens_to_spans(tokens)

    assert [span.raw_text for span in spans] == ["你好", "世界"]


def test_soniox_request_uploads_polls_and_cleans_up(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "job.stt.wav"
    audio.write_bytes(b"RIFF")
    monkeypatch.setenv("SONIOX_API_KEY", "soniox-key")
    monkeypatch.setattr(subtitle.time, "sleep", lambda _seconds: None)
    calls: list[tuple[str, str]] = []
    statuses = iter(["queued", "processing", "completed"])
    created_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer soniox-key"
        if request.method == "POST" and request.url.path == "/v1/files":
            return httpx.Response(201, json={"id": "file-1"})
        if request.method == "POST" and request.url.path == "/v1/transcriptions":
            created_body.update(__import__("json").loads(request.content))
            return httpx.Response(201, json={"id": "tr-1"})
        if request.method == "GET" and request.url.path == "/v1/transcriptions/tr-1":
            return httpx.Response(200, json={"status": next(statuses)})
        if request.url.path == "/v1/transcriptions/tr-1/transcript":
            return httpx.Response(200, json={"tokens": [_token("Hi.", 0, 400)]})
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    real_client = httpx.Client
    monkeypatch.setattr(
        subtitle.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    tokens = subtitle._request_soniox_tokens(audio, "Chinese")

    assert tokens == [_token("Hi.", 0, 400)]
    assert created_body["model"] == "stt-async-v5"
    assert created_body["file_id"] == "file-1"
    assert created_body["language_hints"] == ["zh"]
    assert ("DELETE", "/v1/transcriptions/tr-1") in calls
    assert ("DELETE", "/v1/files/file-1") in calls


def _openrouter_settings(**overrides) -> SimpleNamespace:
    values = {
        "translation_provider": "openrouter",
        "openrouter_api_key": "sk-or-test",
        "openrouter_model": "qwen/qwen3.7-flash",
        "local_translation_base_url": "http://127.0.0.1:20128/v1",
        "local_translation_model": "translate",
        "local_translation_api_key": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_openrouter_translation_uses_selected_model_without_reasoning(monkeypatch) -> None:
    monkeypatch.setattr(translator, "get_runtime_settings", _openrouter_settings)
    monkeypatch.delenv("AETHER_OPENROUTER_REASONING", raising=False)
    sent: dict = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: int) -> httpx.Response:
        sent.update(url=url, headers=headers, json=json)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Xin chào"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(translator.httpx, "post", fake_post)

    content = translator._request_translation_completion(translator._translation_model(), "sys", "user")

    assert content == "Xin chào"
    assert sent["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer sk-or-test"
    assert sent["json"]["model"] == "qwen/qwen3.7-flash"
    assert sent["json"]["reasoning"] == {"enabled": False}


def test_openrouter_status_without_key_does_not_mention_9router(monkeypatch) -> None:
    monkeypatch.setattr(translator, "get_runtime_settings", lambda: _openrouter_settings(openrouter_api_key=""))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    status = translator.translation_router_status()

    assert status["ready"] is False
    assert status["state"] == "provider_not_configured"
    assert "9router" not in status["message"]


def test_openrouter_credit_error_is_actionable(monkeypatch) -> None:
    monkeypatch.setattr(translator, "get_runtime_settings", _openrouter_settings)
    monkeypatch.setattr(
        translator.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(
            402,
            json={"error": {"message": "Insufficient credits"}},
            request=httpx.Request("POST", url),
        ),
    )

    status = translator.translation_router_status()

    assert status["ready"] is False
    assert "credit" in status["message"]
    assert "9router" not in status["message"]
