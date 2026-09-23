from types import SimpleNamespace

import httpx

from app.services import translator


def _settings(model: str = "translate") -> SimpleNamespace:
    return SimpleNamespace(
        local_translation_base_url="http://127.0.0.1:20128/v1",
        local_translation_model=model,
        local_translation_api_key="",
    )


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", "http://127.0.0.1:20128/v1/models"),
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

    status = translator.translation_router_status()

    assert status["state"] == "ready"
    assert status["ready"] is True
