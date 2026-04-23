"""Unit tests for OpenAICompatService._stream_worker SSE event generation.

These tests verify the streaming worker emits a well-formed Anthropic-format
SSE event sequence for non-Claude models accessed via Bedrock Mantle's
OpenAI-compatible endpoint. They focus on three regression scenarios:

1. thinking → tool_use transition must close the thinking block before the
   tool_use block opens (previously left thinking unclosed).
2. thinking blocks must emit a signature_delta before content_block_stop so
   Anthropic SDK clients see the same event shape as the native Claude path.
3. Streams that end without a finish_reason chunk must still emit
   message_delta + message_stop so clients don't hang.
"""

import json
import queue
from collections.abc import Iterable
from typing import Any
from unittest.mock import MagicMock, patch

from app.schemas.anthropic import Message, MessageRequest
from app.services.openai_compat_service import OpenAICompatService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeChunk:
    """Mimics the openai ChatCompletionChunk object with .model_dump()."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def model_dump(self) -> dict[str, Any]:
        return self._data


def _make_service() -> OpenAICompatService:
    """Build an OpenAICompatService with __init__ bypassed."""
    with patch.object(OpenAICompatService, "__init__", lambda self: None):
        svc = OpenAICompatService.__new__(OpenAICompatService)
        svc.client = MagicMock(name="openai_client")
        # Stub the request converter so convert_request returns a minimal dict
        svc.request_converter = MagicMock()
        svc.request_converter.convert_request.return_value = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1024,
        }
        # Use the real response converter so we exercise create_message_start_event
        from app.converters.openai_to_anthropic import OpenAIToAnthropicConverter

        svc.response_converter = OpenAIToAnthropicConverter()
        return svc


def _request() -> MessageRequest:
    return MessageRequest(
        model="test-model",
        messages=[Message(role="user", content="hi")],
        max_tokens=1024,
    )


def _drain_events(q: queue.Queue) -> list[dict[str, Any]]:
    """Pull ('event', sse_str) entries off the queue and parse the JSON data."""
    parsed: list[dict[str, Any]] = []
    while True:
        try:
            msg_type, data = q.get_nowait()
        except queue.Empty:
            break
        if msg_type == "event":
            # sse format: "event: <type>\ndata: <json>\n\n"
            for line in data.split("\n"):
                if line.startswith("data:"):
                    parsed.append(json.loads(line[len("data:") :].strip()))
                    break
        elif msg_type == "done":
            parsed.append({"__sentinel__": "done"})
        elif msg_type == "error":
            parsed.append({"__sentinel__": "error", "data": data})
    return parsed


def _run_worker(chunks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run _stream_worker with the given fake chunk sequence."""
    svc = _make_service()
    svc.client.chat.completions.create.return_value = iter(
        [_FakeChunk(c) for c in chunks]
    )
    q: queue.Queue = queue.Queue()
    svc._stream_worker(_request(), "msg_test", q)
    return _drain_events(q)


def _types(events: list[dict[str, Any]]) -> list[str]:
    """Return a flat list of (event_type | delta_type) for sequence assertions."""
    out: list[str] = []
    for e in events:
        if "__sentinel__" in e:
            out.append(f"__{e['__sentinel__']}__")
            continue
        t = e.get("type")
        if t == "content_block_delta":
            out.append(f"delta:{e['delta']['type']}")
        elif t == "content_block_start":
            out.append(f"start:{e['content_block']['type']}")
        else:
            out.append(t)
    return out


def _usage_chunk(prompt: int = 10, completion: int = 20) -> dict[str, Any]:
    return {
        "choices": [],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


def _chunk(
    *,
    reasoning: str | None = None,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if reasoning is not None:
        delta["reasoning"] = reasoning
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {"choices": [{"delta": delta, "finish_reason": finish_reason}]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_text_only_stream_emits_wellformed_sequence():
    """Simple text response: start → text block → stop."""
    events = _run_worker(
        [
            _chunk(content="Hello"),
            _chunk(content=" world"),
            _chunk(finish_reason="stop"),
            _usage_chunk(prompt=5, completion=2),
        ]
    )

    assert _types(events) == [
        "message_start",
        "start:text",
        "delta:text_delta",
        "delta:text_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]
    # message_delta carries final usage and stop_reason
    msg_delta = next(e for e in events if e.get("type") == "message_delta")
    assert msg_delta["delta"]["stop_reason"] == "end_turn"
    assert msg_delta["usage"]["input_tokens"] == 5
    assert msg_delta["usage"]["output_tokens"] == 2


def test_thinking_to_tool_use_closes_thinking_block():
    """Regression: reasoning followed directly by tool_call must close the
    thinking block (emit signature_delta + content_block_stop) before opening
    the tool_use block — previously the two blocks collided on the same index.
    """
    events = _run_worker(
        [
            _chunk(reasoning="Think A"),
            _chunk(reasoning="Think B"),
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_abc",
                        "function": {"name": "do_thing", "arguments": ""},
                    }
                ]
            ),
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "function": {"arguments": '{"x":1}'},
                    }
                ]
            ),
            _chunk(finish_reason="tool_calls"),
            _usage_chunk(),
        ]
    )

    assert _types(events) == [
        "message_start",
        "start:thinking",
        "delta:thinking_delta",
        "delta:thinking_delta",
        "delta:signature_delta",
        "content_block_stop",
        "start:tool_use",
        "delta:input_json_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]

    # Verify the indices are distinct (thinking=0, tool_use=1)
    block_events = [
        e
        for e in events
        if e.get("type") in ("content_block_start", "content_block_stop")
    ]
    assert [e["index"] for e in block_events] == [0, 0, 1, 1]
    assert block_events[0]["content_block"]["type"] == "thinking"
    assert block_events[2]["content_block"]["type"] == "tool_use"


def test_thinking_block_emits_signature_delta_before_stop():
    """Thinking-only response: signature_delta must appear before content_block_stop."""
    events = _run_worker(
        [
            _chunk(reasoning="A"),
            _chunk(reasoning="B"),
            _chunk(finish_reason="length"),
            _usage_chunk(prompt=1, completion=32000),
        ]
    )

    assert _types(events) == [
        "message_start",
        "start:thinking",
        "delta:thinking_delta",
        "delta:thinking_delta",
        "delta:signature_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]
    sig = next(
        e
        for e in events
        if e.get("type") == "content_block_delta"
        and e["delta"]["type"] == "signature_delta"
    )
    # Non-Claude models don't produce a real signature — empty string is a placeholder
    assert sig["delta"]["signature"] == ""
    assert sig["index"] == 0
    # stop_reason maps length → max_tokens
    msg_delta = next(e for e in events if e.get("type") == "message_delta")
    assert msg_delta["delta"]["stop_reason"] == "max_tokens"


def test_thinking_then_text_closes_thinking_with_signature():
    """Thinking followed by text should close thinking (with signature_delta) first."""
    events = _run_worker(
        [
            _chunk(reasoning="reasoning"),
            _chunk(content="answer"),
            _chunk(finish_reason="stop"),
            _usage_chunk(),
        ]
    )

    assert _types(events) == [
        "message_start",
        "start:thinking",
        "delta:thinking_delta",
        "delta:signature_delta",
        "content_block_stop",
        "start:text",
        "delta:text_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]


def test_no_finish_reason_still_emits_terminator():
    """If upstream ends without finish_reason, fallback terminator must be emitted.

    Otherwise Anthropic SDK clients sit waiting forever for message_stop.
    """
    events = _run_worker(
        [
            _chunk(content="partial"),
            # no finish_reason chunk — stream just ends
        ]
    )

    types = _types(events)
    # Must end with fallback message_delta + message_stop + done
    assert types[-4:] == [
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]
    # Text block was opened, so it must have been closed by the fallback path
    assert "start:text" in types
    msg_delta = next(e for e in events if e.get("type") == "message_delta")
    # Default stop_reason when upstream provided none
    assert msg_delta["delta"]["stop_reason"] == "end_turn"


def test_no_finish_reason_closes_open_thinking():
    """Thinking-only stream that ends without finish_reason must still close the
    thinking block (with signature_delta) and emit the terminator."""
    events = _run_worker(
        [
            _chunk(reasoning="hmm"),
        ]
    )

    assert _types(events) == [
        "message_start",
        "start:thinking",
        "delta:thinking_delta",
        "delta:signature_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]


def test_tool_use_arguments_streamed_as_input_json_delta():
    """Tool calls with arguments spread across chunks should stream as
    input_json_delta events at the same content_block index."""
    events = _run_worker(
        [
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "f", "arguments": ""},
                    }
                ]
            ),
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "function": {"arguments": '{"a":'},
                    }
                ]
            ),
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "function": {"arguments": "1}"},
                    }
                ]
            ),
            _chunk(finish_reason="tool_calls"),
            _usage_chunk(),
        ]
    )

    assert _types(events) == [
        "message_start",
        "start:tool_use",
        "delta:input_json_delta",
        "delta:input_json_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]

    # Reassembling the partial_json yields the tool input
    partials = [
        e["delta"]["partial_json"]
        for e in events
        if e.get("type") == "content_block_delta"
        and e["delta"]["type"] == "input_json_delta"
    ]
    assert "".join(partials) == '{"a":1}'

    # tool_use block has id/name set from the first chunk
    tool_start = next(
        e
        for e in events
        if e.get("type") == "content_block_start"
        and e["content_block"]["type"] == "tool_use"
    )
    assert tool_start["content_block"]["id"] == "call_1"
    assert tool_start["content_block"]["name"] == "f"


def test_two_tool_calls_in_a_row_close_cleanly():
    """Two sequential tool_calls: the first tool_use block must be closed
    (content_block_stop at its index) before the second opens at index+1."""
    events = _run_worker(
        [
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "a", "arguments": "{}"},
                    }
                ]
            ),
            _chunk(
                tool_calls=[
                    {
                        "index": 1,
                        "id": "call_2",
                        "function": {"name": "b", "arguments": "{}"},
                    }
                ]
            ),
            _chunk(finish_reason="tool_calls"),
            _usage_chunk(),
        ]
    )

    assert _types(events) == [
        "message_start",
        "start:tool_use",
        "delta:input_json_delta",
        "content_block_stop",
        "start:tool_use",
        "delta:input_json_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]

    block_events = [
        e
        for e in events
        if e.get("type") in ("content_block_start", "content_block_stop")
    ]
    # start(0) stop(0) start(1) stop(1) — distinct indices per tool
    assert [e["index"] for e in block_events] == [0, 0, 1, 1]
    tool_starts = [e for e in block_events if e.get("type") == "content_block_start"]
    assert [s["content_block"]["id"] for s in tool_starts] == ["call_1", "call_2"]


def test_reasoning_after_text_closes_text_block():
    """Interleaved-thinking: if a model resumes reasoning AFTER emitting text,
    the open text block must be closed with its own content_block_stop before
    a new thinking block opens (not collide on the same index)."""
    events = _run_worker(
        [
            _chunk(reasoning="first thought"),
            _chunk(content="hi"),
            _chunk(reasoning="more thinking"),  # resume reasoning
            _chunk(content=" again"),
            _chunk(finish_reason="stop"),
            _usage_chunk(),
        ]
    )

    assert _types(events) == [
        "message_start",
        "start:thinking",
        "delta:thinking_delta",
        "delta:signature_delta",
        "content_block_stop",
        "start:text",
        "delta:text_delta",
        "content_block_stop",
        "start:thinking",
        "delta:thinking_delta",
        "delta:signature_delta",
        "content_block_stop",
        "start:text",
        "delta:text_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]

    block_events = [
        e
        for e in events
        if e.get("type") in ("content_block_start", "content_block_stop")
    ]
    # Four distinct blocks at indices 0,1,2,3, each with matched start/stop
    assert [e["index"] for e in block_events] == [0, 0, 1, 1, 2, 2, 3, 3]


def test_reasoning_after_tool_use_closes_tool_block():
    """Interleaved-thinking: resuming reasoning after a tool_use must close
    the tool_use block first (not leave it dangling)."""
    events = _run_worker(
        [
            _chunk(reasoning="thinking"),
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ]
            ),
            _chunk(reasoning="more thinking"),  # resume reasoning after tool
            _chunk(finish_reason="stop"),
            _usage_chunk(),
        ]
    )

    types = _types(events)
    # Tool block must be closed before second thinking block opens
    assert types == [
        "message_start",
        "start:thinking",
        "delta:thinking_delta",
        "delta:signature_delta",
        "content_block_stop",
        "start:tool_use",
        "delta:input_json_delta",
        "content_block_stop",
        "start:thinking",
        "delta:thinking_delta",
        "delta:signature_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
        "__done__",
    ]


def test_openai_error_mid_stream_yields_error_sentinel_and_no_fallback():
    """If the OpenAI client raises mid-stream, the worker must enqueue
    ('error', ...) and NOT emit a fallback message_stop afterward — the
    async consumer yields a single error event and breaks."""

    def _chunk_iterator():
        yield _FakeChunk(_chunk(reasoning="part"))
        # Simulate an upstream OpenAI error after the thinking block has opened
        from openai import APIError

        raise APIError(
            message="upstream boom",
            request=MagicMock(),
            body=None,
        )

    svc = _make_service()
    svc.client.chat.completions.create.return_value = _chunk_iterator()
    q: queue.Queue = queue.Queue()
    svc._stream_worker(_request(), "msg_test", q)
    events = _drain_events(q)

    # Should have message_start, thinking start, thinking_delta, then error sentinel.
    # No content_block_stop, no signature_delta, no message_delta/message_stop.
    assert _types(events)[:3] == [
        "message_start",
        "start:thinking",
        "delta:thinking_delta",
    ]
    # Last queued item should be the error sentinel (no "done", no terminator)
    assert events[-1]["__sentinel__"] == "error"
    terminator_types = {
        "message_delta",
        "message_stop",
        "content_block_stop",
        "delta:signature_delta",
    }
    assert not any(t in _types(events) for t in terminator_types)
