"""Tests for custom->function tool downgrade."""
from app.api.openai_passthrough.chat_responses_adapter import downgrade_custom_tools


def test_custom_tool_becomes_function_with_input_string():
    body = {"model": "m", "tools": [
        {"type": "custom", "name": "apply_patch", "description": "Apply a patch"}
    ]}
    assert downgrade_custom_tools(body) == ["apply_patch"]
    tool = body["tools"][0]
    assert tool["type"] == "function"
    assert tool["name"] == "apply_patch"
    assert tool["description"] == "Apply a patch"
    props = tool["parameters"]["properties"]
    assert set(props) == {"input"}
    assert props["input"]["type"] == "string"
    assert tool["parameters"]["required"] == ["input"]


def test_function_and_mcp_tools_untouched():
    original = [
        {"type": "function", "name": "f", "parameters": {"type": "object"}},
        {"type": "mcp", "server_label": "s"},
    ]
    body = {"tools": [dict(t) for t in original]}
    assert downgrade_custom_tools(body) == []
    assert body["tools"] == original


def test_mixed_list_only_custom_rewritten():
    body = {"tools": [
        {"type": "function", "name": "keep"},
        {"type": "custom", "name": "conv"},
        {"type": "mcp", "server_label": "s"},
    ]}
    assert downgrade_custom_tools(body) == ["conv"]
    assert [t["type"] for t in body["tools"]] == ["function", "function", "mcp"]
    assert body["tools"][0] == {"type": "function", "name": "keep"}


def test_custom_without_name_left_alone():
    """Nothing callable to preserve — let the upstream error surface."""
    body = {"tools": [{"type": "custom", "description": "no name"}]}
    assert downgrade_custom_tools(body) == []
    assert body["tools"][0]["type"] == "custom"


def test_custom_with_format_field_dropped():
    """`format` is custom-only; it must not leak into the function tool."""
    body = {"tools": [
        {"type": "custom", "name": "t", "format": {"type": "grammar", "syntax": "lark"}}
    ]}
    assert downgrade_custom_tools(body) == ["t"]
    assert "format" not in body["tools"][0]


def test_missing_or_malformed_tools_is_noop():
    for body in ({}, {"tools": None}, {"tools": "nope"}, {"tools": []},
                 {"tools": ["str", 42, None]}):
        assert downgrade_custom_tools(body) == []


def test_no_description_omits_key():
    body = {"tools": [{"type": "custom", "name": "t"}]}
    downgrade_custom_tools(body)
    assert "description" not in body["tools"][0]
