"""Unit tests for OpenAI Responses API web search adapter helpers."""

import json
from typing import Any

import pytest

from app.api.openai_passthrough.web_search import (
    OpenAIResponsesWebSearchError,
    build_message_request,
    build_response_json,
    drop_web_search_tools,
    extract_web_search_options,
    is_responses_web_search_request,
    stream_response_events,
)
from app.schemas.anthropic import Message, MessageRequest, MessageResponse, Usage


def _message_text(req: MessageRequest, index: int) -> str:
    content = req.messages[index].model_dump()["content"]
    assert isinstance(content, list)
    first = content[0]
    assert isinstance(first, dict)
    text = first["text"]
    assert isinstance(text, str)
    return text


def _sse_payloads(out: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for line in out.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[len("data: ") :])
        assert isinstance(payload, dict)
        payloads.append(payload)
    return payloads


def _first_payload(
    payloads: list[dict[str, Any]],
    event_type: str,
) -> dict[str, Any]:
    for payload in payloads:
        if payload.get("type") == event_type:
            return payload
    raise AssertionError(f"Missing payload for {event_type}")


def test_is_responses_web_search_request_detects_current_and_preview_tools():
    assert is_responses_web_search_request({"tools": [{"type": "web_search"}]})
    assert is_responses_web_search_request({"tools": [{"type": "web_search_preview"}]})
    assert not is_responses_web_search_request(
        {"tools": [{"type": "function", "name": "x"}]}
    )
    assert not is_responses_web_search_request({"input": "hi"})


def test_drop_web_search_tools_keeps_the_other_tools():
    """Codex attaches web_search to every request; refusing it would disable
    apply_patch and every other tool along with it."""
    body = {
        "tools": [
            {"type": "web_search"},
            {"type": "function", "name": "apply_patch"},
            {"type": "web_search_preview"},
        ]
    }
    assert drop_web_search_tools(body) == 2
    assert body["tools"] == [{"type": "function", "name": "apply_patch"}]
    assert not is_responses_web_search_request(body)


def test_drop_web_search_tools_no_op_without_search_tools():
    body = {"tools": [{"type": "function", "name": "apply_patch"}]}
    assert drop_web_search_tools(body) == 0
    assert body["tools"] == [{"type": "function", "name": "apply_patch"}]
    assert drop_web_search_tools({"input": "hi"}) == 0


def test_extract_web_search_options_maps_filters_and_location():
    options = extract_web_search_options(
        {
            "tools": [
                {
                    "type": "web_search",
                    "filters": {
                        "allowed_domains": ["docs.python.org"],
                        "blocked_domains": ["example.com"],
                    },
                    "user_location": {
                        "type": "approximate",
                        "city": "Seattle",
                        "region": "WA",
                        "country": "US",
                        "timezone": "America/Los_Angeles",
                    },
                    "search_context_size": "medium",
                }
            ]
        }
    )

    assert options.allowed_domains == ["docs.python.org"]
    assert options.blocked_domains == ["example.com"]
    assert options.user_location is not None
    assert options.user_location.city == "Seattle"
    assert options.search_context_size == "medium"


def test_extract_web_search_options_rejects_external_web_access_false():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        extract_web_search_options(
            {"tools": [{"type": "web_search", "external_web_access": False}]}
        )

    assert exc.value.status_code == 400
    assert exc.value.error_type == "invalid_request_error"
    assert "external_web_access" in exc.value.message


def test_extract_web_search_options_rejects_return_token_budget():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        extract_web_search_options(
            {"tools": [{"type": "web_search", "return_token_budget": 1200}]}
        )

    assert exc.value.status_code == 400
    assert "return_token_budget" in exc.value.message


def test_extract_web_search_options_rejects_present_non_object_filters():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        extract_web_search_options({"tools": [{"type": "web_search", "filters": []}]})

    assert "filters" in exc.value.message


def test_extract_web_search_options_rejects_conflicting_multiple_tools():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        extract_web_search_options(
            {
                "tools": [
                    {"type": "web_search", "filters": {"allowed_domains": ["a.com"]}},
                    {"type": "web_search", "filters": {"allowed_domains": ["b.com"]}},
                ]
            }
        )

    assert exc.value.status_code == 400
    assert "Conflicting" in exc.value.message


def test_build_message_request_converts_string_input_and_instructions():
    req = build_message_request(
        {
            "model": "openai.gpt-oss-120b",
            "instructions": "Be concise.",
            "input": "What changed in Python 3.13?",
            "max_output_tokens": 777,
            "temperature": 0.2,
            "top_p": 0.9,
            "tools": [{"type": "web_search"}],
        }
    )

    assert isinstance(req, MessageRequest)
    assert req.model == "openai.gpt-oss-120b"
    assert req.max_tokens == 777
    assert req.system is not None
    assert req.messages[0].role == "user"
    assert _message_text(req, 0) == "What changed in Python 3.13?"
    assert req.temperature == 0.2
    assert req.top_p == 0.9
    assert req.tools == [{"type": "web_search_20250305", "name": "web_search"}]


def test_build_message_request_converts_responses_input_array_and_filters():
    req = build_message_request(
        {
            "model": "m",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Find current news"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "What topic?"}],
                },
                {
                    "role": "user",
                    "content": "AI infrastructure",
                },
            ],
            "tools": [
                {
                    "type": "web_search",
                    "filters": {"allowed_domains": ["example.com"]},
                }
            ],
        }
    )

    assert [m.role for m in req.messages] == ["user", "assistant", "user"]
    assert _message_text(req, 0) == "Find current news"
    assert _message_text(req, 1) == "What topic?"
    assert _message_text(req, 2) == "AI infrastructure"
    assert req.tools == [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "allowed_domains": ["example.com"],
        }
    ]


def test_build_message_request_prepends_previous_response_context():
    first_req = build_message_request(
        {
            "model": "m",
            "input": "Search for a current positive technology story.",
            "tools": [{"type": "web_search"}],
        }
    )
    previous_messages = [
        *first_req.messages,
        Message(role="assistant", content="The previous story was about Example Robotics."),
    ]

    follow_up = build_message_request(
        {
            "model": "m",
            "previous_response_id": "resp_stateful_1",
            "input": "Using the previous response, search for one update about that organization.",
            "tools": [{"type": "web_search"}],
        },
        previous_messages=previous_messages,
    )

    assert [message.role for message in follow_up.messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert _message_text(follow_up, 0) == "Search for a current positive technology story."
    assert _message_text(follow_up, 1) == "The previous story was about Example Robotics."
    assert _message_text(follow_up, 2).startswith("Using the previous response")


def test_build_message_request_rejects_unknown_previous_response_id():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        build_message_request(
            {
                "model": "m",
                "previous_response_id": "resp_missing",
                "input": "Use previous context.",
                "tools": [{"type": "web_search"}],
            }
        )

    assert exc.value.status_code == 404
    assert "previous_response_id" in exc.value.message


def test_build_message_request_rejects_missing_input():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        build_message_request({"model": "m", "tools": [{"type": "web_search"}]})

    assert exc.value.status_code == 400
    assert "input" in exc.value.message


def test_build_message_request_maps_web_search_tool_choice_dict():
    req = build_message_request(
        {
            "model": "m",
            "input": "Find news",
            "tools": [{"type": "web_search"}],
            "tool_choice": {"type": "web_search"},
        }
    )

    assert req.tool_choice == {"type": "tool", "name": "web_search"}


def test_build_message_request_maps_function_tool_choice_dict():
    req = build_message_request(
        {
            "model": "m",
            "input": "Find news",
            "tools": [{"type": "web_search"}],
            "tool_choice": {"function": {"name": "web_search"}},
        }
    )

    assert req.tool_choice == {"type": "tool", "name": "web_search"}


def test_build_message_request_rejects_zero_max_output_tokens():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        build_message_request(
            {
                "model": "m",
                "input": "Find news",
                "max_output_tokens": 0,
                "tools": [{"type": "web_search"}],
            }
        )

    assert "max_output_tokens" in exc.value.message


def test_build_message_request_rejects_invalid_max_output_tokens():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        build_message_request(
            {
                "model": "m",
                "input": "Find news",
                "max_output_tokens": "abc",
                "tools": [{"type": "web_search"}],
            }
        )

    assert "max_output_tokens" in exc.value.message


def test_build_message_request_rejects_invalid_user_location_field_type():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        build_message_request(
            {
                "model": "m",
                "input": "Find news",
                "tools": [
                    {
                        "type": "web_search",
                        "user_location": {"type": "approximate", "city": 123},
                    }
                ],
            }
        )

    assert "user_location" in exc.value.message


def test_build_message_request_wraps_schema_validation_errors():
    with pytest.raises(OpenAIResponsesWebSearchError) as exc:
        build_message_request(
            {
                "model": "m",
                "input": "Find news",
                "temperature": 2,
                "tools": [{"type": "web_search"}],
            }
        )

    assert exc.value.status_code == 400
    assert exc.value.error_type == "invalid_request_error"
    assert "temperature" in exc.value.message


def test_build_response_json_maps_text_annotations_and_usage():
    content: list[Any] = [
        {
            "type": "server_tool_use",
            "id": "srvtoolu_123",
            "name": "web_search",
            "input": {"query": "Python 3.13"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_123",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://docs.python.org/3/whatsnew/3.13.html",
                    "title": "What's New In Python 3.13",
                    "encrypted_content": "eA==",
                }
            ],
        },
        {
            "type": "text",
            "text": "Python 3.13 added a new interactive interpreter.",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "url": "https://docs.python.org/3/whatsnew/3.13.html",
                    "title": "What's New In Python 3.13",
                    "cited_text": "new interactive interpreter",
                }
            ],
        },
    ]
    msg = MessageResponse(
        id="msg-local",
        type="message",
        role="assistant",
        model="m",
        stop_reason="end_turn",
        content=content,
        usage=Usage(
            input_tokens=10,
            output_tokens=5,
            server_tool_use={"web_search_requests": 1},
        ),
    )

    data = build_response_json(msg, original_model="m")

    assert data["object"] == "response"
    assert data["status"] == "completed"
    assert data["model"] == "m"
    assert data["output"][0]["type"] == "web_search_call"
    assert data["output"][0]["status"] == "completed"
    message = data["output"][1]
    assert message["type"] == "message"
    assert message["content"][0]["type"] == "output_text"
    assert data["output_text"] == "Python 3.13 added a new interactive interpreter."
    ann = message["content"][0]["annotations"][0]
    assert ann["type"] == "url_citation"
    assert ann["url"] == "https://docs.python.org/3/whatsnew/3.13.html"
    assert ann["start_index"] == 0
    assert ann["end_index"] == len(data["output_text"])
    assert data["usage"] == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert data["metadata"]["web_search_requests"] == 1


@pytest.mark.asyncio
async def test_stream_response_events_emits_responses_sse():
    content: list[Any] = [{"type": "text", "text": "hello"}]
    msg = MessageResponse(
        id="msg-local",
        type="message",
        role="assistant",
        model="m",
        stop_reason="end_turn",
        content=content,
        usage=Usage(
            input_tokens=2,
            output_tokens=1,
            server_tool_use={"web_search_requests": 1},
        ),
    )

    chunks = [chunk async for chunk in stream_response_events(msg, original_model="m")]
    out = b"".join(chunks).decode("utf-8")

    assert "event: response.created\n" in out
    assert "event: response.output_item.added\n" in out
    assert "event: response.output_text.delta\n" in out
    assert "event: response.output_text.done\n" in out
    assert "event: response.completed\n" in out

    payloads = _sse_payloads(out)
    assert [payload["sequence_number"] for payload in payloads] == list(
        range(len(payloads))
    )

    message_added = next(
        payload
        for payload in payloads
        if payload.get("type") == "response.output_item.added"
        and payload["item"]["type"] == "message"
    )
    assert message_added["item"]["status"] == "in_progress"
    assert message_added["item"]["content"] == []

    content_part_added = _first_payload(payloads, "response.content_part.added")
    assert content_part_added["part"] == {
        "type": "output_text",
        "text": "",
        "annotations": [],
    }

    output_text_delta = _first_payload(payloads, "response.output_text.delta")
    assert output_text_delta["delta"] == "hello"

    output_text_done = _first_payload(payloads, "response.output_text.done")
    assert output_text_done["text"] == "hello"

    content_part_done = _first_payload(payloads, "response.content_part.done")
    assert content_part_done["part"]["text"] == "hello"

    message_done = next(
        payload
        for payload in payloads
        if payload.get("type") == "response.output_item.done"
        and payload["item"]["type"] == "message"
    )
    assert message_done["item"]["status"] == "completed"
    assert message_done["item"]["content"][0]["text"] == "hello"

    completed = _first_payload(payloads, "response.completed")
    assert completed["response"]["usage"]["input_tokens"] == 2
    assert completed["response"]["output"][-1]["content"][0]["text"] == "hello"
    assert [item["type"] for item in completed["response"]["output"]] == [
        "web_search_call",
        "message",
    ]


def test_build_response_json_emits_one_web_search_call_per_request():
    content: list[Any] = [{"type": "text", "text": "done"}]
    msg = MessageResponse(
        id="msg-local",
        type="message",
        role="assistant",
        model="m",
        stop_reason="end_turn",
        content=content,
        usage=Usage(
            input_tokens=10,
            output_tokens=5,
            server_tool_use={"web_search_requests": 2},
        ),
    )

    data = build_response_json(msg, original_model="m")

    assert [item["type"] for item in data["output"]] == [
        "web_search_call",
        "web_search_call",
        "message",
    ]
    assert data["metadata"]["web_search_requests"] == 2


def test_build_response_json_uses_full_text_block_annotation_offsets():
    content: list[Any] = [
        {
            "type": "text",
            "text": "first",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "url": "https://example.com/first",
                    "title": "First",
                    "cited_text": "first",
                }
            ],
        },
        {
            "type": "text",
            "text": "second",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "url": "https://example.com/second",
                    "title": "Second",
                    "cited_text": "second",
                }
            ],
        },
    ]
    msg = MessageResponse(
        id="msg-local",
        type="message",
        role="assistant",
        model="m",
        stop_reason="end_turn",
        content=content,
        usage=Usage(input_tokens=10, output_tokens=5),
    )

    data = build_response_json(msg, original_model="m")

    message = data["output"][0]
    annotations = message["content"][0]["annotations"]
    assert data["output_text"] == "first\nsecond"
    assert annotations[0]["start_index"] == 0
    assert annotations[0]["end_index"] == 5
    assert annotations[1]["start_index"] == 6
    assert annotations[1]["end_index"] == 12


def test_build_response_json_ignores_malformed_web_search_count():
    content: list[Any] = [{"type": "text", "text": "done"}]
    msg = MessageResponse(
        id="msg-local",
        type="message",
        role="assistant",
        model="m",
        stop_reason="end_turn",
        content=content,
        usage=Usage(
            input_tokens=10,
            output_tokens=5,
            server_tool_use={"web_search_requests": "bad"},
        ),
    )

    data = build_response_json(msg, original_model="m")

    assert [item["type"] for item in data["output"]] == ["message"]
    assert "metadata" not in data


@pytest.mark.parametrize("web_search_requests", [True, 1.5, float("inf")])
def test_build_response_json_ignores_non_integer_web_search_count(
    web_search_requests: Any,
):
    content: list[Any] = [{"type": "text", "text": "done"}]
    msg = MessageResponse(
        id="msg-local",
        type="message",
        role="assistant",
        model="m",
        stop_reason="end_turn",
        content=content,
        usage=Usage(
            input_tokens=10,
            output_tokens=5,
            server_tool_use={"web_search_requests": web_search_requests},
        ),
    )

    data = build_response_json(msg, original_model="m")

    assert [item["type"] for item in data["output"]] == ["message"]
    assert "metadata" not in data
