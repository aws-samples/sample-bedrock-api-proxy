"""Tests for mid-conversation tool changes (beta mid-conversation-tool-changes-2026-07-01).

The client declares the full tool set in `tools` once, then offers or withdraws
individual tools with `tool_addition` / `tool_removal` blocks inside a
`role: "system"` message. The proxy must accept those blocks and forward them
unchanged on the InvokeModel path; the Converse API has no equivalent, so
system-role messages are dropped there.
"""

import pytest

from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter
from app.schemas.anthropic import MessageRequest
from app.services.bedrock_service import BedrockService

TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the current weather for a location.",
        "input_schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    }
]


@pytest.fixture
def service():
    """BedrockService without touching AWS (skip __init__, we only need converters)."""
    return BedrockService.__new__(BedrockService)


def _request(system_content):
    return MessageRequest(
        model="claude-opus-5",
        max_tokens=64,
        tools=TOOLS,
        messages=[
            {"role": "user", "content": "Say OK."},
            {"role": "system", "content": system_content},
        ],
    )


# --- Schema validation ---


def test_tool_removal_with_tool_reference_validates():
    req = _request(
        [
            {
                "type": "tool_removal",
                "tool": {"type": "tool_reference", "name": "get_weather"},
            }
        ]
    )
    block = req.messages[-1].content[0]
    assert block.type == "tool_removal"
    assert block.tool.name == "get_weather"


def test_tool_addition_with_mcp_tool_reference_validates():
    req = _request(
        [
            {
                "type": "tool_addition",
                "tool": {
                    "type": "mcp_tool_reference",
                    "server_name": "github",
                    "name": "list_prs",
                },
            }
        ]
    )
    block = req.messages[-1].content[0]
    assert block.type == "tool_addition"
    assert block.tool.server_name == "github"
    assert block.tool.name == "list_prs"


def test_tool_addition_with_mcp_toolset_reference_validates():
    req = _request(
        [
            {
                "type": "tool_addition",
                "tool": {"type": "mcp_toolset_reference", "server_name": "github"},
            }
        ]
    )
    assert req.messages[-1].content[0].tool.type == "mcp_toolset_reference"


def test_text_blocks_can_be_mixed_with_tool_changes():
    req = _request(
        [
            {"type": "text", "text": "Weather lookups are disabled for this session."},
            {
                "type": "tool_removal",
                "tool": {"type": "tool_reference", "name": "get_weather"},
            },
        ]
    )
    assert [b.type for b in req.messages[-1].content] == ["text", "tool_removal"]


def test_tool_change_block_accepts_cache_control():
    req = _request(
        [
            {
                "type": "tool_removal",
                "tool": {"type": "tool_reference", "name": "get_weather"},
                "cache_control": {"type": "ephemeral"},
            }
        ]
    )
    assert req.messages[-1].content[0].cache_control.type == "ephemeral"


def test_tool_result_tool_reference_shape_still_validates():
    """The pre-existing tool_result child block (field `tool_name`) is a different
    shape that shares the `tool_reference` type string. It must keep working."""
    req = MessageRequest(
        model="claude-opus-5",
        max_tokens=64,
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [
                            {"type": "tool_reference", "tool_name": "get_weather"}
                        ],
                    }
                ],
            },
        ],
    )
    assert req.messages[-1].content[0].content[0].tool_name == "get_weather"


# --- InvokeModel passthrough ---


def test_native_request_forwards_tool_change_blocks(service):
    req = _request(
        [
            {"type": "text", "text": "Weather lookups are disabled."},
            {
                "type": "tool_removal",
                "tool": {"type": "tool_reference", "name": "get_weather"},
            },
        ]
    )
    body = service._convert_to_anthropic_native_request(
        req, "mid-conversation-tool-changes-2026-07-01"
    )

    system_msg = body["messages"][-1]
    assert system_msg["role"] == "system"
    assert system_msg["content"] == [
        {"type": "text", "text": "Weather lookups are disabled."},
        {
            "type": "tool_removal",
            "tool": {"type": "tool_reference", "name": "get_weather"},
        },
    ]
    assert body["anthropic_beta"] == ["mid-conversation-tool-changes-2026-07-01"]


def test_native_request_preserves_mcp_toolset_reference(service):
    req = _request(
        [
            {
                "type": "tool_addition",
                "tool": {"type": "mcp_toolset_reference", "server_name": "github"},
            }
        ]
    )
    body = service._convert_to_anthropic_native_request(req)

    assert body["messages"][-1]["content"] == [
        {
            "type": "tool_addition",
            "tool": {"type": "mcp_toolset_reference", "server_name": "github"},
        }
    ]


# --- Converse path drops system-role messages ---


def test_converse_conversion_skips_system_messages():
    converter = AnthropicToBedrockConverter()
    req = _request(
        [
            {
                "type": "tool_removal",
                "tool": {"type": "tool_reference", "name": "get_weather"},
            }
        ]
    )

    messages = converter._convert_messages(req.messages)

    assert [m["role"] for m in messages] == ["user"]
