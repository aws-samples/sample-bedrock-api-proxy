"""Regressions for Claude Code requests sent through Bedrock Converse."""

import json
import queue
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, ParamValidationError
from botocore.session import get_session
from botocore.validate import validate_parameters

from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter
from app.converters.bedrock_to_anthropic import BedrockToAnthropicConverter
from app.converters.converse_compat import ConverseRequestAdapter
from app.core.config import settings
from app.core.exceptions import ValidationError
from app.schemas.anthropic import Message, MessageRequest
from app.services.bedrock_service import BedrockService

MODEL = "global.openai.gpt-6-astra"
LONG_NAME = (
    "mcp__example-customer-service__get_customer_influences_by_account_and_service"
)
SECOND_NAME = (
    "mcp__example-agentcore-service__identity_create_workload_identity_provider"
)


def request_with_tools(names=(LONG_NAME,)):
    return {
        "modelId": MODEL,
        "messages": [{"role": "user", "content": [{"text": "Look up the account."}]}],
        "inferenceConfig": {"maxTokens": 128},
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": name,
                        "description": "",
                        "inputSchema": {"json": {"type": "object", "properties": {}}},
                    }
                }
                for name in names
            ]
        },
    }


def tool_response(name):
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "toolu_1", "name": name, "input": {}}}
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
    }


def start_event(name):
    return {
        "contentBlockStart": {
            "contentBlockIndex": 0,
            "start": {"toolUse": {"toolUseId": "toolu_1", "name": name}},
        }
    }


def validate_converse(payload, operation="Converse"):
    shape = (
        get_session().get_service_model("bedrock-runtime").operation_model(operation)
    )
    validate_parameters(payload, shape.input_shape)
    for tool in payload.get("toolConfig", {}).get("tools", []):
        if "toolSpec" in tool:
            # Botocore validates minimum lengths, but not all service-side
            # maximum lengths. Check the actual SDK model constraint as well.
            name_shape = (
                shape.input_shape.members["toolConfig"]
                .members["tools"]
                .member.members["toolSpec"]
                .members["name"]
            )
            assert len(tool["toolSpec"]["name"]) <= name_shape.metadata["max"]


def test_tools_choice_and_history_share_alias_and_leave_input_untouched():
    payload = request_with_tools((LONG_NAME, SECOND_NAME, "a" * 64))
    payload["toolConfig"]["toolChoice"] = {"tool": {"name": LONG_NAME}}
    payload["messages"] += [
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "toolu_old",
                        "name": LONG_NAME,
                        "input": {"name": LONG_NAME},
                    }
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "toolu_old",
                        "content": [{"text": LONG_NAME}],
                    }
                }
            ],
        },
    ]
    original = deepcopy(payload)
    adapter = ConverseRequestAdapter(payload, MODEL)
    converted = adapter.request
    validate_converse(converted)
    validate_converse(converted, "ConverseStream")
    names = [t["toolSpec"]["name"] for t in converted["toolConfig"]["tools"]]
    assert len(set(names)) == 3
    assert names[0] != LONG_NAME
    assert names[2] == "a" * 64
    assert converted["toolConfig"]["toolChoice"]["tool"]["name"] == names[0]
    historical_use = converted["messages"][1]["content"][0]["toolUse"]
    assert historical_use["name"] == names[0]
    assert historical_use["toolUseId"] == "toolu_old"
    assert historical_use["input"] == {"name": LONG_NAME}
    assert converted["messages"][2] == original["messages"][2]
    assert payload == original


@pytest.mark.parametrize("description", [None, "", " \n\t"])
def test_empty_descriptions_get_nonempty_fallback(description):
    payload = request_with_tools()
    payload["toolConfig"]["tools"][0]["toolSpec"]["description"] = description
    adapter = ConverseRequestAdapter(payload, MODEL)
    validate_converse(adapter.request)
    assert (
        LONG_NAME
        in adapter.request["toolConfig"]["tools"][0]["toolSpec"]["description"]
    )


def test_valid_descriptions_and_cache_points_are_preserved():
    payload = request_with_tools(("Read",))
    payload["toolConfig"]["tools"][0]["toolSpec"]["description"] = "Read a file."
    payload["toolConfig"]["tools"].append({"cachePoint": {"type": "default"}})
    assert ConverseRequestAdapter(payload, MODEL).request == payload


def test_aliases_are_stable_across_order_and_do_not_collide_with_real_names():
    payload = request_with_tools()
    alias = ConverseRequestAdapter(payload, MODEL).request["toolConfig"]["tools"][0][
        "toolSpec"
    ]["name"]
    # A client can already have a tool whose name matches our generated alias.
    combined = request_with_tools((LONG_NAME, alias))
    adapter = ConverseRequestAdapter(combined, MODEL)
    specs = adapter.request["toolConfig"]["tools"]
    assert specs[0]["toolSpec"]["name"] != alias
    assert specs[1]["toolSpec"]["name"] == alias
    combined["toolConfig"]["tools"].reverse()
    reordered = ConverseRequestAdapter(combined, MODEL)
    assert reordered.request["toolConfig"]["tools"][1] == specs[0]
    restored = adapter.restore_response(tool_response(specs[0]["toolSpec"]["name"]))
    assert restored["output"]["message"]["content"][0]["toolUse"]["name"] == LONG_NAME


def test_history_only_tool_names_are_restored_and_requests_are_isolated():
    payload = request_with_tools()
    first = ConverseRequestAdapter(payload, MODEL)
    alias = first.request["toolConfig"]["tools"][0]["toolSpec"]["name"]
    history = request_with_tools(("Read",))
    history["messages"].insert(0, tool_response(LONG_NAME)["output"]["message"])
    second = ConverseRequestAdapter(history, MODEL)
    assert second.request["messages"][0]["content"][0]["toolUse"]["name"] == alias
    unrelated = ConverseRequestAdapter(request_with_tools((SECOND_NAME,)), MODEL)
    response = tool_response(alias)
    original = deepcopy(response)
    assert (
        first.restore_response(response)["output"]["message"]["content"][0]["toolUse"][
            "name"
        ]
        == LONG_NAME
    )
    assert unrelated.restore_response(response) == original
    assert response == original


def test_stream_tool_name_is_restored_without_changing_event_or_arguments():
    adapter = ConverseRequestAdapter(request_with_tools(), MODEL)
    alias = adapter.request["toolConfig"]["tools"][0]["toolSpec"]["name"]
    event = start_event(alias)
    original = deepcopy(event)
    restored = adapter.restore_event(event)
    assert restored["contentBlockStart"]["start"]["toolUse"] == {
        "toolUseId": "toolu_1",
        "name": LONG_NAME,
    }
    assert event == original
    delta = {
        "contentBlockDelta": {
            "contentBlockIndex": 0,
            "delta": {"toolUse": {"input": '{"name":"anything"}'}},
        }
    }
    assert adapter.restore_event(delta) == delta


@pytest.mark.parametrize(
    "model",
    [
        MODEL,
        "openai.gpt-6-astra",
        "arn:aws:bedrock:us-west-2::foundation-model/openai.gpt-6-astra",
    ],
)
def test_gpt_assistant_prefill_preserved_with_explicit_continuation(model):
    payload = request_with_tools()
    payload["messages"].append(
        {"role": "assistant", "content": [{"text": '{"answer":'}]}
    )
    original = deepcopy(payload)
    adapter = ConverseRequestAdapter(payload, model)
    assert adapter.request["messages"][:-1] == payload["messages"]
    assert adapter.request["messages"][-1]["role"] == "user"
    assert "continuation" in adapter.request["messages"][-1]["content"][0]["text"]
    assert payload == original
    validate_converse(adapter.request)


@pytest.mark.parametrize(
    "model", ["anthropic.claude-sonnet-4-5", "amazon.nova-pro-v1:0"]
)
def test_other_models_keep_assistant_prefills(model):
    payload = request_with_tools(("Read",))
    payload["messages"].append({"role": "assistant", "content": [{"text": "Answer:"}]})
    adapter = ConverseRequestAdapter(payload, model)
    assert adapter.request["messages"] == payload["messages"]


def test_empty_assistant_prefill_removed_but_user_message_retained():
    payload = request_with_tools()
    payload["messages"].append({"role": "assistant", "content": []})
    adapter = ConverseRequestAdapter(payload, MODEL)
    assert adapter.request["messages"] == payload["messages"][:1]


def test_empty_only_prefill_is_a_client_error():
    payload = request_with_tools()
    payload["messages"] = [{"role": "assistant", "content": []}]
    with pytest.raises(ValidationError, match="non-empty user message"):
        ConverseRequestAdapter(payload, MODEL)


def test_inline_system_updates_move_to_system_in_order_without_changing_roles():
    payload = request_with_tools()
    payload["system"] = [{"text": "Original instructions."}]
    payload["messages"] += [
        {"role": "system", "content": [{"text": "IDE context."}]},
        {"role": "assistant", "content": [{"text": "Ready."}]},
        {"role": "user", "content": [{"text": "Hi."}]},
        {"role": "system", "content": [{"text": "Latest context."}]},
    ]
    original = deepcopy(payload)
    adapter = ConverseRequestAdapter(payload, MODEL)
    validate_converse(adapter.request)
    validate_converse(adapter.request, "ConverseStream")
    assert adapter.request["system"] == [
        {"text": "Original instructions."},
        {"text": "IDE context."},
        {"text": "Latest context."},
    ]
    assert adapter.request["messages"] == [
        message for message in original["messages"] if message["role"] != "system"
    ]
    assert adapter.request["messages"][-1]["role"] == "user"
    assert payload == original


def test_inline_system_after_assistant_still_adapts_prefill():
    payload = request_with_tools()
    payload["messages"] += [
        {"role": "assistant", "content": [{"text": "Answer:"}]},
        {"role": "system", "content": [{"text": "Be concise."}]},
    ]
    adapter = ConverseRequestAdapter(payload, MODEL)
    assert adapter.request["system"] == [{"text": "Be concise."}]
    assert adapter.request["messages"][-2] == payload["messages"][-2]
    assert adapter.request["messages"][-1]["role"] == "user"


def test_system_only_request_requires_user_input():
    payload = request_with_tools()
    payload["messages"] = [{"role": "system", "content": [{"text": "Context."}]}]
    with pytest.raises(ValidationError, match="non-empty user message"):
        ConverseRequestAdapter(payload, MODEL)


def test_inline_system_tool_call_is_not_silently_dropped():
    payload = request_with_tools()
    payload["messages"].append(
        {
            "role": "system",
            "content": tool_response(LONG_NAME)["output"]["message"]["content"],
        }
    )
    with pytest.raises(ValidationError, match="inline system messages only support"):
        ConverseRequestAdapter(payload, MODEL)


def test_names_with_shared_prefix_and_invalid_characters_remain_distinct():
    names = ("x" * 64 + "one", "x" * 64 + "two", "工具/lookup")
    adapter = ConverseRequestAdapter(request_with_tools(names), MODEL)
    aliases = [t["toolSpec"]["name"] for t in adapter.request["toolConfig"]["tools"]]
    assert len(set(aliases)) == len(names)
    assert all(alias.isascii() and len(alias) <= 64 for alias in aliases)
    for name, alias in zip(names, aliases, strict=True):
        restored = adapter.restore_response(tool_response(alias))
        assert restored["output"]["message"]["content"][0]["toolUse"]["name"] == name


def test_incomplete_tool_call_rejected_instead_of_fabricating_tool_results():
    payload = request_with_tools()
    payload["messages"].append(tool_response(LONG_NAME)["output"]["message"])
    with pytest.raises(ValidationError, match="missing tool results") as exc:
        ConverseRequestAdapter(payload, MODEL)
    assert exc.value.http_status == 400


@pytest.fixture
def service(monkeypatch):
    # Exercise real converters and service control flow with only AWS mocked.
    # Scoped GPT models default to Runtime Responses on current main; these
    # regressions target deployments explicitly using the Converse fallback.
    monkeypatch.setattr(settings, "enable_bedrock_responses", False)
    monkeypatch.setattr(
        settings,
        "default_model_mapping",
        {**settings.default_model_mapping, "gpt-6-astra": MODEL},
    )
    resolver = SimpleNamespace(resolve=lambda model: model)
    monkeypatch.setattr(
        "app.services.bedrock_service.get_inference_profile_resolver", lambda: resolver
    )
    monkeypatch.setattr(
        "app.converters.anthropic_to_bedrock.get_inference_profile_resolver",
        lambda: resolver,
    )
    service = BedrockService.__new__(BedrockService)
    service._default_provider_id = None
    service._openai_compat_service = None
    service.anthropic_to_bedrock = AnthropicToBedrockConverter()
    service.bedrock_to_anthropic = BedrockToAnthropicConverter()
    service.client = MagicMock()
    service.get_client = MagicMock(return_value=service.client)
    return service


def anthropic_request():
    return MessageRequest(
        model="gpt-6-astra",
        max_tokens=128,
        messages=[{"role": "user", "content": "Look up the account."}],
        tools=[
            {"name": LONG_NAME, "description": "", "input_schema": {"type": "object"}}
        ],
        tool_choice={"type": "tool", "name": LONG_NAME},
    )


@pytest.mark.parametrize("retry", [False, True])
def test_service_nonstream_restores_names_including_service_tier_retry(service, retry):
    calls = []

    def converse(**payload):
        validate_converse(payload)
        calls.append(deepcopy(payload))
        if retry and len(calls) == 1:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ValidationException",
                        "Message": "Model does not support service tier",
                    }
                },
                "Converse",
            )
        return tool_response(payload["toolConfig"]["tools"][0]["toolSpec"]["name"])

    service.client.converse.side_effect = converse
    response = service._invoke_model_sync_inner(
        anthropic_request(), "msg_test", service_tier="reserved" if retry else "default"
    )
    assert response.content[0].name == LONG_NAME
    assert response.content[0].id == "toolu_1"
    assert len(calls) == (2 if retry else 1)
    if retry:
        assert "serviceTier" not in calls[-1]


@pytest.mark.parametrize("retry", [False, True])
def test_service_stream_restores_names_including_service_tier_retry(service, retry):
    calls = []

    def converse_stream(**payload):
        validate_converse(payload, "ConverseStream")
        calls.append(deepcopy(payload))
        if retry and len(calls) == 1:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ValidationException",
                        "Message": "Model does not support service tier",
                    }
                },
                "ConverseStream",
            )
        alias = payload["toolConfig"]["tools"][0]["toolSpec"]["name"]
        return {
            "stream": iter(
                [
                    {"messageStart": {"role": "assistant"}},
                    start_event(alias),
                    {
                        "contentBlockDelta": {
                            "contentBlockIndex": 0,
                            "delta": {"toolUse": {"input": "{}"}},
                        }
                    },
                    {"contentBlockStop": {"contentBlockIndex": 0}},
                    {"messageStop": {"stopReason": "tool_use"}},
                    {
                        "metadata": {
                            "usage": {
                                "inputTokens": 10,
                                "outputTokens": 5,
                                "totalTokens": 15,
                            }
                        }
                    },
                ]
            )
        }

    service.client.converse_stream.side_effect = converse_stream
    request = anthropic_request()
    payload = service.anthropic_to_bedrock.convert_request(request)
    if retry:
        payload["serviceTier"] = {"type": "reserved"}
    events = queue.Queue()
    service._stream_worker(
        payload, request, "msg_test", "reserved" if retry else "default", events
    )
    collected = list(events.queue)
    assert collected[-1] == ("done", None)
    assert not any(kind == "error" for kind, _ in collected)
    decoded = [
        json.loads(line[6:])
        for kind, event in collected
        if kind == "event"
        for line in event.splitlines()
        if line.startswith("data: ")
    ]
    starts = [event for event in decoded if event["type"] == "content_block_start"]
    assert starts == [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": LONG_NAME,
            },
        }
    ]
    assert len(calls) == (2 if retry else 1)


def test_service_sdk_validation_errors_become_400(service):
    service.client.converse.side_effect = ParamValidationError(
        report="Invalid tool schema"
    )
    with pytest.raises(ValidationError) as exc:
        service._invoke_model_sync_inner(anthropic_request())
    assert exc.value.http_status == 400
    assert exc.value.error_type == "invalid_request_error"


def test_stream_sdk_validation_errors_remain_nonretryable(service):
    service.client.converse_stream.side_effect = ParamValidationError(
        report="Invalid tool schema"
    )
    request = anthropic_request()
    payload = service.anthropic_to_bedrock.convert_request(request)
    events = queue.Queue()
    service._stream_worker(payload, request, "msg_test", "default", events)
    kind, (code, message) = events.get_nowait()
    assert kind == "error"
    event = service.bedrock_to_anthropic.create_error_event(code, message)
    assert event["error"]["type"] == "invalid_request_error"


def test_stream_incomplete_tool_call_does_not_invoke_bedrock(service):
    request = anthropic_request()
    payload = service.anthropic_to_bedrock.convert_request(request)
    payload["messages"].append(tool_response(LONG_NAME)["output"]["message"])
    events = queue.Queue()
    service._stream_worker(payload, request, "msg_test", "default", events)
    service.client.converse_stream.assert_not_called()
    kind, (code, message) = events.get_nowait()
    assert kind == "error"
    assert "missing tool results" in message
    assert (
        service.bedrock_to_anthropic.create_error_event(code, message)["error"]["type"]
        == "invalid_request_error"
    )


@pytest.mark.parametrize("streaming", [False, True])
def test_service_handles_claude_code_system_message_after_user(service, streaming):
    request = anthropic_request()
    request.messages.append(
        Message(role="system", content="IDE selected-line context.")
    )

    def invoke(**payload):
        validate_converse(payload, "ConverseStream" if streaming else "Converse")
        assert [m["role"] for m in payload["messages"]] == ["user"]
        assert payload["system"] == [{"text": "IDE selected-line context."}]
        alias = payload["toolConfig"]["tools"][0]["toolSpec"]["name"]
        if streaming:
            return {"stream": iter([start_event(alias)])}
        return tool_response(alias)

    if streaming:
        service.client.converse_stream.side_effect = invoke
        events = queue.Queue()
        service._stream_worker(
            service.anthropic_to_bedrock.convert_request(request),
            request,
            "msg_test",
            "default",
            events,
        )
        assert list(events.queue)[-1] == ("done", None)
    else:
        service.client.converse.side_effect = invoke
        assert service._invoke_model_sync_inner(request).content[0].name == LONG_NAME


@pytest.mark.parametrize("content", ["", " \n", [], [{"type": "text", "text": ""}]])
def test_native_restored_history_skips_only_empty_system_messages(service, content):
    request = MessageRequest(
        model="claude-fable-5-1",
        max_tokens=128,
        messages=[
            {"role": "user", "content": "Continue."},
            {"role": "system", "content": content},
            {"role": "system", "content": "Retained instructions."},
        ],
    )
    original = request.model_dump()
    native = service._convert_to_anthropic_native_request(request)
    assert [m["role"] for m in native["messages"]] == ["user", "system"]
    assert native["messages"][-1]["content"] == [
        {"type": "text", "text": "Retained instructions."}
    ]
    assert request.model_dump() == original


@pytest.mark.parametrize("signature", [None, ""])
def test_native_drops_unsigned_empty_thinking_but_preserves_fable_signature(
    service, signature
):
    blocks = [
        {"type": "thinking", "thinking": "", "signature": signature},
        {"type": "thinking", "thinking": "", "signature": "claude-signed-state"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "Keep the answer."},
        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}},
    ]
    request = MessageRequest(
        model="claude-fable-5-1",
        max_tokens=128,
        messages=[
            {"role": "user", "content": "Continue."},
            {"role": "assistant", "content": blocks},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "OK"}
                ],
            },
        ],
    )
    original = request.model_dump()
    native = service._convert_to_anthropic_native_request(request)
    assert native["messages"][1]["content"] == blocks[1:]
    assert native["messages"][2]["content"][0]["tool_use_id"] == "toolu_1"
    assert request.model_dump() == original


def test_native_empty_thinking_only_turn_is_removed(service):
    request = MessageRequest(
        model="claude-fable-5-1",
        max_tokens=128,
        messages=[
            {"role": "user", "content": "Continue."},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}]},
            {"role": "user", "content": "Hi."},
        ],
    )
    native = service._convert_to_anthropic_native_request(request)
    assert [m["role"] for m in native["messages"]] == ["user", "user"]


def test_gpt_discards_provider_reasoning_but_keeps_history_and_tools():
    payload = request_with_tools(("Read",))
    payload["messages"] += [
        {
            "role": "assistant",
            "content": [
                {
                    "reasoningContent": {
                        "reasoningText": {"text": "", "signature": "sig"}
                    }
                },
                {"reasoningContent": {"redactedContent": b"opaque"}},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"reasoningContent": {"reasoningText": {"text": "old state"}}},
                {"text": "Keep this answer."},
                {"toolUse": {"name": "Read", "toolUseId": "toolu_1", "input": {}}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"toolResult": {"toolUseId": "toolu_1", "content": [{"text": "OK"}]}}
            ],
        },
    ]
    original = deepcopy(payload)
    result = ConverseRequestAdapter(payload, MODEL).request
    assert result["messages"] == [
        original["messages"][0],
        {"role": "assistant", "content": original["messages"][2]["content"][1:]},
        original["messages"][3],
    ]
    validate_converse(result)
    assert payload == original
    # Other providers retain their own reasoning protocol.
    other = ConverseRequestAdapter(payload, "amazon.nova-pro-v1:0").request
    assert other["messages"] == payload["messages"]


def test_gpt_response_does_not_create_foreign_claude_thinking():
    adapter = ConverseRequestAdapter(request_with_tools(("Read",)), MODEL)
    response = tool_response("Read")
    response["output"]["message"]["content"].insert(
        0, {"reasoningContent": {"redactedContent": b"opaque"}}
    )
    original = deepcopy(response)
    restored = adapter.restore_response(response)
    assert (
        restored["output"]["message"]["content"]
        == original["output"]["message"]["content"][1:]
    )
    assert restored["usage"] == original["usage"]
    assert response == original


@pytest.mark.parametrize("explicit_starts", [False, True])
def test_gpt_stream_omits_reasoning_without_empty_blocks_or_index_gaps(
    service, explicit_starts
):
    raw = [{"messageStart": {"role": "assistant"}}]
    if explicit_starts:
        raw.append({"contentBlockStart": {"contentBlockIndex": 0, "start": {}}})
    raw += [
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"reasoningContent": {"signature": "opaque"}},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 0}},
    ]
    if explicit_starts:
        raw.append({"contentBlockStart": {"contentBlockIndex": 1, "start": {}}})
    raw += [
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"text": "OK"}}},
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30}
            }
        },
    ]
    service.client.converse_stream.return_value = {"stream": iter(raw)}
    request = anthropic_request()
    events = queue.Queue()
    service._stream_worker(
        service.anthropic_to_bedrock.convert_request(request),
        request,
        "msg_test",
        "default",
        events,
    )
    collected = list(events.queue)
    assert collected[-1] == ("done", None)
    assert not any(kind == "error" for kind, _ in collected)
    decoded = [
        json.loads(line[6:])
        for kind, event in collected
        if kind == "event"
        for line in event.splitlines()
        if line.startswith("data: ")
    ]
    starts = [x for x in decoded if x["type"] == "content_block_start"]
    assert starts == [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
    ]
    assert {x["index"] for x in decoded if "index" in x} == {0}
    assert any(x.get("delta") == {"type": "text_delta", "text": "OK"} for x in decoded)
    assert not any(
        x.get("delta", {}).get("type") in {"thinking_delta", "signature_delta"}
        for x in decoded
    )


@pytest.mark.parametrize(
    "alias,target",
    [
        ("claude-opus-5", "global.anthropic.claude-opus-5"),
        ("claude-opus-5[1m]", "global.anthropic.claude-opus-5"),
        ("claude-fable-5-1", "global.anthropic.claude-fable-5-1"),
        ("claude-fable-5-1[1m]", "global.anthropic.claude-fable-5-1"),
        ("claude-sonnet-5", "global.anthropic.claude-sonnet-5"),
    ],
)
def test_current_claude_aliases_resolve_to_bedrock_profiles(alias, target):
    converter = AnthropicToBedrockConverter()
    assert converter._convert_model_id(alias) == target


def test_gpt_stop_sequences_are_enforced_across_text_blocks():
    payload = request_with_tools(("Read",))
    payload["inferenceConfig"]["stopSequences"] = ["LATER", "<STOP>"]
    original = deepcopy(payload)
    adapter = ConverseRequestAdapter(payload, MODEL)
    assert "stopSequences" not in adapter.request["inferenceConfig"]
    response = tool_response("Read")
    response["output"]["message"]["content"] = [
        {"text": "DENY<ST"},
        {"text": "OP>ALLOW LATER"},
        *response["output"]["message"]["content"],
    ]
    restored = adapter.restore_response(response)
    assert restored["output"]["message"]["content"] == [{"text": "DENY"}]
    assert restored["stopReason"] == "stop_sequence"
    assert restored["_proxy_stop_sequence"] == "<STOP>"
    assert restored["usage"] == response["usage"]
    assert payload == original
    other = ConverseRequestAdapter(payload, "amazon.nova-pro-v1:0")
    assert other.request["inferenceConfig"]["stopSequences"] == ["LATER", "<STOP>"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("retry", [False, True])
def test_service_enforces_gpt_stops_and_reports_real_usage(service, streaming, retry):
    request = anthropic_request()
    request.stop_sequences = ["<STOP>"]
    response = tool_response("Read")
    response["output"]["message"]["content"] = [
        {"text": "DENY<STOP>ALLOW"},
        *response["output"]["message"]["content"],
    ]
    calls = []

    def converse(**payload):
        assert "stopSequences" not in payload["inferenceConfig"]
        calls.append(deepcopy(payload))
        if retry and len(calls) == 1:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ValidationException",
                        "Message": "Model does not support service tier",
                    }
                },
                "Converse",
            )
        return response

    service.client.converse.side_effect = converse
    tier = "reserved" if retry else "default"
    if streaming:
        events = queue.Queue()
        payload = service.anthropic_to_bedrock.convert_request(request)
        if retry:
            payload["serviceTier"] = {"type": tier}
        service._stream_worker(payload, request, "msg_test", tier, events)
        collected = list(events.queue)
        assert collected[-1] == ("done", None)
        decoded = [
            json.loads(line[6:])
            for kind, event in collected
            if kind == "event"
            for line in event.splitlines()
            if line.startswith("data: ")
        ]
        assert not any(kind == "error" for kind, _ in collected)
        assert "".join(e.get("delta", {}).get("text", "") for e in decoded) == "DENY"
        assert not any(
            e.get("content_block", {}).get("type") == "tool_use" for e in decoded
        )
        finish = next(e for e in decoded if e["type"] == "message_delta")
        assert finish["delta"] == {
            "stop_reason": "stop_sequence",
            "stop_sequence": "<STOP>",
        }
        assert finish["usage"]["output_tokens"] == 5
        start = next(e for e in decoded if e["type"] == "message_start")
        assert start["message"]["usage"]["input_tokens"] == 10
        service.client.converse_stream.assert_not_called()
    else:
        result = service._invoke_model_sync_inner(request, service_tier=tier)
        assert len(result.content) == 1 and result.content[0].text == "DENY"
        assert result.stop_reason == "stop_sequence"
        assert result.stop_sequence == "<STOP>"
        assert result.usage.output_tokens == 5 and result.usage.input_tokens == 10
    assert len(calls) == (2 if retry else 1)


def test_empty_gpt_stop_sequence_is_rejected():
    payload = request_with_tools()
    payload["inferenceConfig"]["stopSequences"] = [""]
    with pytest.raises(ValidationError, match="must not be empty"):
        ConverseRequestAdapter(payload, MODEL)


@pytest.mark.parametrize("status", ["success", "error"])
@pytest.mark.parametrize("with_text", [False, True])
def test_gpt_tool_images_preserve_bytes_result_ids_order_and_other_content(
    status, with_text
):
    images = [
        {"image": {"format": "png", "source": {"bytes": b"first-image"}}},
        {"image": {"format": "jpeg", "source": {"bytes": b"second-image"}}},
    ]
    direct = {"image": {"format": "png", "source": {"bytes": b"direct-image"}}}
    payload = request_with_tools(("Read",))
    payload["messages"] = [
        {
            "role": "user",
            "content": [
                {"text": "Compare the returned images."},
                {
                    "toolResult": {
                        "toolUseId": "toolu_first",
                        "status": status,
                        "content": (
                            [{"text": "Before."}, images[0], {"text": "After."}]
                            if with_text
                            else [images[0]]
                        ),
                    }
                },
                {
                    "toolResult": {
                        "toolUseId": "toolu_second",
                        "content": [images[1], {"json": {"width": 64}}],
                    }
                },
                direct,
            ],
        }
    ]
    original = deepcopy(payload)
    normalized = ConverseRequestAdapter(payload, MODEL).request
    validate_converse(normalized)
    validate_converse(normalized, "ConverseStream")
    blocks = normalized["messages"][0]["content"]
    assert blocks[0] == original["messages"][0]["content"][0]
    assert blocks[2] == images[0]
    assert blocks[4] == images[1]
    assert blocks[5] == direct
    first, second = blocks[1]["toolResult"], blocks[3]["toolResult"]
    assert first["toolUseId"] == "toolu_first"
    assert first["status"] == status
    assert second["toolUseId"] == "toolu_second"
    assert "toolu_first" in first["content"][1 if with_text else 0]["text"]
    assert "toolu_second" in second["content"][0]["text"]
    assert second["content"][1] == {"json": {"width": 64}}
    if with_text:
        assert first["content"][0] == {"text": "Before."}
        assert first["content"][-1] == {"text": "After."}
    assert payload == original
    # The provider's supported native tool-result format is kept for other models.
    assert (
        ConverseRequestAdapter(payload, "amazon.nova-pro-v1:0").request["messages"]
        == original["messages"]
    )


def test_multiple_images_from_one_tool_result_are_numbered_and_stay_together():
    payload = request_with_tools(("Read",))
    images = [
        {"image": {"format": "png", "source": {"bytes": b"one"}}},
        {"image": {"format": "png", "source": {"bytes": b"two"}}},
    ]
    payload["messages"][0]["content"] = [
        {"toolResult": {"toolUseId": "toolu_image", "content": images}}
    ]
    normalized = ConverseRequestAdapter(payload, MODEL).request
    blocks = normalized["messages"][0]["content"]
    assert blocks[1:] == images
    refs = blocks[0]["toolResult"]["content"]
    assert "Image attachment 1 " in refs[0]["text"]
    assert "Image attachment 2 " in refs[1]["text"]


@pytest.mark.parametrize("streaming", [False, True])
def test_service_read_tool_image_is_sent_as_user_image(service, streaming):
    request = anthropic_request()
    request.messages.extend(
        [
            Message(
                role="assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": "toolu_image",
                        "name": LONG_NAME,
                        "input": {},
                    }
                ],
            ),
            Message(
                role="user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_image",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "aW1hZ2U=",
                                },
                            }
                        ],
                    }
                ],
            ),
        ]
    )

    def invoke(**payload):
        validate_converse(payload, "ConverseStream" if streaming else "Converse")
        result, image = payload["messages"][-1]["content"]
        assert result["toolResult"]["toolUseId"] == "toolu_image"
        assert all("image" not in b for b in result["toolResult"]["content"])
        assert image == {"image": {"format": "png", "source": {"bytes": b"image"}}}
        if streaming:
            return {"stream": iter([{"messageStop": {"stopReason": "end_turn"}}])}
        return tool_response("Read")

    if streaming:
        service.client.converse_stream.side_effect = invoke
        events = queue.Queue()
        service._stream_worker(
            service.anthropic_to_bedrock.convert_request(request),
            request,
            "msg_test",
            "default",
            events,
        )
        assert list(events.queue)[-1] == ("done", None)
    else:
        service.client.converse.side_effect = invoke
        service._invoke_model_sync_inner(request)
