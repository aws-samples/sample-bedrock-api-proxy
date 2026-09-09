"""Exercise live Responses SSE parsing through the real SDK's HTTP transport."""

import asyncio
import json
import threading
from contextlib import contextmanager

import httpx
import pytest

from app.converters.openai_responses_to_anthropic import (
    OpenAIResponsesToAnthropicConverter,
)
from app.schemas.anthropic import MessageRequest
from app.services.openai_compat_service import OpenAICompatService


def event(kind, **fields):
    return {"type": kind, "sequence_number": 0, **fields}


def created():
    return event(
        "response.created",
        response={"id": "resp_test", "status": "in_progress", "output": []},
    )


def terminal(status="completed", **fields):
    return event(
        f"response.{status}",
        response={"id": "resp_test", "status": status, "output": [], **fields},
    )


def text_delta(text="Hello", output_index=0, content_index=0):
    return event(
        "response.output_text.delta",
        item_id=f"msg_{output_index}",
        output_index=output_index,
        content_index=content_index,
        delta=text,
        logprobs=[],
    )


def tool_item(index, call_id, arguments=""):
    return event(
        "response.output_item.added",
        output_index=index,
        item={
            "type": "function_call",
            "id": f"fc_{index}",
            "call_id": call_id,
            "name": "lookup",
            "arguments": arguments,
            "status": "in_progress",
        },
    )


class WireStream(httpx.SyncByteStream):
    """Can hold generation open after its first event to detect buffering."""

    def __init__(self, events, *, pause_after=None):
        self.events = events
        self.pause_after = pause_after
        self.release = threading.Event()
        self.closed = threading.Event()
        self.produced = 0

    def __iter__(self):
        for value in self.events:
            if self.pause_after == self.produced:
                if not self.release.wait(5):
                    raise RuntimeError("Test generation was never released")
                if self.closed.is_set():
                    return
            self.produced += 1
            yield (f"event: {value['type']}\ndata: {json.dumps(value)}\n\n").encode()

    def close(self):
        self.closed.set()
        self.release.set()


@contextmanager
def service_for(wire, *, status=200):
    requests = []

    def handle(request):
        assert request.url.path == "/openai/v1/responses"
        requests.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "Rejected"}})
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=wire
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        service = OpenAICompatService(
            base_url="https://runtime.test/openai/v1",
            api_key="test-key",
            http_client=client,
        )
        service.client.max_retries = 0
        yield service, requests


def request():
    # Stream method must override even a non-streaming request.
    return MessageRequest(
        model="global.openai.test", messages=[{"role": "user", "content": "Hi"}]
    )


def decode(frame):
    name, data = frame.strip().split("\n", 1)
    value = json.loads(data.removeprefix("data: "))
    assert name == f"event: {value['type']}"
    return value


async def collect(service):
    return [decode(frame) async for frame in service.invoke_responses_stream(request())]


def assert_lifecycle(events):
    assert events[0]["type"] == "message_start"
    assert events[-1]["type"] == "message_stop"
    assert sum(e["type"] == "message_stop" for e in events) == 1
    started, stopped = set(), set()
    for e in events:
        if e["type"] == "content_block_start":
            assert e["index"] not in started
            started.add(e["index"])
        elif e["type"] == "content_block_delta":
            assert e["index"] in started - stopped
        elif e["type"] == "content_block_stop":
            assert e["index"] in started - stopped
            stopped.add(e["index"])
    assert started == stopped


async def test_stream_delivers_text_before_generation_completes():
    wire = WireStream([created(), text_delta(), terminal()], pause_after=2)
    with service_for(wire) as (service, requests):
        stream = service.invoke_responses_stream(request())
        try:
            first = []
            while not any(e["type"] == "content_block_delta" for e in first):
                first.append(decode(await asyncio.wait_for(anext(stream), 1)))
            assert wire.produced == 2
            assert not wire.closed.is_set()
            assert first[-1]["delta"]["text"] == "Hello"
            wire.release.set()
            events = first + [decode(e) async for e in stream]
        finally:
            wire.release.set()
            await stream.aclose()
    assert requests[0]["stream"] is True
    assert requests[0]["store"] is False
    assert events[0]["message"]["model"] == request().model
    assert_lifecycle(events)
    assert wire.closed.is_set()


async def test_runtime_content_part_lifecycle_does_not_duplicate_done_text():
    part = {"type": "output_text", "text": "Hello world", "annotations": []}
    item = {"type": "message", "id": "msg_0", "role": "assistant", "content": [part]}
    wire = WireStream(
        [
            created(),
            event("response.in_progress", response={"id": "resp_test"}),
            event(
                "response.output_item.added",
                output_index=0,
                item={**item, "content": []},
            ),
            event(
                "response.content_part.added",
                output_index=0,
                content_index=0,
                item_id="msg_0",
                part={**part, "text": ""},
            ),
            text_delta("Hello"),
            # Done-only suffix support is useful for compatible endpoints.
            event(
                "response.output_text.done",
                output_index=0,
                content_index=0,
                item_id="msg_0",
                text="Hello world",
            ),
            event(
                "response.content_part.done",
                output_index=0,
                content_index=0,
                item_id="msg_0",
                part=part,
            ),
            event("response.output_item.done", output_index=0, item=item),
            terminal(output=[item]),
        ]
    )
    with service_for(wire) as (service, _):
        events = await collect(service)
    assert_lifecycle(events)
    assert (
        "".join(
            e["delta"]["text"] for e in events if e["type"] == "content_block_delta"
        )
        == "Hello world"
    )


async def test_reasoning_multiple_parts_tools_and_usage_match_sync():
    reasoning = {
        "type": "reasoning",
        "id": "rs_0",
        "summary": [
            {"type": "summary_text", "text": "Think."},
            {"type": "summary_text", "text": "Check."},
        ],
    }
    tool = {
        "type": "function_call",
        "id": "fc_2",
        "call_id": "call_2",
        "name": "lookup",
        "arguments": '{"q":"a"}',
    }
    final = terminal(
        output=[
            reasoning,
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Ready"}],
            },
            tool,
        ],
        usage={
            "input_tokens": 100,
            "output_tokens": 40,
            "input_tokens_details": {"cached_tokens": 70, "cache_write_tokens": 10},
            "output_tokens_details": {"reasoning_tokens": 25},
        },
    )
    wire = WireStream(
        [
            created(),
            event(
                "response.reasoning_summary_text.delta",
                item_id="rs_0",
                output_index=0,
                summary_index=0,
                delta="Think.",
            ),
            event(
                "response.reasoning_summary_text.done",
                item_id="rs_0",
                output_index=0,
                summary_index=0,
                text="Think.",
            ),
            event(
                "response.reasoning_summary_text.delta",
                item_id="rs_0",
                output_index=0,
                summary_index=1,
                delta="Check.",
            ),
            text_delta("Ready", output_index=1),
            tool_item(2, "call_2"),
            event(
                "response.function_call_arguments.delta",
                output_index=2,
                item_id="fc_2",
                delta='{"q":',
            ),
            event(
                "response.function_call_arguments.delta",
                output_index=2,
                item_id="fc_2",
                delta='"a"}',
            ),
            event(
                "response.function_call_arguments.done",
                output_index=2,
                item_id="fc_2",
                arguments='{"q":"a"}',
            ),
            event("response.output_item.done", output_index=2, item=tool),
            final,
        ]
    )
    with service_for(wire) as (service, _):
        events = await collect(service)
    assert_lifecycle(events)
    starts = [e["content_block"] for e in events if e["type"] == "content_block_start"]
    assert [b["type"] for b in starts] == ["thinking", "thinking", "text", "tool_use"]
    assert starts[-1]["id"] == "call_2"
    deltas = [e["delta"] for e in events if e["type"] == "content_block_delta"]
    assert [d["thinking"] for d in deltas if d["type"] == "thinking_delta"] == [
        "Think.",
        "Check.",
    ]
    assert (
        "".join(d["partial_json"] for d in deltas if d["type"] == "input_json_delta")
        == tool["arguments"]
    )
    sync = OpenAIResponsesToAnthropicConverter().convert_response(
        final["response"], "m"
    )
    assert events[-2]["usage"] == sync.usage.model_dump(exclude_none=True)
    assert events[-2]["usage"]["input_tokens"] == 20
    assert events[-2]["usage"]["cache_creation_input_tokens"] == 10
    assert events[-2]["usage"]["cache_read_input_tokens"] == 70
    assert events[-2]["delta"]["stop_reason"] == sync.stop_reason == "tool_use"


async def test_interleaved_tool_arguments_keep_call_identity():
    wire = WireStream(
        [
            created(),
            tool_item(0, "call_a"),
            tool_item(1, "call_b"),
            *[
                event(
                    "response.function_call_arguments.delta",
                    output_index=i,
                    item_id=f"fc_{i}",
                    delta=delta,
                )
                for i, delta in [(0, '{"a":'), (1, '{"b":'), (0, "1}"), (1, "2}")]
            ],
            terminal(),
        ]
    )
    with service_for(wire) as (service, _):
        events = await collect(service)
    assert_lifecycle(events)
    inputs = {}
    ids = {}
    for e in events:
        if e["type"] == "content_block_start":
            ids[e["index"]] = e["content_block"]["id"]
            inputs[e["index"]] = ""
        elif e["type"] == "content_block_delta":
            inputs[e["index"]] += e["delta"]["partial_json"]
    assert {ids[i]: json.loads(value) for i, value in inputs.items()} == {
        "call_a": {"a": 1},
        "call_b": {"b": 2},
    }


@pytest.mark.parametrize("tool", [False, True])
async def test_incomplete_token_limit_closes_blocks_and_reports_usage(tool):
    content = (
        [tool_item(0, "call_partial", '{"q":')] if tool else [text_delta("partial")]
    )
    wire = WireStream(
        [
            created(),
            *content,
            terminal(
                "incomplete",
                incomplete_details={"reason": "max_output_tokens"},
                usage={"input_tokens": 2, "output_tokens": 10},
            ),
        ]
    )
    with service_for(wire) as (service, _):
        events = await collect(service)
    assert_lifecycle(events)
    assert events[-2]["delta"]["stop_reason"] == "max_tokens"
    assert events[-2]["usage"]["output_tokens"] == 10


@pytest.mark.parametrize(
    "failure",
    [
        terminal("failed", error={"code": "server_error", "message": "Failure"}),
        event("error", code="server_error", message="Failure", param=None),
        # A missing terminal event must not masquerade as a complete response.
        None,
    ],
)
async def test_upstream_failure_or_eof_yields_error_without_success(failure):
    wire = WireStream([created(), text_delta()] + ([failure] if failure else []))
    with service_for(wire) as (service, _):
        events = await collect(service)
    assert events[-1]["type"] == "error"
    assert events[-1]["error"]["type"] == "api_error"
    assert not any(e["type"] in ("message_delta", "message_stop") for e in events)
    assert wire.closed.is_set()


async def test_http_error_maps_to_anthropic_stream_error():
    wire = WireStream([])
    with service_for(wire, status=429) as (service, _):
        events = await collect(service)
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["error"]["type"] == "rate_limit_error"


@pytest.mark.parametrize("cancel_pending_read", [False, True])
async def test_cancellation_closes_stream_without_closing_shared_client(
    cancel_pending_read,
):
    wire = WireStream([created(), terminal()], pause_after=1)
    with service_for(wire) as (service, _):
        stream = service.invoke_responses_stream(request())
        try:
            assert (
                decode(await asyncio.wait_for(anext(stream), 1))["type"]
                == "message_start"
            )
            if cancel_pending_read:
                pending = asyncio.create_task(anext(stream))
                await asyncio.sleep(0.02)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                await stream.aclose()
            assert wire.closed.is_set()
            assert not service.client.is_closed()
        finally:
            wire.release.set()
            await stream.aclose()


async def test_slow_consumer_backpressures_sdk_reader_and_can_close():
    wire = WireStream(
        [created()] + [text_delta("x") for _ in range(500)] + [terminal()]
    )
    with service_for(wire) as (service, _):
        stream = service.invoke_responses_stream(request())
        try:
            await asyncio.wait_for(anext(stream), 1)
            await asyncio.sleep(0.1)
            assert wire.produced < 100
            await asyncio.wait_for(stream.aclose(), 1)
            assert wire.closed.is_set()
        finally:
            await stream.aclose()
