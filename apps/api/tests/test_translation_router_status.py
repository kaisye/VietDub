from types import SimpleNamespace

import httpx

from app.services import translator


def _settings(model: str = "translate") -> SimpleNamespace:
    return SimpleNamespace(
        local_translation_base_url="http://127.0.0.1:20128/v1",
        local_translation_model=model,
        local_translation_api_key="",
    )


def _response(status: int, payload: dict, method: str = "GET", path: str = "/v1/models") -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request(method, f"http://127.0.0.1:20128{path}"),
    )


def test_router_status_reports_provider_not_configured_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(translator, "get_runtime_settings", _settings)
    request_headers: dict[str, str] = {}

    def fake_get(url: str, *, headers: dict[str, str], timeout: int) -> httpx.Response:
        assert url == "http://127.0.0.1:20128/v1/models"
        assert timeout == 10
        request_headers.update(headers)
        return _response(401, {"error": "Provider login required"})

    monkeypatch.setattr(translator.httpx, "get", fake_get)

    status = translator.translation_router_status()

    assert status["state"] == "provider_not_configured"
    assert status["running"] is True
    assert status["ready"] is False
    assert "Authorization" not in request_headers


def test_router_status_lists_models_when_selected_combo_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(translator, "get_runtime_settings", lambda: _settings("translate"))
    monkeypatch.setattr(
        translator.httpx,
        "get",
        lambda *args, **kwargs: _response(200, {"data": [{"id": "cx/model-b"}, {"id": "cx/model-a"}]}),
    )

    status = translator.translation_router_status()

    assert status["state"] == "model_not_found"
    assert status["models"] == ["cx/model-a", "cx/model-b"]


def test_router_status_is_ready_for_configured_combo(monkeypatch) -> None:
    monkeypatch.setattr(translator, "get_runtime_settings", _settings)
    monkeypatch.setattr(
        translator.httpx,
        "get",
        lambda *args, **kwargs: _response(200, {"data": [{"id": "translate"}]}),
    )
    request_headers: dict[str, str] = {}

    def fake_post(url, *, headers, json, timeout):
        assert url == "http://127.0.0.1:20128/v1/chat/completions"
        assert json["model"] == "translate"
        assert json["max_tokens"] == 8
        assert timeout == 30
        request_headers.update(headers)
        return _response(
            200,
            {"choices": [{"message": {"content": "OK"}}]},
            method="POST",
            path="/v1/chat/completions",
        )

    monkeypatch.setattr(translator.httpx, "post", fake_post)

    status = translator.translation_router_status()

    assert status["state"] == "ready"
    assert status["ready"] is True
    assert "Authorization" not in request_headers


def test_router_model_listing_is_not_ready_when_real_request_cannot_route(monkeypatch) -> None:
    monkeypatch.setattr(translator, "get_runtime_settings", _settings)
    monkeypatch.setattr(
        translator.httpx,
        "get",
        lambda *args, **kwargs: _response(200, {"data": [{"id": "translate"}]}),
    )
    monkeypatch.setattr(
        translator.httpx,
        "post",
        lambda *args, **kwargs: _response(
            401,
            {"error": {"message": "No available provider account"}},
            method="POST",
            path="/v1/chat/completions",
        ),
    )

    status = translator.translation_router_status()

    assert status["ready"] is False
    assert status["state"] == "provider_not_configured"
    assert "gán provider" in status["message"]
    assert status["models"] == ["translate"]


def test_router_requiring_api_key_asks_for_key_and_sends_saved_key(monkeypatch) -> None:
    missing_key = {"error": {"message": "Missing API key", "type": "authentication_error", "code": "invalid_api_key"}}
    monkeypatch.setattr(translator, "get_runtime_settings", _settings)
    monkeypatch.setattr(translator.httpx, "get", lambda *args, **kwargs: _response(401, missing_key))

    status = translator.translation_router_status()

    assert status["state"] == "provider_not_configured"
    assert "API key 9router" in status["message"]

    sent: list[dict[str, str]] = []

    def keyed_settings() -> SimpleNamespace:
        settings = _settings("vietdub")
        settings.local_translation_api_key = "sk-test"
        return settings

    def fake_get(url: str, *, headers: dict[str, str], timeout: int) -> httpx.Response:
        sent.append(headers)
        return _response(200, {"data": [{"id": "vietdub"}]})

    def fake_post(url: str, *, headers: dict[str, str], json: dict, timeout: int) -> httpx.Response:
        sent.append(headers)
        return _response(
            200,
            {"choices": [{"message": {"content": "OK"}}]},
            method="POST",
            path="/v1/chat/completions",
        )

    monkeypatch.setattr(translator, "get_runtime_settings", keyed_settings)
    monkeypatch.setattr(translator.httpx, "get", fake_get)
    monkeypatch.setattr(translator.httpx, "post", fake_post)

    status = translator.translation_router_status()

    assert status["ready"] is True
    assert [h.get("Authorization") for h in sent] == ["Bearer sk-test", "Bearer sk-test"]
