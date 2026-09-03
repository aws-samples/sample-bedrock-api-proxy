"""Unit tests for OpenAIResponsesToAnthropicConverter."""

from app.converters.openai_responses_to_anthropic import (
    OpenAIResponsesToAnthropicConverter,
)
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
            "input_tokens_details": {"cached_tokens": 30},
            "output_tokens_details": {"reasoning_tokens": 12},
            "total_tokens": 150,
        },
    }

    result = _converter().convert_response(resp, model="m")

    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50
    assert result.usage.cache_read_input_tokens == 30
    assert result.usage.reasoning_tokens == 12


def test_usage_missing_fields_default_to_zero():
    resp = {"id": "resp_e", "model": "m", "output": []}

    result = _converter().convert_response(resp, model="m")

    assert result.usage.input_tokens == 0
    assert result.usage.output_tokens == 0
    assert result.usage.cache_read_input_tokens is None
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
                "content": [
                    {"type": "output_text", "text": "done", "annotations": []}
                ],
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
