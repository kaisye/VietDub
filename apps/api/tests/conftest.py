import pytest


@pytest.fixture(autouse=True)
def _no_term_check_by_default(monkeypatch):
    """The name/number check makes extra LLM calls; tests that want it turn it on."""
    monkeypatch.setenv("AETHER_TERM_CHECK", "0")
