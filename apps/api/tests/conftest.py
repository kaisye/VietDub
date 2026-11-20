import pytest


@pytest.fixture(autouse=True)
def _no_term_check_by_default(monkeypatch):
    """The name/number check makes extra LLM calls; tests that want it turn it on."""
    monkeypatch.setenv("AETHER_TERM_CHECK", "0")


@pytest.fixture(autouse=True)
def _no_watermark_from_the_real_settings(monkeypatch, tmp_path):
    """Renders must not pick up the watermark the developer turned on in their own app."""
    from app.services import watermark

    monkeypatch.setattr(watermark, "_settings_path", lambda: tmp_path / "watermark.json")
