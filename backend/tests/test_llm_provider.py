"""Tests for provider resolution — the part of the pluggable LLM layer that maps the
configured provider name to a concrete (style, model, base_url, key), with overrides.
These are pure and don't touch the network (no client is constructed).
"""
import pytest

from app import llm
from app.config import Settings


def test_known_providers_present():
    assert {"deepseek", "openai", "anthropic", "local", "openai-compatible"} <= set(llm.PROVIDERS)


def test_resolve_defaults_to_preset():
    provider, model, base_url, _key = llm._resolve(Settings(llm_provider="deepseek"))
    assert provider.style == "openai"
    assert model == "deepseek-chat"
    assert base_url == "https://api.deepseek.com"


def test_resolve_applies_model_and_base_url_overrides():
    s = Settings(llm_provider="openai", llm_model="gpt-4o", llm_base_url="https://proxy.local/v1")
    _provider, model, base_url, _key = llm._resolve(s)
    assert model == "gpt-4o"
    assert base_url == "https://proxy.local/v1"


def test_resolve_unknown_provider_raises():
    with pytest.raises(RuntimeError, match="Unknown AETHER_LLM_PROVIDER"):
        llm._resolve(Settings(llm_provider="does-not-exist"))


def test_resolve_requires_a_model_for_bare_compatible_provider():
    # openai-compatible has no preset model; with no override it must fail clearly rather than
    # send an empty model to the API.
    with pytest.raises(RuntimeError, match="No model configured"):
        llm._resolve(Settings(llm_provider="openai-compatible", llm_model="", llm_base_url=""))


def test_provider_info_is_secret_free_and_flags_configured(monkeypatch):
    # openai-style providers report configured even without a key (local servers need none)...
    monkeypatch.setattr(llm, "get_settings", lambda: Settings(llm_provider="deepseek"))
    info = llm.provider_info()
    assert info["configured"] is True
    assert "key" not in str(info).lower() or "api_key" not in info
    assert all("sk-" not in str(val) for val in info.values())

    # ...while anthropic needs a real key, so an empty one reports not-configured.
    monkeypatch.setattr(llm, "get_settings",
                        lambda: Settings(llm_provider="anthropic", anthropic_api_key=""))
    assert llm.provider_info()["configured"] is False
