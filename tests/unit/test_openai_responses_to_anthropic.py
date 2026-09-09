"""Unit tests for OpenAIResponsesToAnthropicConverter."""

import pytest

from app.converters.openai_responses_to_anthropic import (
    OpenAIResponsesToAnthropicConverter,
)
from app.core.exceptions import BedrockAPIError
from app.schemas.anthropic import MessageResponse, TextContent, ToolUseContent


def _converter() -> OpenAIResponsesToAnthropicConverter:
    return OpenAIResponsesToAnthropicConverter()


def test_single_message_output_text():
    resp = {
        "id": "resp_abc123",
        "model": "upstream-model",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Hello world", "annotations": []}
                ],
            }
        ],
        "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
    }

    result = _converter().convert_response(resp, model="my-model")

    assert isinstance(result, MessageResponse)
    assert result.id == "resp_abc123"
    assert result.type == "message"
    assert result.role == "assistant"
    assert result.model == "my-model"
    assert result.stop_reason == "end_turn"
    assert result.stop_sequence is None
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert block.text == "Hello world"


def test_reasoning_skipped_and_function_call_emitted():
    resp = {
        "id": "resp_xyz",
        "model": "upstream-model",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "function_call",
                "call_id": "call_0",
                "name": "web_search",
                "arguments": '{"query": "weather today"}',
                "id": "fc_1",
                "status": "completed",
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }

    result = _converter().convert_response(resp, model="m")

    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, ToolUseContent)
    assert block.id == "call_0"  # call_id, not the fc_ id
    assert block.name == "web_search"
    assert block.input == {"query": "weather today"}
    assert result.stop_reason == "tool_use"


def test_usage_mapping():
    resp = {
        "id": "resp_u",
        "model": "m",
        "output": [],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 20},
            "output_tokens_details": {"reasoning_tokens": 12},
            "total_tokens": 150,
        },
    }

    result = _converter().convert_response(resp, model="m")

    assert result.usage.input_tokens == 50
    assert result.usage.output_tokens == 50
    assert result.usage.cache_read_input_tokens == 30
    assert result.usage.cache_creation_input_tokens == 20
    assert result.usage.reasoning_tokens == 12


def test_usage_missing_fields_default_to_zero():
    resp = {"id": "resp_e", "model": "m", "output": []}

    result = _converter().convert_response(resp, model="m")

    assert result.usage.input_tokens == 0
    assert result.usage.output_tokens == 0
    assert result.usage.cache_read_input_tokens is None
    assert result.usage.cache_creation_input_tokens is None
    assert result.usage.reasoning_tokens is None


def test_mixed_reasoning_function_call_and_message():
    resp = {
        "id": "resp_mix",
        "model": "m",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "function_call",
                "call_id": "call_5",
                "name": "lookup",
                "arguments": '{"q": "x"}',
                "id": "fc_5",
                "status": "completed",
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done", "annotations": []}],
            },
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    result = _converter().convert_response(resp, model="m")

    assert len(result.content) == 2
    assert isinstance(result.content[0], ToolUseContent)
    assert result.content[0].id == "call_5"
    assert isinstance(result.content[1], TextContent)
    assert result.content[1].text == "done"
    assert result.stop_reason == "tool_use"


def test_missing_id_generates_msg_id():
    resp = {
        "model": "m",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hi", "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    result = _converter().convert_response(resp, model="m")

    assert result.id.startswith("msg_")


def test_only_reasoning_yields_empty_text_block():
    resp = {
        "id": "resp_only_reasoning",
        "model": "m",
        "output": [{"type": "reasoning", "id": "rs_1", "summary": []}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    result = _converter().convert_response(resp, model="m")

    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert block.text == ""
    assert result.stop_reason == "end_turn"


def test_function_call_invalid_arguments_defaults_to_empty_dict():
    resp = {
        "id": "resp_bad",
        "model": "m",
        "output": [
            {
                "type": "function_call",
                "call_id": "call_9",
                "name": "tool",
                "arguments": "not valid json",
                "id": "fc_9",
                "status": "completed",
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    result = _converter().convert_response(resp, model="m")

    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, ToolUseContent)
    assert block.input == {}


def test_reasoning_summaries_preserved_in_output_order():
    response = _converter().convert_response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "First thought."},
                        {"type": "summary_text", "text": "Second thought."},
                    ],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Answer"}],
                },
            ],
        },
        model="m",
    )
    assert [b.type for b in response.content] == ["thinking", "thinking", "text"]
    assert response.content[0].thinking == "First thought."
    assert response.content[1].thinking == "Second thought."


@pytest.mark.parametrize("has_tool", [False, True])
def test_token_limit_takes_precedence_over_tool_stop_reason(has_tool):
    output = (
        [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": '{"q":',
            }
        ]
        if has_tool
        else []
    )
    response = _converter().convert_response(
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": output,
            "usage": {"input_tokens": 20, "output_tokens": 100},
        },
        model="m",
    )
    assert response.stop_reason == "max_tokens"
    assert response.usage.output_tokens == 100


@pytest.mark.parametrize(
    "response",
    [
        {
            "status": "failed",
            "output": [],
            "error": {"code": "server_error", "message": "broken"},
        },
        {"status": "completed", "output": [], "error": {"message": "broken"}},
        {"status": "cancelled", "output": []},
        {"status": "in_progress", "output": []},
        {"message": "Unknown operation", "__type": "UnknownOperationException"},
        {
            "status": "incomplete",
            "output": [],
            "incomplete_details": {"reason": "unknown"},
        },
    ],
)
def test_error_envelopes_are_not_empty_successes(response):
    with pytest.raises(BedrockAPIError):
        _converter().convert_response(response, model="m")


def test_content_filter_maps_to_refusal():
    response = _converter().convert_response(
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "content_filter"},
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": "Cannot help."}],
                }
            ],
        },
        model="m",
    )
    assert response.stop_reason == "refusal"
    assert response.content[0].text == "Cannot help."


@pytest.mark.parametrize(
    ("details", "expected_input"),
    [
        (None, 100),
        ({"cached_tokens": 0, "cache_write_tokens": 0}, 100),
        ({"cached_tokens": 100}, 0),
        ({"cache_write_tokens": 100}, 0),
        ({"cached_tokens": 101}, 0),
    ],
)
def test_cache_exclusive_input_and_missing_details(details, expected_input):
    usage = _converter().convert_usage(
        {"input_tokens": 100, "output_tokens": 10, "input_tokens_details": details}
    )
    assert usage.input_tokens == expected_input
    assert usage.output_tokens == 10
