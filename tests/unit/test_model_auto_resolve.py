"""Auto-resolving unmapped claude-* names to Bedrock inference profiles."""

from unittest.mock import Mock

import pytest

from app.core.config import settings
from app.services import model_auto_resolve as mar

PROFILES = {
    "global.anthropic.claude-fable-5-2",
    "us.anthropic.claude-fable-5-2",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "global.anthropic.claude-opus-4-6-v1",
    "global.anthropic.claude-twin-1-20250101-v1:0",
    "global.anthropic.claude-twin-1-20250601-v1:0",
    "global.openai.gpt-6.1-sol",
}


@pytest.fixture(autouse=True)
def profiles(monkeypatch):
    monkeypatch.setattr(settings, "model_auto_resolve_enabled", True)
    monkeypatch.setattr(settings, "model_auto_resolve_cache_ttl_seconds", 3600)
    loader = Mock(return_value=PROFILES)
    cache = mar._ProfileCache(loader)
    monkeypatch.setattr(mar, "_profiles", cache)
    return loader


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-fable-5-2", "global.anthropic.claude-fable-5-2"),
        ("claude-fable-5-2[1m]", "global.anthropic.claude-fable-5-2"),
        ("claude-fable-5-2-us", "us.anthropic.claude-fable-5-2"),
        ("claude-haiku-4-5", "global.anthropic.claude-haiku-4-5-20251001-v1:0"),
        ("claude-fable-9", None),  # not on Bedrock: keep pass-through behavior
        ("claude-twin-1", None),  # two dated versions: ambiguous
        ("claude-opus-4-6", None),  # "-v1" without a date is not guessed
        ("gpt-6.1-sol", None),  # only claude-* names
        ("global.anthropic.claude-fable-5-2", None),  # already a Bedrock ID
    ],
)
def test_candidate_profile_id(model, expected):
    assert mar.candidate_profile_id(model, PROFILES) == expected


def test_hit_is_saved_as_mapping():
    manager = Mock()
    assert mar.auto_resolve("claude-fable-5-2", manager) == "global.anthropic.claude-fable-5-2"
    manager.set_mapping.assert_called_once_with(
        "claude-fable-5-2", "global.anthropic.claude-fable-5-2"
    )


def test_miss_saves_nothing():
    manager = Mock()
    assert mar.auto_resolve("claude-fable-9", manager) is None
    manager.set_mapping.assert_not_called()


def test_save_failure_still_resolves():
    manager = Mock()
    manager.set_mapping.side_effect = RuntimeError("throttled")
    assert mar.auto_resolve("claude-fable-5-2", manager) == "global.anthropic.claude-fable-5-2"


def test_disabled(monkeypatch, profiles):
    monkeypatch.setattr(settings, "model_auto_resolve_enabled", False)
    assert mar.auto_resolve("claude-fable-5-2", Mock()) is None
    profiles.assert_not_called()


def test_profile_list_is_cached(profiles):
    mar.auto_resolve("claude-fable-5-2")
    mar.auto_resolve("claude-fable-5-2-us")
    assert profiles.call_count == 1


def test_listing_failure_falls_back_to_pass_through(monkeypatch):
    loader = Mock(side_effect=RuntimeError("AccessDenied"))
    monkeypatch.setattr(mar, "_profiles", mar._ProfileCache(loader))
    assert mar.auto_resolve("claude-fable-5-2") is None
    assert mar.auto_resolve("claude-fable-5-2") is None
    assert loader.call_count == 1  # failure is not retried on every request


def test_passthrough_resolver_uses_auto_resolve(monkeypatch):
    from app.api.openai_passthrough.model_mapping import resolve_model_id

    monkeypatch.setattr(settings, "default_model_mapping", {})
    manager = Mock()
    manager.get_mapping.return_value = None
    assert resolve_model_id("claude-fable-5-2", manager) == "global.anthropic.claude-fable-5-2"
    assert resolve_model_id("gpt-6.1-sol", manager) == "gpt-6.1-sol"


def test_converter_uses_auto_resolve(monkeypatch):
    from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter

    monkeypatch.setattr(settings, "default_model_mapping", {})
    converter = AnthropicToBedrockConverter(None)
    assert converter._convert_model_id("claude-fable-5-2-us") == "us.anthropic.claude-fable-5-2"
    assert converter._convert_model_id("claude-fable-9") == "claude-fable-9"
