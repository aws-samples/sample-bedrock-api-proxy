"""R7: ``thinking: {"type": "disabled"}`` must never enable reasoning.

Before the fix both non-Claude converters used ``if request.thinking:`` which is
truthy for the (valid) disabled config and turned reasoning on.
"""

import pytest

from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter
from app.converters.anthropic_to_openai import AnthropicToOpenAIConverter
from app.converters.thinking import is_thinking_enabled
from app.core.config import settings
from app.schemas.anthropic import MessageRequest

NOVA_2 = "us.amazon.nova-pro-2:0"
KIMI = "moonshotai.kimi-k2.5"
OPENAI_MODEL = "openai.gpt-oss-120b-1:0"

DISABLED = {"type": "disabled"}
ENABLED = {"type": "enabled", "budget_tokens": 4096}


def _request(model: str, thinking):
    return MessageRequest(
        model=model,
        max_tokens=100,
        messages=[{"role": "user", "content": "hi"}],
        thinking=thinking,
    )


@pytest.mark.parametrize(
    "thinking, expected",
    [
        (None, False),
        (DISABLED, False),
        ({}, False),
        ({"budget_tokens": 1024}, False),
        (ENABLED, True),
        ({"type": "enabled"}, True),
    ],
)
def test_is_thinking_enabled(thinking, expected):
    assert is_thinking_enabled(thinking) is expected


# --- OpenAI-compat (Mantle chat/completions) -------------------------------


def test_openai_disabled_omits_reasoning_effort():
    result = AnthropicToOpenAIConverter().convert_request(
        _request(OPENAI_MODEL, DISABLED)
    )
    assert "reasoning_effort" not in result
    assert "extra_body" not in result


def test_openai_none_omits_reasoning_effort():
    result = AnthropicToOpenAIConverter().convert_request(_request(OPENAI_MODEL, None))
    assert "reasoning_effort" not in result


def test_openai_enabled_still_sets_reasoning_effort():
    result = AnthropicToOpenAIConverter().convert_request(
        _request(OPENAI_MODEL, ENABLED)
    )
    assert result["reasoning_effort"] == "high"
    assert result["extra_body"] == {"include_reasoning": True}


# --- Converse -----------------------------------------------------------------


@pytest.fixture
def extended_thinking_on(monkeypatch):
    monkeypatch.setattr(settings, "enable_extended_thinking", True)


def test_converse_nova2_disabled_omits_reasoning_config(extended_thinking_on):
    result = AnthropicToBedrockConverter().convert_request(_request(NOVA_2, DISABLED))
    extra = result.get("additionalModelRequestFields", {})
    assert "reasoningConfig" not in extra
    # temperature/maxTokens must not have been stripped either
    assert result["inferenceConfig"]["maxTokens"] == 100


def test_converse_nova2_enabled_unchanged(extended_thinking_on):
    result = AnthropicToBedrockConverter().convert_request(_request(NOVA_2, ENABLED))
    assert (
        result["additionalModelRequestFields"]["reasoningConfig"]["type"] == "enabled"
    )
    assert "maxTokens" not in result["inferenceConfig"]


def test_converse_kimi_disabled_omits_reasoning_effort(extended_thinking_on):
    result = AnthropicToBedrockConverter().convert_request(_request(KIMI, DISABLED))
    assert "reasoning_effort" not in result.get("additionalModelRequestFields", {})


def test_converse_kimi_enabled_unchanged(extended_thinking_on):
    result = AnthropicToBedrockConverter().convert_request(_request(KIMI, ENABLED))
    assert result["additionalModelRequestFields"]["reasoning_effort"] == "high"


def test_converse_none_unchanged(extended_thinking_on):
    result = AnthropicToBedrockConverter().convert_request(_request(KIMI, None))
    assert "reasoning_effort" not in result.get("additionalModelRequestFields", {})
