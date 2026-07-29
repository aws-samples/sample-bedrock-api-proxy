"""Adapt Chat Completions requests to upstream Responses API calls.

The public endpoint remains Chat Completions-compatible. These helpers translate
only the proxy-to-upstream leg and then map Responses output back to Chat
Completions shape for existing OpenAI SDK clients.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

# Learned per-model unsupported params: model -> {param: learned_at (monotonic)}.
# Populated when upstream 400s with unsupported_parameter/unknown_parameter so
# subsequent requests strip the param proactively instead of paying the 400
# round-trip every time. In-memory per worker (same precedent as the token
# bucket rate limiter); entries expire so a model that gains support recovers.
_unsupported_param_cache: dict[str, dict[str, float]] = {}

UNSUPPORTED_PARAM_CACHE_TTL_SECONDS = 3600.0

_CHAT_TO_RESPONSES_COPY_FIELDS = {
    "temperature",
    "top_p",
    "stream",
    "metadata",
    "reasoning",
    "parallel_tool_calls",
    "service_tier",
    "user",
}

# How many upstream 400 "unsupported_parameter" errors we absorb by dropping
# the named param and retrying. Bounded by the number of droppable sampling
# params a Chat Completions client realistically sends (temperature, top_p,
# stop, ...).
MAX_UNSUPPORTED_PARAM_RETRIES = 4

# Upstream 400 codes that name a request param the model won't accept.
# xai.grok-4.3 uses "unsupported_parameter" for temperature/top_p and
# "unknown_parameter" for stop/seed.
_DROPPABLE_PARAM_ERROR_CODES = {"unsupported_parameter", "unknown_parameter"}


def pop_unsupported_parameter(
    body: dict[str, Any],
    status_code: int,
    error_payload: Any,
) -> str | None:
    """Remove a top-level param the upstream rejected as unsupported.

    Some Responses-API models reject sampling params outright (e.g.
    xai.grok-4.3 400s on 'temperature': "Unsupported parameter: 'temperature'
    is not supported with this model."), but Chat Completions clients send
    them unconditionally. The 400 carries code="unsupported_parameter" (or
    "unknown_parameter") plus the param name, so the caller can drop it and
    retry.

    Returns the dropped param name, or None if the error is not a droppable
    unsupported-parameter error (caller should surface it verbatim).

    The (model, param) pair is remembered in-memory so later requests strip
    the param proactively via strip_learned_unsupported_params() instead of
    paying the 400 round-trip on every request.
    """
    if status_code != 400 or not isinstance(error_payload, dict):
        return None
    error = error_payload.get("error")
    if (
        not isinstance(error, dict)
        or error.get("code") not in _DROPPABLE_PARAM_ERROR_CODES
    ):
        return None
    param = error.get("param")
    if isinstance(param, str) and param in body:
        del body[param]
        model = body.get("model")
        if isinstance(model, str) and model:
            _unsupported_param_cache.setdefault(model, {})[param] = time.monotonic()
        return param
    return None


def strip_learned_unsupported_params(body: dict[str, Any]) -> list[str]:
    """Proactively remove params this model previously rejected as unsupported.

    Consults the in-memory cache populated by pop_unsupported_parameter().
    Entries older than UNSUPPORTED_PARAM_CACHE_TTL_SECONDS are evicted, so a
    model that gains support for a param recovers within the TTL.

    Returns the list of removed param names (for logging).
    """
    model = body.get("model")
    if not isinstance(model, str):
        return []
    learned = _unsupported_param_cache.get(model)
    if not learned:
        return []
    now = time.monotonic()
    stripped: list[str] = []
    for param, learned_at in list(learned.items()):
        if now - learned_at > UNSUPPORTED_PARAM_CACHE_TTL_SECONDS:
            del learned[param]
            continue
        if param in body:
            del body[param]
            stripped.append(param)
    if not learned:
        _unsupported_param_cache.pop(model, None)
    return stripped


def reset_unsupported_param_cache_for_testing() -> None:
    """Clear the learned-params cache — only call from test fixtures."""
    _unsupported_param_cache.clear()


# bedrock-mantle implements only two Responses-API tool variants. Everything
# else in the OpenAI spec (custom, namespace, web_search, code_interpreter,
# file_search, image_generation, computer_use_preview, local_shell, ...) is
# rejected at deserialization time, failing the entire request. Verified by
# probing the upstream directly.
_MANTLE_SUPPORTED_TOOL_TYPES = frozenset({"function", "mcp"})

# A `custom` tool takes free-form text rather than JSON arguments. The closest
# function equivalent is one string parameter, and OpenAI's native custom tool
# hands the client that text as `input`, so reusing the name keeps the tool call
# readable by an unmodified client.
_CUSTOM_TOOL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "input": {
            "type": "string",
            "description": "Raw text payload for this tool, passed through verbatim.",
        }
    },
    "required": ["input"],
    "additionalProperties": False,
}


# Conversation-history item types mantle can deserialize. Anything outside this
# set fails the whole request with
#   Invalid 'input': value did not match any expected variant
# before the model is even consulted. Verified by probing each type upstream.
_MANTLE_SUPPORTED_INPUT_TYPES = frozenset({
    "message",
    "reasoning",
    "function_call",
    "function_call_output",
    "mcp_call",
    "mcp_list_tools",
    "item_reference",
})

# Reasoning-effort values mantle actually serves. Its schema advertises six
# (none, minimal, low, medium, high, xhigh) and rejects anything else at
# deserialization:
#   Invalid 'reasoning': Invalid 'effort': unknown variant `max`, expected one of
#   `high`, `low`, `medium`, `minimal`, `none`, `xhigh`
# But probing gpt-oss-120b shows none/minimal/xhigh deserialize and then fail the
# request with a 500-style "server had an error", so only three are usable.
# Newer clients offer tiers above high (Codex exposes ultra and max), which must
# be clamped rather than forwarded.
_MANTLE_USABLE_REASONING_EFFORTS = ("low", "medium", "high")

# Map every effort a client might send onto a usable tier, preserving relative
# intent: anything below low rounds up to low (the request must still reason),
# anything above high clamps to high (the strongest tier available).
_REASONING_EFFORT_CLAMP = {
    "none": "low",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "ultra": "high",
    "max": "high",
}

# These deserialize but are then rejected semantically with
# "Unknown input type: <t>" (HTTP 200 carrying an error body). They are the
# echo of a `custom` tool, which downgrade_unsupported_tools() has already
# rewritten as a function — so the history has to be rewritten to match, or the
# next turn fails on the tool call the model itself produced.
_CUSTOM_CALL_TO_FUNCTION_CALL = {
    "custom_tool_call": "function_call",
    "custom_tool_call_output": "function_call_output",
}


def _function_tool(name: str, description: str, parameters: Any) -> dict[str, Any]:
    """Build a Responses-API function tool, omitting an empty description."""
    tool: dict[str, Any] = {
        "type": "function",
        "name": name,
        "parameters": _normalize_null_required_fields(parameters),
    }
    if isinstance(description, str) and description:
        tool["description"] = description
    return tool


def downgrade_unsupported_tools(body: dict[str, Any]) -> list[str]:
    """Rewrite tool variants bedrock-mantle rejects into ``function`` tools.

    Mantle accepts only ``function`` and ``mcp``; any other variant fails the
    whole request at deserialization::

        Failed to deserialize the JSON body into the target type: ?[12]:
        Invalid 'tools': unknown variant `namespace`, expected `function` or `mcp`

    The error names no parameter (``param`` is null), so the learned-unsupported
    -param retry in pop_unsupported_parameter() cannot help: the offending value
    is a variant tag inside an array, not a top-level field. And because one bad
    entry rejects the entire array, a single unsupported tool disables every tool
    in the request.

    Dropping the tools is not viable — for the Codex CLI ``apply_patch`` is how
    the model edits files. Each variant is instead mapped to the nearest
    supported shape:

    * ``custom`` -> a function taking one string parameter ``input`` (the
      variant carries free-form text, and ``input`` is the argument name
      OpenAI's own custom tool produces).
    * ``namespace`` -> its nested tools flattened to top level as
      ``<namespace>.<tool>``. Dotted names are accepted upstream and preserve
      the grouping the client sent, so the returned call still identifies the
      intended tool.
    * anything else with a usable ``name`` -> a function keeping its declared
      ``parameters`` if present, else accepting a free-form ``input`` string.

    A tool with no usable name is left untouched so the upstream error surfaces
    verbatim instead of this code inventing a tool the client never declared.

    Mutates ``body["tools"]`` in place. Returns the rewritten tool names, for
    logging.
    """
    tools = body.get("tools")
    if not isinstance(tools, list):
        return []

    rewritten: list[str] = []
    result: list[Any] = []

    for tool in tools:
        if not isinstance(tool, dict):
            result.append(tool)
            continue

        tool_type = tool.get("type")
        if tool_type in _MANTLE_SUPPORTED_TOOL_TYPES:
            result.append(tool)
            continue

        name = tool.get("name")
        if not isinstance(name, str) or not name:
            result.append(tool)
            continue

        description = tool.get("description")
        if not isinstance(description, str):
            description = ""

        if tool_type == "namespace":
            nested = tool.get("tools")
            if isinstance(nested, list) and nested:
                for child in nested:
                    if not isinstance(child, dict):
                        continue
                    child_name = child.get("name")
                    if not isinstance(child_name, str) or not child_name:
                        continue
                    child_desc = child.get("description")
                    if not isinstance(child_desc, str) or not child_desc:
                        child_desc = description
                    qualified = f"{name}.{child_name}"
                    child_params = child.get("parameters")
                    if not isinstance(child_params, dict):
                        child_params = _CUSTOM_TOOL_INPUT_SCHEMA
                    result.append(_function_tool(qualified, child_desc, child_params))
                    rewritten.append(qualified)
                continue
            # An empty namespace has nothing to call; drop it rather than
            # forwarding a variant that would reject the whole request.
            rewritten.append(name)
            continue

        if tool_type == "custom":
            parameters: Any = _CUSTOM_TOOL_INPUT_SCHEMA
        else:
            declared = tool.get("parameters")
            parameters = declared if isinstance(declared, dict) else _CUSTOM_TOOL_INPUT_SCHEMA

        result.append(_function_tool(name, description, parameters))
        rewritten.append(name)

    if rewritten:
        body["tools"] = result
    return rewritten


# tool_choice values mantle serves. It rejects everything else with
#   does not support tool_choice '"required"' ... Supported options: ["auto"]
# so a client asking the model to be forced into (or out of) a tool call fails
# the whole request.
_MANTLE_SUPPORTED_TOOL_CHOICES = frozenset({"auto"})

# Content-part types accepted inside a user/system/developer message.
_MANTLE_SUPPORTED_CONTENT_PARTS = frozenset({"input_text"})


def normalize_message_content(body: dict[str, Any]) -> list[str]:
    """Reshape message content into the narrow form mantle accepts.

    Two constraints, both found by probing the upstream:

    * Assistant messages must carry a plain string. A content-part array — which
      is what the Responses API specifies and what clients replay from a previous
      turn — triggers ``SubmitRequestFailure ... 219 validation errors``.
    * User/system/developer messages accept only ``input_text`` parts. ``text``
      (the Chat Completions spelling), ``refusal``, ``input_audio`` and
      ``input_image`` are rejected at deserialization; ``input_file`` fails
      server-side.

    Non-text parts cannot be represented, so they are replaced with a short
    textual placeholder rather than dropped silently: the model still sees that
    an attachment was present, and the turn structure stays intact.

    Mutates ``body["input"]`` in place. Returns notes describing what changed.
    """
    items = body.get("input")
    if not isinstance(items, list):
        return []

    notes: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in (None, "message"):
            continue
        content = item.get("content")
        role = item.get("role")

        if role == "assistant":
            if isinstance(content, list):
                text = _content_to_text(content)
                item["content"] = text
                notes.append("assistant content[]->str")
            continue

        if not isinstance(content, list):
            continue

        converted: list[Any] = []
        changed = False
        for part in content:
            if not isinstance(part, dict):
                converted.append(part)
                continue
            part_type = part.get("type")
            if part_type in _MANTLE_SUPPORTED_CONTENT_PARTS:
                converted.append(part)
                continue
            text = part.get("text")
            if part_type == "text" and isinstance(text, str):
                # Same payload, different spelling.
                converted.append({"type": "input_text", "text": text})
                changed = True
                notes.append("text->input_text")
                continue
            placeholder = _content_part_placeholder(part_type, part)
            converted.append({"type": "input_text", "text": placeholder})
            changed = True
            notes.append(f"{part_type}->input_text placeholder")
        if changed:
            item["content"] = converted

    return notes


def _content_part_placeholder(part_type: Any, part: dict[str, Any]) -> str:
    """Describe an unrepresentable content part as text."""
    if part_type == "refusal":
        refusal = part.get("refusal")
        return str(refusal) if isinstance(refusal, str) and refusal else "[refusal]"
    if part_type == "input_image":
        return "[image omitted: not supported by this upstream model]"
    if part_type == "input_file":
        name = part.get("filename")
        label = f" {name}" if isinstance(name, str) and name else ""
        return f"[file{label} omitted: not supported by this upstream model]"
    if part_type == "input_audio":
        return "[audio omitted: not supported by this upstream model]"
    text = part.get("text")
    if isinstance(text, str) and text:
        return text
    return f"[{part_type or 'content'} omitted: not supported by this upstream model]"


def clamp_tool_choice(body: dict[str, Any]) -> str | None:
    """Reduce ``tool_choice`` to ``auto``, the only value mantle serves.

    Rejecting the request is worse than relaxing the constraint: ``required`` and
    a named-function choice are both requests to *use* tools, and ``auto`` still
    permits that — the model simply is not forced. ``none`` is the one case where
    intent is inverted, so the tools are removed instead of silently allowing
    calls the client asked to suppress.

    Returns a note describing the change, or None if nothing changed.
    """
    if "tool_choice" not in body:
        return None
    choice = body["tool_choice"]

    if isinstance(choice, str):
        if choice in _MANTLE_SUPPORTED_TOOL_CHOICES:
            return None
        if choice == "none":
            # Honour "do not call tools" by withholding the tools entirely.
            body["tool_choice"] = "auto"
            if body.get("tools"):
                body["tools"] = []
                return "tool_choice none->auto (tools withheld)"
            return "tool_choice none->auto"
        body["tool_choice"] = "auto"
        return f"tool_choice {choice}->auto"

    if isinstance(choice, dict):
        described = choice.get("name") or choice.get("type") or "object"
        body["tool_choice"] = "auto"
        return f"tool_choice {described}->auto"

    return None


def clamp_reasoning_effort(body: dict[str, Any]) -> str | None:
    """Clamp ``reasoning.effort`` to a tier bedrock-mantle actually serves.

    Mantle rejects unknown values at deserialization, failing the whole request::

        Invalid 'reasoning': Invalid 'effort': unknown variant `max`, expected
        one of `high`, `low`, `medium`, `minimal`, `none`, `xhigh`

    Clients keep adding tiers above `high` — the Codex CLI exposes `ultra` and
    `max` — so forwarding the value verbatim breaks every request from a client
    configured that way, with an error that looks like a proxy bug.

    The advertised set is also wider than the usable one: probing gpt-oss-120b
    shows `none`, `minimal` and `xhigh` deserialize and then fail the request
    outright, so those are remapped too rather than passed through.

    Clamping preserves intent in the only direction available: below `low` rounds
    up (the caller still wants reasoning), above `high` saturates at the
    strongest usable tier. Returns a "from->to" note when the value changed, else
    None.
    """
    reasoning = body.get("reasoning")
    if not isinstance(reasoning, dict):
        return None
    effort = reasoning.get("effort")
    if not isinstance(effort, str) or not effort:
        return None

    normalized = effort.strip().lower()
    if normalized in _MANTLE_USABLE_REASONING_EFFORTS:
        return None

    # Unknown future tiers are clamped to `high` rather than dropped: a client
    # asking for an effort we do not recognise is asking for more reasoning, not
    # less, and forwarding it would fail the request.
    replacement = _REASONING_EFFORT_CLAMP.get(normalized, "high")
    reasoning["effort"] = replacement
    return f"{effort}->{replacement}"


def sanitize_input_items(body: dict[str, Any]) -> list[str]:
    """Drop or rewrite conversation-history items mantle cannot accept.

    ``body["input"]`` carries the prior turns of the conversation. Mantle
    deserializes only a subset of the OpenAI item types; anything else fails the
    entire request before the model is consulted::

        Failed to deserialize the JSON body into the target type:
        Invalid 'input': value did not match any expected variant

    Since one bad item rejects the whole array, a single unsupported entry makes
    every subsequent turn fail — the conversation becomes permanently stuck,
    because the offending item stays in the history the client replays.

    Two classes are handled:

    * ``custom_tool_call`` / ``custom_tool_call_output`` are renamed to their
      ``function_call`` equivalents. These are the echo of a ``custom`` tool that
      downgrade_unsupported_tools() already rewrote as a function, so the history
      must be rewritten the same way to stay consistent with the declared tools.
      The free-form ``input`` field becomes JSON ``arguments`` to match.
    * Items whose type mantle does not implement at all (``web_search_call``,
      ``local_shell_call``, ``computer_call``, ``file_search_call``,
      ``image_generation_call``, ``code_interpreter_call``, ...) are dropped.
      They describe tool activity that did not happen upstream, so removing them
      loses provider-side annotations but keeps the conversation usable.

    Items with no ``type`` are left alone: mantle accepts a bare
    ``{"role", "content"}`` message, which is what clients commonly send.

    Mutates ``body["input"]`` in place. Returns human-readable notes describing
    what changed, for logging.
    """
    items = body.get("input")
    if not isinstance(items, list):
        return []

    notes: list[str] = []
    result: list[Any] = []

    for item in items:
        if not isinstance(item, dict):
            result.append(item)
            continue

        item_type = item.get("type")
        if item_type is None:
            # A plain {"role": ..., "content": ...} message.
            result.append(item)
            continue

        replacement_type = _CUSTOM_CALL_TO_FUNCTION_CALL.get(item_type)
        if replacement_type is not None:
            converted = {
                key: value for key, value in item.items() if key != "input"
            }
            converted["type"] = replacement_type
            if replacement_type == "function_call":
                # A custom tool call carries free-form text in `input`; the
                # function equivalent expects a JSON `arguments` string whose
                # shape matches _CUSTOM_TOOL_INPUT_SCHEMA.
                if "arguments" not in converted:
                    raw = item.get("input")
                    converted["arguments"] = json.dumps(
                        {"input": raw if isinstance(raw, str) else ""}
                    )
                converted.setdefault("name", item.get("name", ""))
            result.append(converted)
            notes.append(f"{item_type}->{replacement_type}")
            continue

        if item_type not in _MANTLE_SUPPORTED_INPUT_TYPES:
            notes.append(f"dropped {item_type}")
            continue

        result.append(item)

    if notes:
        body["input"] = result
    return notes


def chat_request_to_response_request(body: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI Chat Completions body into a Responses API body."""
    result: dict[str, Any] = {"model": body.get("model", "")}

    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        input_items.extend(_convert_chat_message(message, instructions))

    if instructions:
        result["instructions"] = "\n".join(part for part in instructions if part)
    result["input"] = input_items

    max_output_tokens = body.get("max_tokens", body.get("max_completion_tokens"))
    if max_output_tokens is not None:
        result["max_output_tokens"] = max_output_tokens

    for field in _CHAT_TO_RESPONSES_COPY_FIELDS:
        if field in body:
            result[field] = body[field]

    if "tools" in body:
        result["tools"] = [_convert_tool(tool) for tool in body.get("tools") or []]
    if "tool_choice" in body:
        result["tool_choice"] = _convert_tool_choice(body["tool_choice"])
    if "response_format" in body:
        result["response_format"] = body["response_format"]
    if "stop" in body:
        result["stop"] = body["stop"]

    return result


def response_to_chat_completion(
    response: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    """Convert a non-streaming Responses API body to Chat Completions shape."""
    content = _extract_response_text(response)
    tool_calls = _extract_response_tool_calls(response)
    message: dict[str, Any] = {
        "role": "assistant",
        "content": content if content or not tool_calls else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    result: dict[str, Any] = {
        "id": response.get("id") or f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(response.get("created") or time.time()),
        "model": response.get("model") or model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _finish_reason(response, tool_calls),
            }
        ],
    }
    usage = response.get("usage")
    if isinstance(usage, dict):
        result["usage"] = responses_usage_to_chat_usage(usage)
    return result


def responses_usage_to_chat_usage(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert Responses usage fields to Chat Completions usage fields."""
    prompt_tokens = int(raw.get("input_tokens", 0) or 0)
    completion_tokens = int(raw.get("output_tokens", 0) or 0)
    total_tokens = int(raw.get("total_tokens", prompt_tokens + completion_tokens) or 0)

    result: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }

    input_details = raw.get("input_tokens_details")
    if isinstance(input_details, dict):
        prompt_details: dict[str, Any] = {}
        if "cached_tokens" in input_details:
            prompt_details["cached_tokens"] = int(
                input_details.get("cached_tokens") or 0
            )
        if prompt_details:
            result["prompt_tokens_details"] = prompt_details

    output_details = raw.get("output_tokens_details")
    if isinstance(output_details, dict):
        completion_details: dict[str, Any] = {}
        if "reasoning_tokens" in output_details:
            completion_details["reasoning_tokens"] = int(
                output_details.get("reasoning_tokens") or 0
            )
        if completion_details:
            result["completion_tokens_details"] = completion_details

    return result


async def stream_responses_as_chat_completions(
    resp: httpx.Response,
    *,
    model: str,
    on_complete: Callable[[dict[str, Any]], Awaitable[None] | None],
) -> AsyncIterator[bytes]:
    """Convert an upstream Responses SSE stream to Chat Completions SSE."""
    response_id = f"chatcmpl-{int(time.time())}"
    response_model = model
    created = int(time.time())
    usage: dict[str, Any] = {}
    done_sent = False

    try:
        async for raw_line in resp.aiter_lines():
            payload = _load_sse_data(raw_line)
            if payload is None:
                continue

            event_type = payload.get("type")
            if event_type == "response.created":
                response_obj = payload.get("response") or {}
                if isinstance(response_obj, dict):
                    response_id = response_obj.get("id") or response_id
                    response_model = response_obj.get("model") or response_model
                    created = int(response_obj.get("created") or created)
                yield _chat_sse(
                    _chat_chunk(
                        response_id,
                        response_model,
                        created,
                        [{"index": 0, "delta": {"role": "assistant"}}],
                    )
                )

            elif event_type == "response.output_text.delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    yield _chat_sse(
                        _chat_chunk(
                            response_id,
                            response_model,
                            created,
                            [{"index": 0, "delta": {"content": delta}}],
                        )
                    )

            elif event_type == "response.completed":
                response_obj = payload.get("response") or {}
                tool_calls = (
                    _extract_response_tool_calls(response_obj)
                    if isinstance(response_obj, dict)
                    else []
                )
                yield _chat_sse(
                    _chat_chunk(
                        response_id,
                        response_model,
                        created,
                        [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": _finish_reason(
                                    response_obj if isinstance(response_obj, dict) else {},
                                    tool_calls,
                                ),
                            }
                        ],
                    )
                )
                if isinstance(response_obj, dict):
                    raw_usage = response_obj.get("usage")
                    if isinstance(raw_usage, dict):
                        usage.clear()
                        usage.update(raw_usage)
                        usage_chunk = _chat_chunk(
                            response_id,
                            response_model,
                            created,
                            [],
                        )
                        usage_chunk["usage"] = responses_usage_to_chat_usage(raw_usage)
                        yield _chat_sse(usage_chunk)
                yield b"data: [DONE]\n\n"
                done_sent = True

        if not done_sent:
            yield b"data: [DONE]\n\n"
    finally:
        await resp.aclose()

    if usage:
        result = on_complete(usage)
        if hasattr(result, "__await__"):
            await result  # type: ignore[misc]


def _convert_chat_message(
    message: dict[str, Any],
    instructions: list[str],
) -> list[dict[str, Any]]:
    role = message.get("role")
    content = message.get("content", "")
    if role in {"system", "developer"}:
        text = _content_to_text(content)
        if text:
            instructions.append(text)
        return []

    if role == "tool":
        return [
            {
                "type": "function_call_output",
                "call_id": message.get("tool_call_id", ""),
                "output": _content_to_text(content),
            }
        ]

    items: list[dict[str, Any]] = []
    if content not in ("", None):
        converted = _convert_content_parts(content, role or "user")
        if converted not in ("", None, []):
            items.append({"role": role or "user", "content": converted})

    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        if not isinstance(function, dict):
            function = {}
        items.append(
            {
                "type": "function_call",
                "call_id": tool_call.get("id", ""),
                "name": function.get("name", ""),
                "arguments": function.get("arguments", ""),
            }
        )
    return items


def _convert_content_parts(content: Any, role: str) -> Any:
    """Convert Chat Completions content parts to Responses API part types.

    Chat Completions uses {"type": "text"|"image_url", ...} parts, but the
    Responses API requires role-specific part types: input_text/input_image
    for user input, output_text for assistant output. Upstream rejects the
    Chat Completions shapes with "Invalid 'input': value did not match any
    expected variant".
    """
    if not isinstance(content, list):
        return content

    is_assistant = role == "assistant"
    text_type = "output_text" if is_assistant else "input_text"
    parts: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append({"type": text_type, "text": str(item)})
            continue
        part_type = item.get("type")
        if part_type in {"text", "input_text", "output_text"}:
            part: dict[str, Any] = {
                "type": text_type,
                "text": str(item.get("text", "")),
            }
            if is_assistant:
                part["annotations"] = item.get("annotations") or []
            parts.append(part)
        elif part_type == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url", "")
                detail = image_url.get("detail")
            else:
                url = "" if image_url is None else str(image_url)
                detail = None
            image_part: dict[str, Any] = {"type": "input_image", "image_url": url}
            if detail:
                image_part["detail"] = detail
            parts.append(image_part)
        else:
            # Already-native Responses parts (input_image, input_file,
            # refusal, ...) and unknown types pass through unchanged.
            parts.append(item)
    return parts


def _convert_tool(tool: Any) -> Any:
    if not isinstance(tool, dict):
        return tool
    if tool.get("type") != "function":
        return tool
    function = tool.get("function")
    if not isinstance(function, dict):
        return tool
    converted = {
        "type": "function",
        "name": function.get("name", ""),
        "description": function.get("description", ""),
        "parameters": _normalize_null_required_fields(function.get("parameters", {})),
    }
    return {key: value for key, value in converted.items() if value not in (None, "")}


def _normalize_null_required_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                []
                if key == "required" and item is None
                else _normalize_null_required_fields(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_null_required_fields(item) for item in value]
    return value


def _convert_tool_choice(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict):
        return tool_choice
    if tool_choice.get("type") != "function":
        return tool_choice
    function = tool_choice.get("function")
    if isinstance(function, dict):
        return {"type": "function", "name": function.get("name", "")}
    return tool_choice


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"text", "input_text", "output_text"}:
                    parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _extract_response_text(response: dict[str, Any]) -> str:
    texts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for entry in item.get("content") or []:
                if isinstance(entry, dict) and entry.get("type") in {
                    "output_text",
                    "text",
                }:
                    texts.append(str(entry.get("text", "")))
        elif item.get("type") == "output_text":
            texts.append(str(item.get("text", "")))
    return "".join(texts)


def _extract_response_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        call_id = item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}"
        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "") or "",
                },
            }
        )
    return tool_calls


def _finish_reason(response: dict[str, Any], tool_calls: list[dict[str, Any]]) -> str:
    if tool_calls:
        return "tool_calls"
    if response.get("status") == "incomplete":
        return "length"
    return "stop"


def _load_sse_data(raw_line: str) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _chat_chunk(
    response_id: str,
    model: str,
    created: int,
    choices: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": choices,
    }


def _chat_sse(payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return f"data: {data}\n\n".encode()
