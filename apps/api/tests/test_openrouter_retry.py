"""OpenRouter translation calls wait out short rate limits instead of failing the job."""
from __future__ import annotations

import httpx
import pytest

from app.services import translator


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    request = httpx.Request("POST", translator.OPENROUTER_CHAT_URL)
    if status == 200:
        return httpx.Response(200, json={"choices": [{"message": {"content": "Xin chào"}}]}, request=request)
    return httpx.Response(status, json={"error": {"message": "Provider returned error", "code": status}}, headers=headers, request=request)


@pytest.fixture
def calls(monkeypatch):
    queue: list[httpx.Response] = []
    sleeps: list[float] = []
    monkeypatch.setattr(translator, "_openrouter_post", lambda *args, **kwargs: queue.pop(0))
    monkeypatch.setattr(translator.time, "sleep", sleeps.append)
    return queue, sleeps


def test_rate_limit_is_retried_with_backoff(calls) -> None:
    queue, sleeps = calls
    queue += [_response(429), _response(503, {"retry-after": "2"}), _response(200)]
    assert translator._request_openrouter_completion("m", "s", "u") == "Xin chào"
    assert sleeps == [translator.OPENROUTER_RETRY_DELAYS[0], 2.0]


def test_persistent_rate_limit_fails_with_a_clear_message(calls) -> None:
    queue, sleeps = calls
    queue += [_response(429) for _ in range(len(translator.OPENROUTER_RETRY_DELAYS) + 1)]
    with pytest.raises(RuntimeError, match="sau nhiều lần tự thử lại"):
        translator._request_openrouter_completion("m", "s", "u")
    assert len(sleeps) == len(translator.OPENROUTER_RETRY_DELAYS)


def test_other_errors_are_not_retried(calls) -> None:
    queue, sleeps = calls
    queue += [_response(401)]
    with pytest.raises(RuntimeError, match="HTTP 401"):
        translator._request_openrouter_completion("m", "s", "u")
    assert sleeps == []


def test_served_models_are_counted_and_fallbacks_flagged(calls, monkeypatch) -> None:
    queue, _sleeps = calls
    monkeypatch.setenv("AETHER_OPENROUTER_FALLBACK_MODELS", "deepseek/backup")
    monkeypatch.setattr(translator, "_openrouter_model", lambda: "qwen/main")
    request = httpx.Request("POST", translator.OPENROUTER_CHAT_URL)
    answer = lambda served: httpx.Response(  # noqa: E731
        200, json={"model": served, "choices": [{"message": {"content": "ok"}}]}, request=request
    )
    queue += [answer("qwen/main-20261001"), answer("deepseek/backup"), answer("qwen/main")]

    counts = translator.track_openrouter_models()
    for _ in range(3):
        translator._request_openrouter_completion("qwen/main", "s", "u")

    assert counts == {"qwen/main": 2, "deepseek/backup": 1}
    assert translator.translation_models_summary(counts) == (
        "Dịch bằng OpenRouter: 2 lượt qwen/main, 1 lượt deepseek/backup (dự phòng)."
    )
    assert translator.translation_models_summary({}) == ""
