"""Tests for downgrading Responses-API tool variants mantle does not accept.

bedrock-mantle implements only `function` and `mcp`. Any other variant fails
deserialization and rejects the ENTIRE tools array, so one unsupported tool
disables every tool in the request — hence rewriting rather than dropping.
"""
from app.api.openai_passthrough.chat_responses_adapter import (
    downgrade_unsupported_tools,
)


def _names(body):
    return [t.get("name") for t in body["tools"]]


class TestCustomTool:
    def test_becomes_function_with_input_string(self):
        body = {"tools": [
            {"type": "custom", "name": "apply_patch", "description": "Apply a patch"}
        ]}
        assert downgrade_unsupported_tools(body) == ["apply_patch"]
        tool = body["tools"][0]
        assert tool["type"] == "function"
        assert tool["name"] == "apply_patch"
        assert tool["description"] == "Apply a patch"
        props = tool["parameters"]["properties"]
        assert set(props) == {"input"}
        assert props["input"]["type"] == "string"
        assert tool["parameters"]["required"] == ["input"]

    def test_format_field_dropped(self):
        """`format` is custom-only and meaningless on a function tool."""
        body = {"tools": [
            {"type": "custom", "name": "t",
             "format": {"type": "grammar", "syntax": "lark"}}
        ]}
        assert downgrade_unsupported_tools(body) == ["t"]
        assert "format" not in body["tools"][0]

    def test_no_description_omits_key(self):
        body = {"tools": [{"type": "custom", "name": "t"}]}
        downgrade_unsupported_tools(body)
        assert "description" not in body["tools"][0]


class TestNamespaceTool:
    def test_nested_tools_flattened_with_dotted_names(self):
        body = {"tools": [{
            "type": "namespace", "name": "browser", "description": "browser tools",
            "tools": [
                {"type": "function", "name": "open",
                 "parameters": {"type": "object",
                                "properties": {"url": {"type": "string"}}}},
                {"type": "function", "name": "click",
                 "parameters": {"type": "object",
                                "properties": {"id": {"type": "integer"}}}},
            ],
        }]}
        assert downgrade_unsupported_tools(body) == ["browser.open", "browser.click"]
        assert _names(body) == ["browser.open", "browser.click"]
        assert all(t["type"] == "function" for t in body["tools"])
        # Nested parameter schemas must survive the flattening.
        assert body["tools"][0]["parameters"]["properties"]["url"]["type"] == "string"
        assert body["tools"][1]["parameters"]["properties"]["id"]["type"] == "integer"

    def test_nested_tool_inherits_namespace_description(self):
        body = {"tools": [{
            "type": "namespace", "name": "ns", "description": "group desc",
            "tools": [{"type": "function", "name": "a",
                       "parameters": {"type": "object"}}],
        }]}
        downgrade_unsupported_tools(body)
        assert body["tools"][0]["description"] == "group desc"

    def test_nested_own_description_wins(self):
        body = {"tools": [{
            "type": "namespace", "name": "ns", "description": "group",
            "tools": [{"type": "function", "name": "a", "description": "own",
                       "parameters": {"type": "object"}}],
        }]}
        downgrade_unsupported_tools(body)
        assert body["tools"][0]["description"] == "own"

    def test_nested_without_parameters_gets_input_schema(self):
        body = {"tools": [{
            "type": "namespace", "name": "ns",
            "tools": [{"type": "function", "name": "a"}],
        }]}
        downgrade_unsupported_tools(body)
        assert body["tools"][0]["parameters"]["required"] == ["input"]

    def test_empty_namespace_dropped(self):
        """Nothing callable inside; forwarding it would reject the whole array."""
        body = {"tools": [{"type": "namespace", "name": "ns", "tools": []},
                          {"type": "function", "name": "keep"}]}
        assert downgrade_unsupported_tools(body) == ["ns"]
        assert _names(body) == ["keep"]

    def test_malformed_nested_entries_skipped(self):
        body = {"tools": [{
            "type": "namespace", "name": "ns",
            "tools": ["junk", 42, None, {"no_name": True},
                      {"type": "function", "name": "ok"}],
        }]}
        assert downgrade_unsupported_tools(body) == ["ns.ok"]
        assert _names(body) == ["ns.ok"]


class TestOtherVariants:
    def test_unknown_variant_keeps_declared_parameters(self):
        """e.g. web_search / local_shell — preserve the client's schema."""
        body = {"tools": [{
            "type": "local_shell", "name": "shell",
            "parameters": {"type": "object",
                           "properties": {"cmd": {"type": "string"}}},
        }]}
        assert downgrade_unsupported_tools(body) == ["shell"]
        tool = body["tools"][0]
        assert tool["type"] == "function"
        assert tool["parameters"]["properties"]["cmd"]["type"] == "string"

    def test_unknown_variant_without_parameters_gets_input_schema(self):
        body = {"tools": [{"type": "web_search", "name": "search"}]}
        assert downgrade_unsupported_tools(body) == ["search"]
        assert body["tools"][0]["parameters"]["required"] == ["input"]


class TestPreservation:
    def test_function_and_mcp_untouched(self):
        original = [
            {"type": "function", "name": "f", "parameters": {"type": "object"}},
            {"type": "mcp", "server_label": "s", "connector_id": "c"},
        ]
        body = {"tools": [dict(t) for t in original]}
        assert downgrade_unsupported_tools(body) == []
        assert body["tools"] == original

    def test_mixed_list_order_preserved(self):
        body = {"tools": [
            {"type": "function", "name": "first"},
            {"type": "custom", "name": "second"},
            {"type": "mcp", "server_label": "third"},
        ]}
        assert downgrade_unsupported_tools(body) == ["second"]
        assert [t["type"] for t in body["tools"]] == ["function", "function", "mcp"]
        assert body["tools"][0] == {"type": "function", "name": "first"}

    def test_variant_without_name_left_alone(self):
        """No callable name to preserve — let the upstream error surface."""
        body = {"tools": [{"type": "custom", "description": "no name"}]}
        assert downgrade_unsupported_tools(body) == []
        assert body["tools"][0]["type"] == "custom"

    def test_missing_or_malformed_tools_is_noop(self):
        for body in ({}, {"tools": None}, {"tools": "nope"}, {"tools": []},
                     {"tools": ["str", 42, None]}):
            assert downgrade_unsupported_tools(body) == []

    def test_body_untouched_when_nothing_rewritten(self):
        body = {"tools": [{"type": "function", "name": "f"}]}
        before = body["tools"]
        downgrade_unsupported_tools(body)
        assert body["tools"] is before
