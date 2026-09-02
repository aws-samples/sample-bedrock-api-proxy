"""OpenAI-compat request conversion must use ``max_completion_tokens``.

OpenAI deprecated ``max_tokens`` for Chat Completions, and gpt-5.6 models on
Bedrock Mantle reject it outright::

    400 {'code': 'unsupported_parameter',
         'message': "Unsupported parameter: 'max_tokens' is not supported with this model."}

``max_completion_tokens`` is accepted by every model reachable through this
path (verified 2026-09-02 on gpt-5.6-sol, gpt-5.4, grok-4.3, kimi-k2.5, glm-5,
minimax-m2.5, gpt-oss-120b).
"""

import pytest

from app.converters.anthropic_to_openai import AnthropicToOpenAIConverter
from app.schemas.anthropic import MessageRequest


@pytest.fixture
def converter() -> AnthropicToOpenAIConverter:
    return AnthropicToOpenAIConverter()


def _request(**overrides) -> MessageRequest:
    payload = {
        "model": "openai.gpt-5.6-sol",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": "Say hi"}],
    }
    payload.update(overrides)
    return MessageRequest(**payload)


def test_convert_request_uses_max_completion_tokens(converter):
    result = converter.convert_request(_request(max_tokens=200))

    assert result["max_completion_tokens"] == 200
    assert "max_tokens" not in result


def test_convert_request_preserves_requested_limit(converter):
    result = converter.convert_request(_request(max_tokens=4096))

    assert result["max_completion_tokens"] == 4096
