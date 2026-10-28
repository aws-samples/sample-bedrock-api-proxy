"""Streaming Chat Completions must relay tool calls from the upstream Responses stream."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.api.openai_passthrough.chat_responses_adapter import (
    stream_responses_as_chat_completions,
)

WEATHER_ID, TIME_ID = "fc_weather", "fc_time"
WEATHER_CALL, TIME_CALL = "call_weather", "call_time"


def _sse(*events: dict[str, Any]) -> bytes:
    return (
        "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    ).encode()


def _item(item_id: str, call_id: str, name: str, arguments: str, status: str):
    return {
        "type": "function_call",
        "id": item_id,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": status,
    }


def _completed(*output: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "response.completed",
        "response": {
            "id": "resp_1",
            "model": "global.openai.gpt-6.1-sol",
            "status": "completed",
            "output": list(output),
            "usage": {"input_tokens": 80, "output_tokens": 40},
        },
    }


def _arg_deltas(item_id: str, output_index: int, *fragments: str):
    return [
        {
            "type": "response.function_call_arguments.delta",
            "item_id": item_id,
            "output_index": output_index,
            "delta": fragment,
        }
        for fragment in fragments
    ]


CREATED = {
    "type": "response.created",
    "response": {"id": "resp_1", "model": "global.openai.gpt-6.1-sol", "output": []},
}
WEATHER_DONE = _item(
    WEATHER_ID, WEATHER_CALL, "get_weather", '{"city":"Shanghai"}', "completed"
)
TIME_DONE = _item(TIME_ID, TIME_CALL, "get_time", '{"city":"Tokyo"}', "completed")

# Event order observed from Bedrock Runtime /openai/v1/responses for a prompt that
# triggers two parallel function calls (ids shortened, obfuscation padding removed).
PARALLEL_CALL_EVENTS = [
    CREATED,
    {"type": "response.in_progress", "response": {"id": "resp_1", "output": []}},
    {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": _item(WEATHER_ID, WEATHER_CALL, "get_weather", "", "in_progress"),
    },
    *_arg_deltas(WEATHER_ID, 0, "{", '"city', '":"', "Shanghai", '"}'),
    {
        "type": "response.function_call_arguments.done",
        "item_id": WEATHER_ID,
        "output_index": 0,
        "arguments": '{"city":"Shanghai"}',
    },
    {"type": "response.output_item.done", "output_index": 0, "item": WEATHER_DONE},
    {
        "type": "response.output_item.added",
        "output_index": 1,
        "item": _item(TIME_ID, TIME_CALL, "get_time", "", "in_progress"),
    },
    *_arg_deltas(TIME_ID, 1, "{", '"city', '":"', "Tokyo", '"}'),
    {
        "type": "response.function_call_arguments.done",
        "item_id": TIME_ID,
        "output_index": 1,
        "arguments": '{"city":"Tokyo"}',
    },
    {"type": "response.output_item.done", "output_index": 1, "item": TIME_DONE},
    _completed(WEATHER_DONE, TIME_DONE),
]


async def _convert(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resp = httpx.Response(200, content=_sse(*events))
    chunks = []
    async for raw in stream_responses_as_chat_completions(
        resp, model="gpt-6.1-sol", on_complete=lambda usage: None
    ):
        for line in raw.decode().splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                chunks.append(json.loads(line[len("data: ") :]))
    return chunks


def _rebuild_tool_calls(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reassemble tool calls the way OpenAI SDK clients do."""
    calls: dict[int, dict[str, Any]] = {}
    for chunk in chunks:
        for choice in chunk["choices"]:
            for delta in choice["delta"].get("tool_calls") or []:
                call = calls.setdefault(
                    delta["index"],
                    {"id": None, "type": None, "name": None, "arguments": ""},
                )
                call["id"] = delta.get("id") or call["id"]
                call["type"] = delta.get("type") or call["type"]
                function = delta.get("function") or {}
                call["name"] = function.get("name") or call["name"]
                call["arguments"] += function.get("arguments") or ""
    return [calls[i] for i in sorted(calls)]


def _finish_reasons(chunks: list[dict[str, Any]]) -> list[str]:
    return [
        choice["finish_reason"]
        for chunk in chunks
        for choice in chunk["choices"]
        if choice.get("finish_reason")
    ]


def _tool_call_chunk_positions(chunks: list[dict[str, Any]]) -> list[int]:
    return [
        i
        for i, chunk in enumerate(chunks)
        if any(c["delta"].get("tool_calls") for c in chunk["choices"])
    ]


async def test_parallel_function_calls_are_streamed_as_tool_call_deltas():
    chunks = await _convert(PARALLEL_CALL_EVENTS)

    assert _rebuild_tool_calls(chunks) == [
        {
            "id": WEATHER_CALL,
            "type": "function",
            "name": "get_weather",
            "arguments": '{"city":"Shanghai"}',
        },
        {
            "id": TIME_CALL,
            "type": "function",
            "name": "get_time",
            "arguments": '{"city":"Tokyo"}',
        },
    ]
    assert _finish_reasons(chunks) == ["tool_calls"]
    # Opening entry per call + one chunk per argument fragment; nothing re-sent
    # at completion because the stream already delivered everything.
    assert len(_tool_call_chunk_positions(chunks)) == 2 + 10
    # Tool calls precede finish_reason; usage chunk is still last.
    finish_at = next(
        i
        for i, c in enumerate(chunks)
        if any(x.get("finish_reason") for x in c["choices"])
    )
    assert max(_tool_call_chunk_positions(chunks)) < finish_at
    assert chunks[-1]["usage"]["completion_tokens"] == 40


async def test_first_tool_call_delta_carries_id_type_and_name():
    chunks = await _convert(PARALLEL_CALL_EVENTS)
    first = next(
        delta
        for chunk in chunks
        for choice in chunk["choices"]
        for delta in choice["delta"].get("tool_calls") or []
        if delta["index"] == 1
    )
    assert first["id"] == TIME_CALL
    assert first["type"] == "function"
    assert first["function"] == {"name": "get_time", "arguments": ""}


async def test_completed_only_stream_falls_back_to_final_output():
    chunks = await _convert([CREATED, _completed(WEATHER_DONE, TIME_DONE)])

    calls = _rebuild_tool_calls(chunks)
    assert [(c["id"], c["name"], c["arguments"]) for c in calls] == [
        (WEATHER_CALL, "get_weather", '{"city":"Shanghai"}'),
        (TIME_CALL, "get_time", '{"city":"Tokyo"}'),
    ]
    assert _finish_reasons(chunks) == ["tool_calls"]


async def test_truncated_argument_stream_is_completed_from_final_output():
    events = [
        CREATED,
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": _item(WEATHER_ID, WEATHER_CALL, "get_weather", "", "in_progress"),
        },
        *_arg_deltas(WEATHER_ID, 0, "{", '"city'),
        _completed(WEATHER_DONE),
    ]
    chunks = await _convert(events)

    calls = _rebuild_tool_calls(chunks)
    assert len(calls) == 1
    assert calls[0]["arguments"] == '{"city":"Shanghai"}'


async def test_text_only_stream_is_unchanged():
    message = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Hello!"}],
    }
    events = [
        CREATED,
        {"type": "response.output_item.added", "output_index": 0, "item": message},
        {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "Hello"},
        {"type": "response.output_text.delta", "item_id": "msg_1", "delta": "!"},
        _completed(message),
    ]
    chunks = await _convert(events)

    assert _tool_call_chunk_positions(chunks) == []
    text = "".join(
        c["delta"].get("content") or "" for chunk in chunks for c in chunk["choices"]
    )
    assert text == "Hello!"
    assert _finish_reasons(chunks) == ["stop"]
