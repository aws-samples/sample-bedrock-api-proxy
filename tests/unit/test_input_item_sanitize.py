"""Tests for dropping/rewriting Responses input items mantle cannot accept.

One unsupported item rejects the ENTIRE input array, and the client keeps
replaying its history — so a single bad item wedges the conversation
permanently. Hence sanitizing rather than surfacing the error.
"""
import json

from app.api.openai_passthrough.chat_responses_adapter import sanitize_input_items


def _types(body):
    return [i.get("type") for i in body["input"]]


class TestDropUnsupported:
    def test_web_search_call_dropped(self):
        body = {"input": [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "web_search_call", "id": "w1", "status": "completed"},
        ]}
        assert sanitize_input_items(body) == ["dropped web_search_call"]
        assert _types(body) == ["message"]

    def test_all_known_unsupported_types_dropped(self):
        unsupported = ["web_search_call", "local_shell_call",
                       "local_shell_call_output", "computer_call",
                       "computer_call_output", "file_search_call",
                       "image_generation_call", "code_interpreter_call"]
        body = {"input": [{"type": t} for t in unsupported]}
        notes = sanitize_input_items(body)
        assert len(notes) == len(unsupported)
        assert body["input"] == []

    def test_unknown_future_type_dropped(self):
        body = {"input": [{"type": "some_new_thing_2027"}]}
        assert sanitize_input_items(body) == ["dropped some_new_thing_2027"]
        assert body["input"] == []


class TestSupportedPreserved:
    def test_supported_types_untouched(self):
        original = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "function_call", "call_id": "c", "name": "f",
             "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c", "output": "o"},
            {"type": "mcp_call", "id": "m", "name": "t", "server_label": "s"},
            {"type": "item_reference", "id": "msg_1"},
        ]
        body = {"input": [dict(i) for i in original]}
        assert sanitize_input_items(body) == []
        assert body["input"] == original

    def test_typeless_message_preserved(self):
        """A bare {"role","content"} message is valid upstream."""
        body = {"input": [{"role": "user", "content": "hi"}]}
        assert sanitize_input_items(body) == []
        assert body["input"] == [{"role": "user", "content": "hi"}]

    def test_body_untouched_when_nothing_changed(self):
        body = {"input": [{"type": "message", "role": "user", "content": "x"}]}
        before = body["input"]
        sanitize_input_items(body)
        assert body["input"] is before

    def test_missing_or_malformed_input_is_noop(self):
        for body in ({}, {"input": None}, {"input": "text prompt"}, {"input": []}):
            assert sanitize_input_items(body) == []

    def test_plain_string_input_untouched(self):
        """Codex may send a bare string; it is not an item array."""
        body = {"input": "say ok"}
        assert sanitize_input_items(body) == []
        assert body["input"] == "say ok"


class TestCustomToolCallRewrite:
    def test_custom_tool_call_becomes_function_call(self):
        body = {"input": [{
            "type": "custom_tool_call", "call_id": "c1",
            "name": "apply_patch", "input": "*** Begin Patch",
        }]}
        assert sanitize_input_items(body) == ["custom_tool_call->function_call"]
        item = body["input"][0]
        assert item["type"] == "function_call"
        assert item["call_id"] == "c1"
        assert item["name"] == "apply_patch"
        # Free-form text must become JSON arguments matching the function schema.
        assert json.loads(item["arguments"]) == {"input": "*** Begin Patch"}
        assert "input" not in item

    def test_custom_tool_call_output_renamed(self):
        body = {"input": [{
            "type": "custom_tool_call_output", "call_id": "c1", "output": "done",
        }]}
        assert sanitize_input_items(body) == [
            "custom_tool_call_output->function_call_output"
        ]
        item = body["input"][0]
        assert item["type"] == "function_call_output"
        assert item["output"] == "done"
        assert item["call_id"] == "c1"

    def test_non_string_input_becomes_empty_string(self):
        body = {"input": [{"type": "custom_tool_call", "call_id": "c",
                           "name": "n", "input": {"unexpected": "shape"}}]}
        sanitize_input_items(body)
        assert json.loads(body["input"][0]["arguments"]) == {"input": ""}

    def test_existing_arguments_not_overwritten(self):
        body = {"input": [{"type": "custom_tool_call", "call_id": "c",
                           "name": "n", "arguments": '{"a":1}', "input": "raw"}]}
        sanitize_input_items(body)
        assert json.loads(body["input"][0]["arguments"]) == {"a": 1}

    def test_call_pairing_preserved_across_rewrite(self):
        """call_id must survive so the output still matches its call."""
        body = {"input": [
            {"type": "custom_tool_call", "call_id": "x9", "name": "n",
             "input": "p"},
            {"type": "custom_tool_call_output", "call_id": "x9", "output": "o"},
        ]}
        sanitize_input_items(body)
        assert _types(body) == ["function_call", "function_call_output"]
        assert {i["call_id"] for i in body["input"]} == {"x9"}


class TestOrdering:
    def test_order_preserved_with_mixed_items(self):
        body = {"input": [
            {"type": "message", "role": "user", "content": "1"},
            {"type": "web_search_call", "id": "w"},
            {"type": "custom_tool_call", "call_id": "c", "name": "n",
             "input": "p"},
            {"type": "message", "role": "user", "content": "2"},
        ]}
        notes = sanitize_input_items(body)
        assert notes == ["dropped web_search_call",
                         "custom_tool_call->function_call"]
        assert _types(body) == ["message", "function_call", "message"]
        assert body["input"][0]["content"] == "1"
        assert body["input"][2]["content"] == "2"

    def test_non_dict_entries_passed_through(self):
        body = {"input": ["str", 42, None,
                          {"type": "web_search_call"}]}
        sanitize_input_items(body)
        assert body["input"] == ["str", 42, None]
