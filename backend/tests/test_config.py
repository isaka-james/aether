"""Tests for settings: the safe-by-default values and the well-known third-party env aliases
(so a key pasted in under its conventional name is actually picked up).
"""
from app.config import Settings


def test_safe_defaults():
    s = Settings()
    assert s.llm_provider == "deepseek"
    assert s.require_confirm_medium_risk is True
    assert s.subagents_enabled is True
    assert s.refine_request is True
    assert s.verify_actions is True
    # Persistence is opt-in: empty by default so the app runs with no external services.
    assert s.database_url == ""
    assert s.redis_url == ""


def test_aether_prefix_override(monkeypatch):
    monkeypatch.setenv("AETHER_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AETHER_REQUIRE_CONFIRM_MEDIUM_RISK", "false")
    s = Settings()
    assert s.llm_provider == "openai"
    assert s.require_confirm_medium_risk is False


def test_conventional_key_aliases(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-456")
    monkeypatch.setenv("ROOT_PWD", "hunter2")
    s = Settings()
    assert s.deepseek_api_key == "dk-123"
    assert s.anthropic_api_key == "ak-456"
    assert s.root_password == "hunter2"


def test_timezone_reads_tz_or_aether_tz(monkeypatch):
    monkeypatch.delenv("AETHER_TZ", raising=False)
    monkeypatch.setenv("TZ", "Europe/Berlin")
    assert Settings().timezone == "Europe/Berlin"
    monkeypatch.setenv("AETHER_TZ", "Asia/Tokyo")  # AETHER_TZ takes priority
    assert Settings().timezone == "Asia/Tokyo"
