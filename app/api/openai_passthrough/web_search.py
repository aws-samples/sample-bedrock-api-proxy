"""OpenAI Responses API web search compatibility.

This module adapts OpenAI Responses requests that use the hosted web_search
tool to the proxy's existing Anthropic Messages web search implementation.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from app.core.config import settings
from app.schemas.anthropic import Message, MessageRequest, MessageResponse
from app.schemas.web_search import UserLocation

OPENAI_WEB_SEARCH_TOOL_TYPES = {"web_search", "web_search_preview"}


class OpenAIResponsesWebSearchError(Exception):
    """Error that should be returned as an OpenAI-style JSON error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        error_type: str = "invalid_request_error",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type

    def to_error_body(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.error_type}}


@dataclass(frozen=True)
class OpenAIWebSearchOptions:
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    user_location: UserLocation | None = None
    search_context_size: str | None = None


def _web_search_tools(body: dict[str, Any]) -> list[dict[str, Any]]:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return []
    return [
        tool
        for tool in tools
        if isinstance(tool, dict) and tool.get("type") in OPENAI_WEB_SEARCH_TOOL_TYPES
    ]


def is_responses_web_search_request(body: dict[str, Any]) -> bool:
    return bool(_web_search_tools(body))


def drop_web_search_tools(body: dict[str, Any]) -> int:
    """Remove ``web_search`` tools from ``body["tools"]``, returning how many.

    A client offers web search as a capability; it is not an instruction to
    search. When the proxy cannot serve it, dropping the tool lets the turn
    proceed without it, which is what the deployment actually supports.
    Failing the request instead would take every other tool down with it — the
    Codex CLI attaches web_search to every request, so refusing makes the
    entire client unusable rather than merely search-less. The tool cannot be
    forwarded either: mantle rejects the variant and, with it, the whole array.

    Mutates ``body["tools"]`` in place.
    """
    tools = body.get("tools")
    if not isinstance(tools, list):
        return 0
    kept = [
        tool
        for tool in tools
        if not (
            isinstance(tool, dict) and tool.get("type") in OPENAI_WEB_SEARCH_TOOL_TYPES
        )
    ]
    dropped = len(tools) - len(kept)
    if dropped:
        body["tools"] = kept
    return dropped


def _block_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        dumped = block.model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    return {}


def _extract_search_count(response: MessageResponse) -> int:
    server_tool_use = response.usage.server_tool_use if response.usage else None
    if not isinstance(server_tool_use, dict):
        return 0
    value = server_tool_use.get("web_search_requests", 0)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return 0
        return max(int(value), 0)
    if not isinstance(value, str):
        return 0
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(count, 0)


def _text_and_annotations(content: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    output_parts: list[str] = []
    annotations: list[dict[str, Any]] = []

    for block in content:
        block_dict = _block_dict(block)
        if block_dict.get("type") != "text":
            continue
        text = str(block_dict.get("text") or "")
        if not text:
            continue
        start = sum(len(part) for part in output_parts)
        if output_parts:
            output_parts.append("\n")
            start += 1
        output_parts.append(text)
        end = start + len(text)
        for citation in block_dict.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            if citation.get("type") != "web_search_result_location":
                continue
            annotations.append(
                {
                    "type": "url_citation",
                    "url": citation.get("url", ""),
                    "title": citation.get("title", ""),
                    "start_index": start,
                    "end_index": end,
                }
            )

    return "".join(output_parts), annotations


def build_response_json(
    response: MessageResponse,
    *,
    original_model: str,
    response_id: str | None = None,
) -> dict[str, Any]:
    response_id = response_id or f"resp_{uuid4().hex[:24]}"
    output_text, annotations = _text_and_annotations(response.content)
    search_count = _extract_search_count(response)

    output: list[dict[str, Any]] = []
    for _ in range(search_count):
        output.append(
            {
                "id": f"ws_{uuid4().hex[:24]}",
                "type": "web_search_call",
                "status": "completed",
            }
        )

    output.append(
        {
            "id": response.id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": output_text,
                    "annotations": annotations,
                }
            ],
        }
    )

    input_tokens = int(response.usage.input_tokens if response.usage else 0)
    output_tokens = int(response.usage.output_tokens if response.usage else 0)
    data: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": original_model,
        "output": output,
        "output_text": output_text,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }
    if search_count > 0:
        data["metadata"] = {"web_search_requests": search_count}
    return data


def ensure_web_search_enabled() -> None:
    if not settings.enable_web_search:
        raise OpenAIResponsesWebSearchError("Web search is disabled")


async def handle_non_streaming_web_search(
    body: dict[str, Any],
    *,
    message_request: MessageRequest | None = None,
    web_search_service: Any,
    bedrock_service: Any,
    request_id: str,
    service_tier: str,
) -> dict[str, Any]:
    ensure_web_search_enabled()
    message_request = message_request or build_message_request(body)
    response = await web_search_service.handle_request(
        request=message_request,
        bedrock_service=bedrock_service,
        request_id=request_id,
        service_tier=service_tier,
        anthropic_beta=None,
    )
    data = build_response_json(
        response,
        original_model=body.get("model", ""),
        response_id=request_id,
    )
    return data


def _sse(event_type: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    ).encode()


async def stream_response_events(
    response: MessageResponse,
    *,
    original_model: str,
    response_id: str | None = None,
    response_data: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    data = response_data or build_response_json(
        response,
        original_model=original_model,
        response_id=response_id,
    )
    sequence_number = 0

    def event(event_type: str, payload: dict[str, Any]) -> bytes:
        nonlocal sequence_number
        numbered_payload = dict(payload)
        numbered_payload["sequence_number"] = sequence_number
        sequence_number += 1
        return _sse(event_type, numbered_payload)

    response_stub = {
        key: data[key]
        for key in ("id", "object", "created_at", "status", "model")
        if key in data
    }

    yield event(
        "response.created",
        {"type": "response.created", "response": response_stub},
    )

    for index, item in enumerate(data["output"]):
        added_item = item
        if item.get("type") == "message":
            added_item = dict(item)
            added_item["status"] = "in_progress"
            added_item["content"] = []

        yield event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": index,
                "item": added_item,
            },
        )
        if item.get("type") == "message":
            content = item.get("content") or []
            if content:
                empty_part = {"type": "output_text", "text": "", "annotations": []}
                yield event(
                    "response.content_part.added",
                    {
                        "type": "response.content_part.added",
                        "item_id": item["id"],
                        "output_index": index,
                        "content_index": 0,
                        "part": empty_part,
                    },
                )
                text = content[0].get("text", "")
                if text:
                    yield event(
                        "response.output_text.delta",
                        {
                            "type": "response.output_text.delta",
                            "item_id": item["id"],
                            "output_index": index,
                            "content_index": 0,
                            "delta": text,
                        },
                    )
                    yield event(
                        "response.output_text.done",
                        {
                            "type": "response.output_text.done",
                            "item_id": item["id"],
                            "output_index": index,
                            "content_index": 0,
                            "text": text,
                        },
                    )
                yield event(
                    "response.content_part.done",
                    {
                        "type": "response.content_part.done",
                        "item_id": item["id"],
                        "output_index": index,
                        "content_index": 0,
                        "part": content[0],
                    },
                )
        yield event(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "output_index": index,
                "item": item,
            },
        )

    completed = dict(data)
    completed["status"] = "completed"
    yield event(
        "response.completed",
        {"type": "response.completed", "response": completed},
    )


def _as_str_list(value: Any, field_name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OpenAIResponsesWebSearchError(f"{field_name} must be an array of strings")
    return value


def _parse_user_location(raw: Any) -> UserLocation | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise OpenAIResponsesWebSearchError("user_location must be an object")
    for field_name in ("type", "city", "region", "country", "timezone"):
        value = raw.get(field_name)
        if value is not None and not isinstance(value, str):
            raise OpenAIResponsesWebSearchError(
                f"user_location.{field_name} must be a string"
            )
    if raw.get("type", "approximate") != "approximate":
        raise OpenAIResponsesWebSearchError(
            "Only approximate user_location is supported"
        )
    return UserLocation(
        type="approximate",
        city=raw.get("city"),
        region=raw.get("region"),
        country=raw.get("country"),
        timezone=raw.get("timezone"),
    )


def _options_signature(options: OpenAIWebSearchOptions) -> tuple[Any, ...]:
    return (
        tuple(options.allowed_domains or []),
        tuple(options.blocked_domains or []),
        options.user_location.model_dump() if options.user_location else None,
        options.search_context_size,
    )


def extract_web_search_options(body: dict[str, Any]) -> OpenAIWebSearchOptions:
    tools = _web_search_tools(body)
    if not tools:
        raise OpenAIResponsesWebSearchError("No web_search tool found")

    parsed: list[OpenAIWebSearchOptions] = []
    for tool in tools:
        if tool.get("external_web_access") is False:
            raise OpenAIResponsesWebSearchError(
                "external_web_access=false is not supported by this proxy"
            )
        if "return_token_budget" in tool:
            raise OpenAIResponsesWebSearchError(
                "return_token_budget is not supported by this proxy"
            )

        filters = tool.get("filters")
        if filters is None:
            filters = {}
        if not isinstance(filters, dict):
            raise OpenAIResponsesWebSearchError("filters must be an object")

        location = tool.get("user_location", body.get("user_location"))
        parsed.append(
            OpenAIWebSearchOptions(
                allowed_domains=_as_str_list(
                    filters.get("allowed_domains"), "filters.allowed_domains"
                ),
                blocked_domains=_as_str_list(
                    filters.get("blocked_domains"), "filters.blocked_domains"
                ),
                user_location=_parse_user_location(location),
                search_context_size=tool.get("search_context_size"),
            )
        )

    first = parsed[0]
    first_sig = _options_signature(first)
    if any(_options_signature(item) != first_sig for item in parsed[1:]):
        raise OpenAIResponsesWebSearchError(
            "Conflicting web_search tool definitions are not supported"
        )
    return first


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type")
                if item_type in {"input_text", "output_text", "text"}:
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        return "\n".join(part for part in parts if part)
    return ""


def _convert_input_to_messages(input_value: Any) -> list[Message]:
    if isinstance(input_value, str):
        if not input_value.strip():
            raise OpenAIResponsesWebSearchError("input must not be empty")
        return [Message(role="user", content=input_value)]

    if not isinstance(input_value, list) or not input_value:
        raise OpenAIResponsesWebSearchError("input is required for web_search requests")

    messages: list[Message] = []
    for item in input_value:
        if isinstance(item, str):
            messages.append(Message(role="user", content=item))
            continue
        if not isinstance(item, dict):
            continue
        role_value = item.get("role")
        role: Literal["user", "assistant"]
        if role_value == "user":
            role = "user"
        elif role_value == "assistant":
            role = "assistant"
        else:
            continue
        text = _content_text(item.get("content", ""))
        if text:
            messages.append(Message(role=role, content=text))

    if not messages:
        raise OpenAIResponsesWebSearchError("input must contain at least one message")
    return messages


def _copy_message(message: Message) -> Message:
    dumped = message.model_dump()
    return Message(role=dumped["role"], content=_content_text(dumped.get("content")))


def _messages_with_previous_context(
    body: dict[str, Any],
    previous_messages: list[Message] | None,
) -> list[Message]:
    messages = _convert_input_to_messages(body.get("input"))
    previous_response_id = body.get("previous_response_id")
    if previous_response_id is None:
        return messages
    if not isinstance(previous_response_id, str) or not previous_response_id.strip():
        raise OpenAIResponsesWebSearchError("previous_response_id must be a string")
    if previous_messages is None:
        raise OpenAIResponsesWebSearchError(
            f"previous_response_id {previous_response_id!r} was not found",
            status_code=404,
        )
    return [_copy_message(message) for message in previous_messages] + messages


def _max_tokens(body: dict[str, Any]) -> int:
    if "max_output_tokens" in body:
        max_tokens_value = body["max_output_tokens"]
    elif "max_tokens" in body:
        max_tokens_value = body["max_tokens"]
    else:
        max_tokens_value = 4096

    try:
        max_tokens = int(max_tokens_value)
    except (TypeError, ValueError) as exc:
        raise OpenAIResponsesWebSearchError(
            "max_output_tokens must be an integer greater than 0"
        ) from exc
    if max_tokens < 1:
        raise OpenAIResponsesWebSearchError("max_output_tokens must be greater than 0")
    return max_tokens


def _tool_choice(body: dict[str, Any]) -> Any:
    choice = body.get("tool_choice")
    if choice is None:
        return None
    if isinstance(choice, str):
        if choice == "auto":
            return None
        if choice in {"required", "web_search"}:
            return {"type": "tool", "name": "web_search"}
    if isinstance(choice, dict):
        if choice.get("type") in OPENAI_WEB_SEARCH_TOOL_TYPES:
            return {"type": "tool", "name": "web_search"}
        function = choice.get("function")
        if isinstance(function, dict) and function.get("name") == "web_search":
            return {"type": "tool", "name": "web_search"}
    raise OpenAIResponsesWebSearchError(
        "Only auto or web_search tool_choice is supported"
    )


def build_message_request(
    body: dict[str, Any],
    *,
    previous_messages: list[Message] | None = None,
) -> MessageRequest:
    options = extract_web_search_options(body)
    max_tokens = _max_tokens(body)

    web_search_tool: dict[str, Any] = {
        "type": "web_search_20250305",
        "name": "web_search",
    }
    if options.allowed_domains:
        web_search_tool["allowed_domains"] = options.allowed_domains
    if options.blocked_domains:
        web_search_tool["blocked_domains"] = options.blocked_domains
    if options.user_location:
        web_search_tool["user_location"] = options.user_location.model_dump(
            exclude_none=True
        )

    try:
        return MessageRequest(
            model=str(body.get("model") or ""),
            messages=_messages_with_previous_context(body, previous_messages),
            max_tokens=max_tokens,
            system=body.get("instructions"),
            temperature=body.get("temperature"),
            top_p=body.get("top_p"),
            top_k=None,
            stream=False,
            tools=[web_search_tool],
            tool_choice=_tool_choice(body),
        )
    except ValidationError as exc:
        raise OpenAIResponsesWebSearchError(str(exc)) from exc
