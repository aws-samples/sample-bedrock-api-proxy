"""Tests for reshaping message content and tool_choice for bedrock-mantle.

Both constraints were found by probing the upstream directly:
  * assistant messages must be a plain string, not a content-part array
    (an array yields "SubmitRequestFailure ... 219 validation errors")
  * only `input_text` parts are accepted on user/system/developer messages
  * tool_choice supports only "auto"
"""
import pytest

from app.api.openai_passthrough.chat_responses_adapter import (
    clamp_tool_choice,
    normalize_message_content,
)


class TestAssistantContent:
    def test_content_array_flattened_to_string(self):
        """This is what a client replays from the previous turn."""
        body = {"input": [
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "prior reply"}]},
            {"type": "message", "role": "user", "content": "ok"},
        ]}
        assert normalize_message_content(body) == ["assistant content[]->str"]
        assert body["input"][0]["content"] == "prior reply"
        # The user turn is already a string and must be untouched.
        assert body["input"][1]["content"] == "ok"

    def test_multiple_parts_concatenated(self):
        body = {"input": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "a"},
            {"type": "output_text", "text": "b"},
        ]}]}
        normalize_message_content(body)
        assert "a" in body["input"][0]["content"]
        assert "b" in body["input"][0]["content"]

    def test_string_content_untouched(self):
        body = {"input": [{"type": "message", "role": "assistant",
                           "content": "already a string"}]}
        assert normalize_message_content(body) == []
        assert body["input"][0]["content"] == "already a string"


class TestUserContentParts:
    def test_chat_style_text_renamed(self):
        """`text` is the Chat Completions spelling; Responses wants input_text."""
        body = {"input": [{"type": "message", "role": "user",
                           "content": [{"type": "text", "text": "hi"}]}]}
        assert normalize_message_content(body) == ["text->input_text"]
        assert body["input"][0]["content"] == [{"type": "input_text", "text": "hi"}]

    def test_input_text_untouched(self):
        body = {"input": [{"type": "message", "role": "user",
                           "content": [{"type": "input_text", "text": "hi"}]}]}
        assert normalize_message_content(body) == []

    @pytest.mark.parametrize("part,expect_in_text", [
        ({"type": "input_image", "image_url": "https://x/a.png"}, "image"),
        ({"type": "input_file", "filename": "notes.txt"}, "notes.txt"),
        ({"type": "input_audio", "input_audio": {"data": "x"}}, "audio"),
    ])
    def test_unrepresentable_parts_become_placeholders(self, part, expect_in_text):
        """Replaced, not dropped — the model should know an attachment existed."""
        body = {"input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "look"}, part,
        ]}]}
        notes = normalize_message_content(body)
        assert len(notes) == 1
        parts = body["input"][0]["content"]
        assert all(p["type"] == "input_text" for p in parts)
        assert parts[0]["text"] == "look"
        assert expect_in_text in parts[1]["text"]

    def test_refusal_text_preserved(self):
        body = {"input": [{"type": "message", "role": "user",
                           "content": [{"type": "refusal",
                                        "refusal": "I cannot help"}]}]}
        normalize_message_content(body)
        assert body["input"][0]["content"][0]["text"] == "I cannot help"

    @pytest.mark.parametrize("role", ["user", "system", "developer"])
    def test_applies_to_all_prompt_roles(self, role):
        body = {"input": [{"type": "message", "role": role,
                           "content": [{"type": "text", "text": "x"}]}]}
        assert normalize_message_content(body) == ["text->input_text"]

    def test_malformed_input_is_noop(self):
        for body in ({}, {"input": None}, {"input": "plain string"},
                     {"input": []}, {"input": ["junk", 42]}):
            assert normalize_message_content(body) == []

    def test_non_message_items_skipped(self):
        body = {"input": [{"type": "function_call", "call_id": "c",
                           "name": "f", "arguments": "{}"}]}
        assert normalize_message_content(body) == []


class TestToolChoice:
    def test_auto_untouched(self):
        body = {"tool_choice": "auto"}
        assert clamp_tool_choice(body) is None
        assert body["tool_choice"] == "auto"

    @pytest.mark.parametrize("sent", ["required", "any"])
    def test_forcing_values_relaxed_to_auto(self, sent):
        """"Use a tool" is still satisfiable under auto — don't fail the request."""
        body = {"tool_choice": sent, "tools": [{"type": "function", "name": "f"}]}
        assert clamp_tool_choice(body) == f"tool_choice {sent}->auto"
        assert body["tool_choice"] == "auto"
        assert body["tools"]  # tools retained

    def test_named_function_relaxed_to_auto(self):
        body = {"tool_choice": {"type": "function", "name": "myfunc"},
                "tools": [{"type": "function", "name": "myfunc"}]}
        assert clamp_tool_choice(body) == "tool_choice myfunc->auto"
        assert body["tool_choice"] == "auto"

    def test_none_withholds_tools(self):
        """`none` inverts intent, so suppress calls by removing the tools."""
        body = {"tool_choice": "none",
                "tools": [{"type": "function", "name": "f"}]}
        assert clamp_tool_choice(body) == "tool_choice none->auto (tools withheld)"
        assert body["tool_choice"] == "auto"
        assert body["tools"] == []

    def test_none_without_tools(self):
        body = {"tool_choice": "none"}
        assert clamp_tool_choice(body) == "tool_choice none->auto"
        assert body["tool_choice"] == "auto"

    def test_absent_tool_choice_is_noop(self):
        body = {"input": "hi"}
        assert clamp_tool_choice(body) is None
        assert "tool_choice" not in body
