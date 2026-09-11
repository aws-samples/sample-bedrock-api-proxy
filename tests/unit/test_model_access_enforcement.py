"""Actual outbound IDs, request isolation, and fail-closed Anthropic paths."""

import asyncio
import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import messages, models
from app.core.access_policy import AccessPolicyDenied, ParsedAccessPolicy
from app.core.config import settings
from app.core.exceptions import NoProviderAvailableError
from app.keypool.failover import FailoverManager
from app.routing.engine import RoutingEngine
from app.routing.rules import RoutingRule, RuleEngine
from app.schemas.anthropic import CountTokensRequest, MessageRequest
from app.schemas.ptc import PTCExecutionState
from app.services.bedrock_provider import BedrockProvider
from app.services.bedrock_service import BedrockService
from app.services.inference_profile_resolver import InferenceProfileResolver
from app.services.model_access import (
    ModelAccessService,
    guard_model_stream,
    preflight_model,
)
from app.services.openai_compat_service import OpenAICompatService
from app.services.provider_registry import ProviderRegistry
from app.services.ptc_service import PTCService
from app.services.standalone_code_execution_service import (
    StandaloneCodeExecutionService,
)
from app.services.web_fetch_service import WebFetchService
from app.services.web_search_service import WebSearchService

A = "global.anthropic.claude-test-a"
B = "global.anthropic.claude-test-b"


def policy(*targets):
    return ParsedAccessPolicy(model_enabled=True, model_allow=frozenset(targets))


def message(model="alias", **kwargs):
    return MessageRequest(
        model=model,
        max_tokens=64,
        messages=[{"role": "user", "content": "hello"}],
        **kwargs,
    )


def native_response(content=None, stop="end_turn"):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "upstream",
        "content": content if content is not None else [{"type": "text", "text": "OK"}],
        "stop_reason": stop,
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }


def native_wire(content=None, stop="end_turn"):
    return {"body": io.BytesIO(json.dumps(native_response(content, stop)).encode())}


def native_stream():
    return {
        "body": [
            {"chunk": {"bytes": json.dumps(event).encode()}}
            for event in [
                {"type": "message_start", "message": native_response()},
                {"type": "message_stop"},
            ]
        ]
    }


def converse_wire():
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": "OK"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 2, "outputTokens": 1},
    }


def service(mapping):
    with patch("boto3.client"):
        svc = BedrockService(dynamodb_client=MagicMock())
    svc.anthropic_to_bedrock._convert_model_id = lambda model: mapping.get(model, model)
    svc.client.invoke_model.side_effect = lambda **kw: native_wire()
    svc.client.invoke_model_with_response_stream.side_effect = (
        lambda **kw: native_stream()
    )
    svc.client.converse.side_effect = lambda **kw: converse_wire()
    svc.client.converse_stream.side_effect = lambda **kw: {
        "stream": [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "OK"}}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {"inputTokens": 2, "outputTokens": 1}}},
        ]
    }
    svc.client.count_tokens.return_value = {"inputTokens": 12}
    return svc


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setattr(settings, "enable_openai_compat", False)
    monkeypatch.setattr(settings, "enable_bedrock_responses", True)
    monkeypatch.setattr(settings, "enable_tracing", False)
    monkeypatch.setattr(settings, "multi_provider_enabled", False)
    monkeypatch.setattr(settings, "openai_api_key", "test")
    monkeypatch.setattr(settings, "openai_base_url", "")


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "api,target", [("native", A), ("converse", "amazon.nova-test")]
)
async def test_boto_wire_pin_alias_and_refresh(api, target, stream):
    mapping = {"alias": target, "alias2": target}
    svc = service(mapping)
    bound = ModelAccessService(svc, policy(target))
    bound.prepare("alias")
    mapping["alias"] = B
    # A second resolver lookup, including inside the converter, would send B.
    svc.anthropic_to_bedrock._convert_model_id = MagicMock(
        side_effect=AssertionError("resolved twice")
    )
    req = message()
    if stream:
        events = [
            json.loads(x.split("data: ", 1)[1])
            async for x in bound.invoke_model_stream(req)
        ]
        start = next(x for x in events if x["type"] == "message_start")
        assert start["message"]["model"] == "alias"
        wire = (
            svc.client.invoke_model_with_response_stream
            if api == "native"
            else svc.client.converse_stream
        )
    else:
        result = await bound.invoke_model(req)
        assert result.model == "alias"
        wire = svc.client.invoke_model if api == "native" else svc.client.converse
    assert wire.call_args.kwargs["modelId"] == target
    svc.anthropic_to_bedrock._convert_model_id.assert_not_called()
    assert req.model == "alias"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("target", [A, "amazon.nova-test", "us.openai.test"])
async def test_service_denial_has_no_outbound(target, stream):
    svc = service({"alias": target})
    with pytest.raises(AccessPolicyDenied):
        if stream:
            _ = [
                x
                async for x in svc.invoke_model_stream(
                    message(), access_policy=policy(B)
                )
            ]
        else:
            await svc.invoke_model(message(), access_policy=policy(B))
    assert not svc.client.method_calls
    assert not svc._responses_services


async def test_aliases_direct_target_and_remap():
    mapping = {"alias": A, "alias2": A}
    svc = service(mapping)
    for model in ["alias", "alias2", A]:
        await ModelAccessService(svc, policy(A)).invoke_model(message(model))
    assert [c.kwargs["modelId"] for c in svc.client.invoke_model.call_args_list] == [
        A
    ] * 3
    mapping["alias"] = B
    with pytest.raises(AccessPolicyDenied):
        await ModelAccessService(svc, policy(A)).invoke_model(message())
    assert svc.client.invoke_model.call_count == 3


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("api", ["native", "converse"])
async def test_tier_retry_retains_checked_target(api, stream):
    target = A if api == "native" else "amazon.nova-test"
    mapping = {"alias": target}
    svc = service(mapping)
    name = {
        ("native", False): "invoke_model",
        ("native", True): "invoke_model_with_response_stream",
        ("converse", False): "converse",
        ("converse", True): "converse_stream",
    }[api, stream]
    wire = getattr(svc.client, name)
    success = wire.side_effect
    calls = []

    def retry(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            mapping["alias"] = B
            raise ClientError(
                {
                    "Error": {
                        "Code": "ValidationException",
                        "Message": "does not support service tier",
                    }
                },
                name,
            )
        return success(**kwargs)

    wire.side_effect = retry
    access = ModelAccessService(svc, policy(target))
    if stream:
        events = [
            x
            async for x in access.invoke_model_stream(
                message(), service_tier="priority"
            )
        ]
        assert any("message_stop" in e for e in events)
    else:
        await access.invoke_model(message(), service_tier="priority")
    assert [c["modelId"] for c in calls] == [target, target]
    assert "serviceTier" not in calls[1]


def openai_transport(wires):
    def handle(req):
        body = json.loads(req.content)
        wires.append((str(req.url), body, threading.get_ident()))
        if req.url.path.endswith("/responses"):
            response = {
                "id": "resp_test",
                "status": "completed",
                "output": [],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }
            if body["stream"]:
                events = [
                    {
                        "type": "response.created",
                        "response": {**response, "status": "in_progress"},
                    },
                    {"type": "response.completed", "response": response},
                ]
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content="".join(f"data: {json.dumps(e)}\n\n" for e in events),
                )
            return httpx.Response(200, json=response)
        response = {
            "id": "chat_test",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "OK"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
        if body["stream"]:
            response["choices"] = [
                {"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(response)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(200, json=response)

    return httpx.MockTransport(handle)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("api", ["runtime", "chat", "responses"])
async def test_openai_adapters_actual_http_wire(api, stream, monkeypatch):
    target = "us.openai.test" if api == "runtime" else "alias"
    mapping = {"alias": "us.openai.test" if api == "runtime" else "openai.mapped"}
    svc = service(mapping)
    wires = []
    with httpx.Client(transport=openai_transport(wires)) as client:
        upstream = OpenAICompatService(
            base_url="https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
            api_key="test",
            http_client=client,
        )
        if api == "runtime":
            monkeypatch.setattr(
                svc, "_responses_service_for_model", lambda *a: upstream
            )
        else:
            svc._openai_compat_service = upstream
            svc._openai_use_responses = api == "responses"
        access = ModelAccessService(svc, policy(target))
        access.prepare("alias")
        mapping["alias"] = B
        if stream:
            events = [x async for x in access.invoke_model_stream(message())]
            assert any('"model": "alias"' in e for e in events)
            assert any("message_stop" in e for e in events)
        else:
            assert (await access.invoke_model(message())).model == "alias"
        assert len(wires) == 1
        assert wires[0][1]["model"] == target
        assert wires[0][2] != threading.get_ident()
        assert wires[0][0].endswith(
            "chat/completions" if api == "chat" else "responses"
        )
        # The mapped ID is NOT what old compat sends.
        with pytest.raises(AccessPolicyDenied):
            await upstream.invoke_model(
                message(), access_policy=policy("openai.mapped")
            )
        assert len(wires) == 1


@pytest.mark.parametrize("api", ["chat", "responses"])
async def test_openai_stream_worker_denies_converted_target(api):
    wires = []
    with httpx.Client(transport=openai_transport(wires)) as client:
        svc = OpenAICompatService(
            base_url="https://example.test/openai/v1",
            api_key="test",
            http_client=client,
        )
        invoke = (
            svc.invoke_model_stream if api == "chat" else svc.invoke_responses_stream
        )
        events = [x async for x in invoke(message(), access_policy=policy(B))]
        assert len(events) == 1
        assert "permission_error" in events[0]
        assert wires == []


async def test_count_wire_estimation_and_no_denial_fallback():
    mapping = {"claude-alias": A}
    svc = service(mapping)
    req = CountTokensRequest(model="claude-alias", messages=message().messages)
    prepared = svc.prepare_count_model(req.model, policy(A))
    mapping[req.model] = B
    assert (
        await svc.count_tokens(req, access_policy=policy(A), prepared_model=prepared)
        == 12
    )
    assert svc.client.count_tokens.call_args.kwargs["modelId"] == A
    estimate = MagicMock(return_value=5)
    svc._estimate_token_count = estimate
    with pytest.raises(AccessPolicyDenied):
        await svc.count_tokens(req, access_policy=policy(A))
    estimate.assert_not_called()
    assert svc.client.count_tokens.call_count == 1
    mapping[req.model] = A
    svc._count_tokens_sync = MagicMock(
        side_effect=AccessPolicyDenied("model_not_allowed")
    )
    with pytest.raises(AccessPolicyDenied):
        await svc.count_tokens(req, access_policy=policy(A))
    estimate.assert_not_called()
    req.model = "local-estimate"
    with pytest.raises(AccessPolicyDenied):
        await svc.count_tokens(req, access_policy=policy(A))
    assert await svc.count_tokens(req, access_policy=policy("local-estimate")) == 5


async def test_concurrent_requests_explicit_executor_policy_isolation():
    svc = service({"one": A, "two": B})
    barrier = threading.Barrier(2)
    seen = []

    def invoke(**kwargs):
        barrier.wait(timeout=5)
        seen.append(kwargs["modelId"])
        return native_wire()

    svc.client.invoke_model.side_effect = invoke
    one, two = ModelAccessService(svc, policy(A)), ModelAccessService(svc, policy(B))
    await asyncio.gather(
        one.invoke_model(message("one")), two.invoke_model(message("two"))
    )
    assert set(seen) == {A, B}
    with pytest.raises(AccessPolicyDenied):
        await one.invoke_model(message("two"))
    assert len(seen) == 2


@pytest.mark.parametrize("strategy", ["cost", "quality", "auto", "rule"])
def test_routing_filters_before_selection_and_classifier(strategy):
    svc = service({"allowed": A, "forbidden": B})
    access = ModelAccessService(svc, policy(A))
    rules = RuleEngine()
    if strategy == "rule":
        rules.load_rules(
            [
                RoutingRule("1", "blocked", "keyword", "hello", "forbidden"),
                RoutingRule(
                    "2", "permitted", "keyword", "hello", "allowed", priority=1
                ),
            ]
        )
    smart = MagicMock(strong_model="forbidden", weak_model="allowed")
    pricing = MagicMock()
    pricing.list_all_pricing.return_value = {
        "items": [
            {"model_id": "forbidden", "input_price": 0},
            {"model_id": "allowed", "input_price": 1},
        ]
    }
    registry = MagicMock()
    registry.get_providers_for_model.return_value = [SimpleNamespace(name="bedrock")]
    engine = RoutingEngine(rules, smart, registry, pricing)
    result = engine.route(
        "forbidden",
        "hello",
        {"routing_strategy": strategy},
        candidate_allowed=access.allows_candidate,
    )
    assert result.model == "allowed"
    smart.classify.assert_not_called()
    assert result.provider == "bedrock"


def test_no_allowed_route_403_vs_allowed_unavailable_503():
    svc = service({"allowed": A})
    pricing = MagicMock()
    pricing.list_all_pricing.return_value = {"items": [{"model_id": "allowed"}]}
    registry = MagicMock()
    registry.get_providers_for_model.return_value = []
    engine = RoutingEngine(
        RuleEngine(), provider_registry=registry, pricing_manager=pricing
    )
    for allowed, error in [(B, AccessPolicyDenied), (A, NoProviderAvailableError)]:
        with pytest.raises(error):
            engine.route(
                "alias",
                "hello",
                {"routing_strategy": "cost"},
                candidate_allowed=ModelAccessService(
                    svc, policy(allowed)
                ).allows_candidate,
            )


@pytest.mark.parametrize("stream", [False, True])
async def test_failover_and_provider_wire_repair(stream):
    mapping = {"allowed": A, "forbidden": B}
    svc = service(mapping)
    access = ModelAccessService(svc, policy(A))
    pool = MagicMock()
    pool.get_available_key.return_value = ("key", "id")
    failover = FailoverManager(pool)
    failover.load_chains_from_dict(
        {"initial": [{"model": "forbidden"}, {"model": "allowed"}]}
    )
    chosen = failover.find_failover(
        "initial", candidate_allowed=access.allows_candidate
    )
    assert chosen[-1] == "allowed"
    pool.get_available_key.assert_called_once_with("bedrock", "allowed")
    mapping["allowed"] = B
    provider = BedrockProvider(svc)
    if stream:
        events = [
            x
            async for x in provider.invoke_stream(
                message("forbidden"), "allowed", {}, model_access=access
            )
        ]
        assert any('"model": "forbidden"' in e for e in events)
        assert (
            svc.client.invoke_model_with_response_stream.call_args.kwargs["modelId"]
            == A
        )
    else:
        result = await provider.invoke(
            message("forbidden"), "allowed", {}, model_access=access
        )
        assert result.response.model == "forbidden"
        assert svc.client.invoke_model.call_args.kwargs["modelId"] == A


def http_app(svc, admitted, *, state=None):
    app = FastAPI()
    app.include_router(messages.router, prefix="/v1")
    app.include_router(models.router, prefix="/v1")

    @app.middleware("http")
    async def admit(req, call_next):
        req.state.access_policy = admitted
        req.state.api_key_info = {}
        return await call_next(req)

    app.dependency_overrides[messages.get_bedrock_service] = lambda: svc
    app.dependency_overrides[models.get_bedrock_service] = lambda: svc
    app.dependency_overrides[messages.get_usage_tracker] = lambda: MagicMock()
    if state:
        for name, value in state.items():
            setattr(app.state, name, value)
    return app


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("kind", ["plain", "ptc", "standalone", "search", "fetch"])
def test_http_denied_before_images_tools_and_headers(kind, stream, monkeypatch):
    svc = service({"alias": B})
    app = http_app(svc, policy(A))
    data = message(stream=stream).model_dump(exclude_none=True)
    headers = {}
    if kind in ("ptc", "standalone"):
        headers["anthropic-beta"] = (
            "advanced-tool-use-2025-11-20"
            if kind == "ptc"
            else "code-execution-2025-08-25"
        )
        data["tools"] = [{"type": "code_execution_20250825", "name": "code_execution"}]
    elif kind in ("search", "fetch"):
        data["tools"] = (
            [{"type": "web_search_20250305", "name": "web_search"}]
            if kind == "search"
            else [{"type": "web_fetch_20250910", "name": "web_fetch"}]
        )
    fetch_images = AsyncMock()
    monkeypatch.setattr(messages, "resolve_image_urls", fetch_images)
    with TestClient(app) as client:
        response = client.post("/v1/messages", json=data, headers=headers)
    assert response.status_code == 403, response.text
    assert "permission_error" in response.text
    assert "text/event-stream" not in response.headers["content-type"]
    fetch_images.assert_not_called()
    assert not svc.client.method_calls


def test_http_count_and_model_discovery():
    svc = service({"alias": A, "blocked": B})
    svc.list_available_models = MagicMock(
        return_value=[{"id": "alias"}, {"id": "blocked"}]
    )
    svc.get_model_info = MagicMock(return_value={"id": A})
    app = http_app(svc, policy(A))
    with TestClient(app) as client:
        response = client.get("/v1/models")
        assert response.json()["data"] == [{"id": "alias"}]
        assert response.json()["has_more"] is False
        assert client.get("/v1/models/blocked").status_code == 403
        svc.get_model_info.assert_not_called()
        assert client.get("/v1/models/alias").status_code == 200
        svc.get_model_info.assert_called_once_with(A)
        response = client.post(
            "/v1/messages/count_tokens",
            json={"model": "blocked", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 403
    assert not svc.client.method_calls


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "tool",
    [StandaloneCodeExecutionService, WebSearchService, WebFetchService, PTCService],
)
async def test_direct_tool_preflight_before_sandbox(tool, stream):
    svc = service({"alias": B})
    access = ModelAccessService(svc, policy(A))
    tool_service = tool()
    sandbox = AsyncMock()
    tool_service._get_or_create_session = sandbox
    method = "handle_ptc_request" if tool is PTCService else "handle_request"
    if stream:
        method += "_streaming"
    with pytest.raises(AccessPolicyDenied):
        result = getattr(tool_service, method)(message(), access, "request", "default")
        if stream:
            _ = [x async for x in result]
        else:
            await result
    sandbox.assert_not_called()
    assert not svc.client.method_calls


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("saved", [False, True])
async def test_ptc_saved_model_denied_before_resume(stream, saved):
    svc = service({"old": B, "new": A})
    access = ModelAccessService(svc, policy(A))
    ptc = PTCService()
    ptc._execution_states["session"] = PTCExecutionState(
        session_id="session",
        code_execution_tool_id="code",
        original_model="old",
        original_target=B if saved else None,
        original_api="native" if saved else None,
    )
    ptc.resume_execution = AsyncMock()
    method = (
        ptc.handle_tool_result_continuation_streaming
        if stream
        else ptc.handle_tool_result_continuation
    )
    with pytest.raises(AccessPolicyDenied):
        result = method(
            "session", "result", False, message("new"), access, "request", "default"
        )
        if stream:
            _ = [x async for x in result]
        else:
            await result
    ptc.resume_execution.assert_not_called()
    assert not svc.client.method_calls


async def test_saved_target_survives_refresh_under_current_policy():
    svc = service({"old": B})
    access = ModelAccessService(svc, policy(A))
    state = PTCExecutionState(
        session_id="s",
        code_execution_tool_id="c",
        original_model="old",
        original_target=A,
        original_api="native",
    )
    preflight_model(access, "new", state)
    await access.invoke_model(message("old"))
    assert svc.client.invoke_model.call_args.kwargs["modelId"] == A
    with pytest.raises(AccessPolicyDenied):
        preflight_model(ModelAccessService(svc, policy(B)), "new", state)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("kind", ["standalone", "search", "fetch"])
@pytest.mark.parametrize("late_denial", [False, True])
async def test_tool_iterations_pin_actual_wire_after_refresh(kind, stream, late_denial):
    mapping = {"alias": A}
    svc = service(mapping)
    access = ModelAccessService(svc, policy(A))
    tool_name = {
        "standalone": "bash_code_execution",
        "search": "web_search",
        "fetch": "web_fetch",
    }[kind]
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            mapping["alias"] = B
            return native_wire(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_test",
                        "name": tool_name,
                        "input": {
                            "query": "test",
                            "url": "https://example.test",
                            "command": "echo hi",
                        },
                    }
                ],
                "tool_use",
            )
        return native_wire()

    svc.client.invoke_model.side_effect = invoke
    if kind == "standalone":
        tool_service = StandaloneCodeExecutionService()
        tool_service._get_or_create_session = AsyncMock(
            return_value=SimpleNamespace(session_id="s", expires_at=MagicMock())
        )
        execute = AsyncMock(
            return_value={
                "type": "bash_code_execution_tool_result",
                "tool_use_id": "toolu_test",
                "content": {
                    "type": "bash_code_execution_result",
                    "stdout": "hi",
                    "stderr": "",
                    "return_code": 0,
                    "content": [],
                },
            }
        )
        tool_service._execute_server_tool = execute
        tools = [{"type": "code_execution_20250825", "name": "code_execution"}]
        tool_service._get_or_create_session.return_value.expires_at.isoformat.return_value = (
            "2026-09-11"
        )
    elif kind == "search":
        tool_service = WebSearchService()
        execute = AsyncMock(return_value=[])
        tool_service._execute_search = execute
        tools = [{"type": "web_search_20250305", "name": "web_search"}]
    else:
        tool_service = WebFetchService()
        execute = AsyncMock(
            return_value={
                "url": "https://example.test",
                "title": "test",
                "content": "hi",
                "media_type": "text/plain",
                "is_pdf": False,
            }
        )
        tool_service._execute_fetch = execute
        tools = [{"type": "web_fetch_20250910", "name": "web_fetch"}]
    req = message(tools=tools)
    if late_denial:

        def change_target(*args):
            req.model = B
            return execute.return_value

        execute.side_effect = change_target
    if stream:
        events = [
            x
            async for x in tool_service.handle_request_streaming(
                req, access, "r", "default"
            )
        ]
        if late_denial:
            assert "permission_error" in events[-1]
            assert not any("message_stop" in e for e in events)
        else:
            assert any("message_stop" in e for e in events), events
    elif late_denial:
        with pytest.raises(AccessPolicyDenied):
            await tool_service.handle_request(req, access, "r", "default")
    else:
        await tool_service.handle_request(req, access, "r", "default")
    execute.assert_awaited_once()
    assert [c["modelId"] for c in calls] == ([A] if late_denial else [A, A])


@pytest.mark.parametrize("stream", [False, True])
async def test_ptc_complete_saved_continuation_wire(stream):
    from datetime import datetime

    from app.services.ptc import ExecutionResult

    svc = service({"old": B, "new": B})
    access = ModelAccessService(svc, policy(A))
    ptc = PTCService()
    state = PTCExecutionState(
        session_id="session",
        code_execution_tool_id="code",
        original_model="old",
        original_target=A,
        original_api="native",
        original_execute_code_id="toolu_code",
        original_assistant_content=[
            {
                "type": "tool_use",
                "id": "toolu_code",
                "name": "execute_code",
                "input": {"code": "print(1)"},
            }
        ],
    )
    ptc._execution_states["session"] = state
    ptc.resume_execution = AsyncMock(
        return_value=(
            ExecutionResult(success=True, stdout="1", stderr="", return_code=0),
            True,
        )
    )
    ptc._sandbox_executor = MagicMock()
    ptc._sandbox_executor.get_session.return_value = SimpleNamespace(
        session_id="session", expires_at=datetime.now()
    )
    method = (
        ptc.handle_tool_result_continuation_streaming
        if stream
        else ptc.handle_tool_result_continuation
    )
    result = method("session", "result", False, message("new"), access, "r", "default")
    if stream:
        events = [x async for x in result]
        assert any("message_stop" in e for e in events), events
    else:
        await result
    ptc.resume_execution.assert_awaited_once()
    assert svc.client.invoke_model.call_args.kwargs["modelId"] == A


async def test_late_changed_target_denies_no_forbidden_wire():
    svc = service({"alias": A, "next": B})
    access = ModelAccessService(svc, policy(A))
    await access.invoke_model(message())
    with pytest.raises(AccessPolicyDenied):
        await access.invoke_model(message("next"))
    assert [c.kwargs["modelId"] for c in svc.client.invoke_model.call_args_list] == [A]


async def test_stream_late_denial_is_error_not_success():
    svc = service({"alias": A, "next": B})
    access = ModelAccessService(svc, policy(A))

    async def loop():
        yield 'event: message_start\ndata: {"type":"message_start","message":{"model":"old"}}\n\n'
        await access.invoke_model(message("next"))
        yield 'event: message_stop\ndata: {"type":"message_stop"}\n\n'

    events = [x async for x in guard_model_stream(loop(), "client")]
    assert len(events) == 2
    assert '"model": "client"' in events[0]
    assert "permission_error" in events[1]
    assert not any("message_stop" in e for e in events)
    assert not svc.client.method_calls


@pytest.mark.parametrize("stream", [False, True])
def test_http_routing_uses_permitted_wire_before_download(stream, monkeypatch):
    monkeypatch.setattr(settings, "multi_provider_enabled", True)
    monkeypatch.setattr(settings, "routing_enabled", True)
    mapping = {"alias": B, "selected": A}
    svc = service(mapping)
    provider = BedrockProvider(svc)
    registry = MagicMock()
    registry.get_provider.return_value = provider
    rules = RuleEngine()
    rules.load_rules([RoutingRule("1", "allow", "keyword", "hello", "selected")])
    engine = RoutingEngine(rules)
    app = http_app(
        svc, policy(A), state={"provider_registry": registry, "routing_engine": engine}
    )
    app.dependency_overrides[messages.get_api_key_info] = lambda: {
        "routing_strategy": "auto"
    }

    async def download(_):
        mapping["selected"] = B

    monkeypatch.setattr(messages, "resolve_image_urls", download)
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages", json=message(stream=stream).model_dump(exclude_none=True)
        )
    assert response.status_code == 200, response.text
    wire = (
        svc.client.invoke_model_with_response_stream
        if stream
        else svc.client.invoke_model
    )
    assert wire.call_args.kwargs["modelId"] == A
    assert (
        '"model": "alias"' in response.text
        if stream
        else response.json()["model"] == "alias"
    )


async def test_count_claude_alias_old_compat_uses_count_wire_not_chat_name():
    svc = service({"claude-alias": "openai.mapped"})
    svc._openai_compat_service = MagicMock()
    req = CountTokensRequest(model="claude-alias", messages=message().messages)
    assert await svc.count_tokens(req, access_policy=policy("openai.mapped")) == 12
    assert svc.client.count_tokens.call_args.kwargs["modelId"] == "openai.mapped"
    with pytest.raises(AccessPolicyDenied):
        await svc.count_tokens(req, access_policy=policy("claude-alias"))


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("batch", [False, True])
async def test_ptc_new_execution_saves_exact_target(stream, batch):
    from datetime import datetime

    from app.services.ptc import BatchToolCallRequest, ToolCallRequest

    mapping = {"alias": A}
    svc = service(mapping)
    access = ModelAccessService(svc, policy(A))
    ptc = PTCService()
    ptc._sandbox_executor = MagicMock()
    session = SimpleNamespace(
        session_id="session",
        expires_at=datetime.now(),
        pending_tool_call=None,
        is_busy=False,
    )
    ptc._get_or_create_session = AsyncMock(return_value=session)
    ptc.is_docker_available = MagicMock(return_value=True)

    async def execute(*args):
        call = ToolCallRequest("call", "lookup", {"x": 1})
        yield BatchToolCallRequest([call]) if batch else call

    ptc._sandbox_executor.execute_code = execute

    def invoke(**kwargs):
        mapping["alias"] = B
        return native_wire(
            [
                {
                    "type": "tool_use",
                    "id": "toolu_code",
                    "name": "execute_code",
                    "input": {"code": "lookup(x=1)"},
                }
            ],
            "tool_use",
        )

    svc.client.invoke_model.side_effect = invoke
    req = message(
        tools=[
            {
                "name": "lookup",
                "input_schema": {"type": "object"},
                "allowed_callers": ["code_execution_20250825"],
            }
        ]
    )
    if stream:
        events = [
            x
            async for x in ptc.handle_ptc_request_streaming(req, access, "r", "default")
        ]
        assert any("message_stop" in e for e in events), events
    else:
        await ptc.handle_ptc_request(req, access, "r", "default")
    saved = ptc.get_pending_execution("session")
    assert saved.original_target == A
    assert saved.original_api == "native"
    assert saved.original_model == "alias"
    assert svc.client.invoke_model.call_args.kwargs["modelId"] == A


async def test_real_mapping_snapshot_replaced_between_check_and_worker(monkeypatch):
    from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter

    monkeypatch.setattr(settings, "default_model_mapping", {"alias": "amazon.nova-a"})
    svc = service({})
    svc.anthropic_to_bedrock = AnthropicToBedrockConverter(None)
    access = ModelAccessService(svc, policy("amazon.nova-a"))
    access.prepare("alias")
    monkeypatch.setattr(settings, "default_model_mapping", {"alias": "amazon.nova-b"})
    await access.invoke_model(message())
    assert svc.client.converse.call_args.kwargs["modelId"] == "amazon.nova-a"
    with pytest.raises(AccessPolicyDenied):
        await ModelAccessService(svc, policy("amazon.nova-a")).invoke_model(message())
    assert svc.client.converse.call_count == 1


@pytest.mark.parametrize("stream", [False, True])
def test_http_ptc_saved_denial_precedes_image_and_resume(stream, monkeypatch):
    monkeypatch.setattr(settings, "enable_programmatic_tool_calling", True)
    svc = service({"alias": A})
    ptc = PTCService()
    ptc._execution_states["session"] = PTCExecutionState(
        session_id="session",
        code_execution_tool_id="code",
        original_model="old",
        original_target=B,
        original_api="native",
    )
    ptc.resume_execution = AsyncMock()
    app = http_app(svc, policy(A))
    app.dependency_overrides[messages.get_ptc_service_dep] = lambda: ptc
    download = AsyncMock()
    monkeypatch.setattr(messages, "resolve_image_urls", download)
    data = message(stream=stream, container="session").model_dump(exclude_none=True)
    data["tools"] = [{"type": "code_execution_20250825", "name": "code_execution"}]
    data["messages"] = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_call", "content": "ok"}
            ],
        }
    ]
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            json=data,
            headers={"anthropic-beta": "advanced-tool-use-2025-11-20"},
        )
    assert response.status_code == 403, response.text
    assert "permission_error" in response.text
    download.assert_not_called()
    ptc.resume_execution.assert_not_called()
    assert not svc.client.method_calls


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("strategy", ["cost", "quality"])
@pytest.mark.parametrize("available", [False, True])
def test_pricing_vendor_routes_registered_adapter(
    strategy, stream, available, monkeypatch
):
    from decimal import Decimal

    from app.services.pricing_sync_service import _vendor_label

    monkeypatch.setattr(settings, "multi_provider_enabled", True)
    monkeypatch.setattr(settings, "routing_enabled", True)
    svc = service({"alias": B})
    registry = ProviderRegistry()
    if available:
        registry.register(BedrockProvider(svc))
    pricing = MagicMock()
    pricing.list_all_pricing.return_value = {
        "items": [
            {
                "model_id": target,
                "provider": _vendor_label(target),
                "input_price": Decimal(price),
                "output_price": Decimal(price),
                "status": "active",
            }
            for target, price in [(B, "0"), (A, "3")]
        ]
    }
    assert pricing.list_all_pricing.return_value["items"][1]["provider"] == "Anthropic"
    engine = RoutingEngine(
        RuleEngine(), provider_registry=registry, pricing_manager=pricing
    )
    app = http_app(
        svc, policy(A), state={"provider_registry": registry, "routing_engine": engine}
    )
    app.dependency_overrides[messages.get_api_key_info] = lambda: {
        "routing_strategy": strategy
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages", json=message(stream=stream).model_dump(exclude_none=True)
        )
    assert response.status_code == (200 if available else 503), response.text
    if available:
        wire = (
            svc.client.invoke_model_with_response_stream
            if stream
            else svc.client.invoke_model
        )
        assert wire.call_args.kwargs["modelId"] == A
        assert "permission_error" not in response.text
    else:
        assert not svc.client.method_calls
        assert "text/event-stream" not in response.headers["content-type"]


PROFILE = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/opaque"


def profile_control_plane(monkeypatch, underlying=None):
    control = MagicMock()
    if underlying:
        control.get_inference_profile.return_value = {
            "models": [{"modelArn": underlying}]
        }
    else:
        control.get_inference_profile.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "inaccessible"}},
            "GetInferenceProfile",
        )
    resolver = InferenceProfileResolver(control)
    monkeypatch.setattr(
        "app.services.bedrock_service.get_inference_profile_resolver", lambda: resolver
    )
    return control


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("compat", [False, True])
@pytest.mark.parametrize("model", ["alias", PROFILE])
def test_forbidden_profile_denied_without_control_plane(
    stream, compat, model, monkeypatch
):
    svc = service({"alias": PROFILE})
    if compat:
        svc._openai_compat_service = MagicMock()
    control = profile_control_plane(monkeypatch)
    app = http_app(svc, policy(A))
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            json=message(model, stream=stream).model_dump(exclude_none=True),
        )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["type"] == "permission_error"
    control.get_inference_profile.assert_not_called()
    assert not svc.client.method_calls
    if compat:
        assert not svc._openai_compat_service.method_calls


def test_forbidden_profile_hidden_and_candidate_skipped(monkeypatch):
    svc = service({"blocked": PROFILE, "alias": A})
    control = profile_control_plane(monkeypatch)
    svc.list_available_models = MagicMock(
        return_value=[{"id": "blocked"}, {"id": "alias"}]
    )
    with TestClient(http_app(svc, policy(A))) as client:
        response = client.get("/v1/models")
    assert response.status_code == 200, response.text
    assert response.json()["data"] == [{"id": "alias"}]
    access = ModelAccessService(svc, policy(A))
    rules = RuleEngine()
    rules.load_rules(
        [
            RoutingRule("1", "blocked", "keyword", "hello", "blocked"),
            RoutingRule("2", "permitted", "keyword", "hello", "alias", priority=1),
        ]
    )
    chosen = RoutingEngine(rules).route(
        "blocked",
        "hello",
        {"routing_strategy": "cost"},
        candidate_allowed=access.allows_candidate,
    )
    assert chosen.model == "alias"
    control.get_inference_profile.assert_not_called()
    assert not svc.client.method_calls


@pytest.mark.parametrize("stream", [False, True])
async def test_profile_legacy_original_wire_still_allowed(stream, monkeypatch):
    wires = []
    svc = service({"alias": PROFILE})
    control = profile_control_plane(monkeypatch, "amazon.nova-test")
    compat = OpenAICompatService(
        base_url="https://example.test/v1",
        api_key="test",
        http_client=httpx.Client(transport=openai_transport(wires)),
    )
    svc._openai_compat_service = compat
    access = ModelAccessService(svc, policy("alias"))
    try:
        if stream:
            _ = [x async for x in access.invoke_model_stream(message())]
        else:
            await access.invoke_model(message())
        assert [body["model"] for _, body, _ in wires] == ["alias"]
        control.get_inference_profile.assert_called_once_with(
            inferenceProfileIdentifier=PROFILE
        )
        assert not svc.client.method_calls
    finally:
        compat.client.close()


def test_profile_final_guard_still_checks_native_wire(monkeypatch):
    svc = service({"alias": PROFILE})
    svc._openai_compat_service = MagicMock()
    control = profile_control_plane(monkeypatch, A)
    with pytest.raises(AccessPolicyDenied):
        svc.prepare_model("alias", policy("alias"))
    control.get_inference_profile.assert_called_once()
    assert not svc.client.method_calls


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("kind", ["search", "fetch"])
@pytest.mark.parametrize("denial", ["none", "early", "late"])
async def test_dynamic_tools_guard_real_nested_sandbox(kind, stream, denial):
    mapping = {"alias": A}
    svc = service(mapping)
    access = ModelAccessService(svc, policy(B if denial == "early" else A))
    tool_service = WebSearchService() if kind == "search" else WebFetchService()
    standalone = StandaloneCodeExecutionService()
    tool_service._standalone_service = standalone
    session = SimpleNamespace(session_id="dynamic-session")
    standalone._get_or_create_session = AsyncMock(return_value=session)
    standalone._sandbox_executor = MagicMock()
    sandbox = standalone.sandbox_executor
    sandbox.close_session = AsyncMock()
    req = message(tools=[{"type": f"web_{kind}_20260209", "name": f"web_{kind}"}])
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs)
        mapping["alias"] = B
        if len(calls) <= 2:
            return native_wire(
                [
                    {
                        "type": "tool_use",
                        "id": f"toolu_bash_{len(calls)}",
                        "name": "bash_code_execution",
                        "input": {"command": "echo hi"},
                    }
                ],
                "tool_use",
            )
        return native_wire()

    async def execute(*args, **kwargs):
        if denial == "late" and sandbox.execute_bash.await_count == 2:
            req.model = B
        return SimpleNamespace(stdout="hi", stderr="", return_code=0)

    svc.client.invoke_model.side_effect = invoke
    sandbox.execute_bash = AsyncMock(side_effect=execute)
    method = (
        tool_service.handle_request_streaming if stream else tool_service.handle_request
    )
    if denial == "early" or (denial == "late" and not stream):
        with pytest.raises(AccessPolicyDenied):
            result = method(req, access, "r", "default")
            if stream:
                _ = [x async for x in result]
            else:
                await result
    elif stream:
        events = [x async for x in method(req, access, "r", "default")]
        if denial == "late":
            assert "permission_error" in events[-1]
            assert any("message_start" in x for x in events)
            assert not any("message_stop" in x or "message_delta" in x for x in events)
        else:
            assert any("message_stop" in x for x in events)
            assert not any("event: error" in x for x in events)
    else:
        await method(req, access, "r", "default")

    if denial == "early":
        standalone._get_or_create_session.assert_not_called()
        sandbox.execute_bash.assert_not_called()
        sandbox.close_session.assert_not_called()
        assert not svc.client.method_calls
    else:
        standalone._get_or_create_session.assert_awaited_once_with(None)
        assert sandbox.execute_bash.await_count == 2
        assert all(
            c.args == (session, "echo hi") for c in sandbox.execute_bash.call_args_list
        )
        sandbox.close_session.assert_awaited_once_with("dynamic-session")
        assert [c["modelId"] for c in calls] == [A] * (2 if denial == "late" else 3)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("complete_first", [False, True])
@pytest.mark.parametrize("api", ["native", "converse"])
async def test_ptc_recursive_replacement_preserves_target_and_current_policy(
    stream, batch, complete_first, api
):
    from datetime import datetime

    from app.services.ptc import BatchToolCallRequest, ExecutionResult, ToolCallRequest

    svc = service({"old": B, "new": B})
    ptc = PTCService()
    session = SimpleNamespace(
        session_id="session",
        expires_at=datetime.now(),
        pending_tool_call=None,
        is_busy=False,
    )
    ptc._sandbox_executor = MagicMock()
    ptc._sandbox_executor.get_session.return_value = session
    initial = PTCExecutionState(
        session_id="session",
        code_execution_tool_id="code",
        code="lookup()",
        original_model="old",
        original_target=A,
        original_api=api,
        original_execute_code_id="toolu_code",
    )
    ptc._execution_states["session"] = initial
    completed = ExecutionResult(success=True, stdout="1", stderr="", return_code=0)
    resumed = []

    async def initial_execution():
        result = yield ToolCallRequest("initial", "lookup", {})
        resumed.append(result)
        yield completed

    initial_gen = initial_execution()
    await initial_gen.__anext__()
    ptc._execution_generators["session"] = initial_gen
    execute_count = 0
    generators = [initial_gen]

    async def execute(code, sandbox_session):
        nonlocal execute_count
        assert sandbox_session is session
        execute_count += 1
        if complete_first and execute_count == 1:
            yield completed
        else:
            call = ToolCallRequest("recursive", "lookup", {})
            result = yield BatchToolCallRequest([call]) if batch else call
            resumed.append(result)
            yield completed

    def sandbox_execute(*args):
        gen = execute(*args)
        generators.append(gen)
        return gen

    ptc._sandbox_executor.execute_code.side_effect = sandbox_execute
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs)
        if len(calls) <= (2 if complete_first else 1):
            tool = {
                "type": "tool_use",
                "id": f"toolu_code_{len(calls)}",
                "name": "execute_code",
                "input": {"code": "lookup()"},
            }
            if api == "native":
                return native_wire([tool], "tool_use")
            return {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": tool["id"],
                                    "name": tool["name"],
                                    "input": tool["input"],
                                }
                            }
                        ],
                    }
                },
                "stopReason": "tool_use",
                "usage": {"inputTokens": 2, "outputTokens": 1},
            }
        return native_wire() if api == "native" else converse_wire()

    wire = svc.client.invoke_model if api == "native" else svc.client.converse
    wire.side_effect = invoke
    req = message(
        "new",
        tools=[
            {
                "name": "lookup",
                "input_schema": {"type": "object"},
                "allowed_callers": ["code_execution_20250825"],
            }
        ],
    )
    method = (
        ptc.handle_tool_result_continuation_streaming
        if stream
        else ptc.handle_tool_result_continuation
    )
    ptc.resume_execution = AsyncMock(wraps=ptc.resume_execution)
    try:
        first = method(
            "session",
            "first",
            False,
            req,
            ModelAccessService(svc, policy(A)),
            "r",
            "default",
        )
        if stream:
            events = [x async for x in first]
            assert any("message_stop" in x for x in events), events
            assert not any("event: error" in x for x in events), events
        else:
            await first
        replacement = ptc.get_pending_execution("session")
        assert replacement is not None and replacement is not initial
        assert (
            replacement.original_model,
            replacement.original_target,
            replacement.original_api,
        ) == ("old", A, api)
        assert bool(replacement.pending_batch_call_ids) == batch
        assert execute_count == (2 if complete_first else 1)
        assert resumed == ["first"]

        # A new admitted policy must deny the saved target before generator.asend,
        # even though the current request name resolves to its allowed B.
        ptc.resume_execution.reset_mock()
        denied = method(
            "session",
            "forbidden",
            False,
            req,
            ModelAccessService(svc, policy(B)),
            "r",
            "default",
        )
        if stream:
            events = [x async for x in guard_model_stream(denied, req.model)]
            assert len(events) == 1 and "permission_error" in events[0]
            assert not any("message_stop" in x for x in events)
        else:
            with pytest.raises(AccessPolicyDenied):
                await denied
        ptc.resume_execution.assert_not_called()
        assert resumed == ["first"]
        assert len(calls) == (2 if complete_first else 1)
        assert ptc.get_pending_execution("session") is replacement

        final = method(
            "session",
            "second",
            False,
            req,
            ModelAccessService(svc, policy(A)),
            "r",
            "default",
        )
        if stream:
            events = [x async for x in final]
            assert any("message_stop" in x for x in events), events
            assert not any("event: error" in x for x in events), events
        else:
            await final
        assert resumed == ["first", "second"]
        assert [c["modelId"] for c in calls] == [A] * (3 if complete_first else 2)
        other = svc.client.converse if api == "native" else svc.client.invoke_model
        other.assert_not_called()
    finally:
        for gen in generators:
            await gen.aclose()


async def test_recursive_ptc_late_denial_has_no_success_sse():
    from datetime import datetime

    from app.services.ptc import ExecutionResult

    svc = service({"old": B})
    ptc = PTCService()
    state = PTCExecutionState(
        session_id="session",
        code_execution_tool_id="code",
        original_model="old",
        original_target=A,
        original_api="native",
    )
    ptc._execution_states["session"] = state
    ptc._sandbox_executor = MagicMock()
    ptc._sandbox_executor.get_session.return_value = SimpleNamespace(
        session_id="session", expires_at=datetime.now()
    )
    completed = ExecutionResult(success=True, stdout="1", stderr="", return_code=0)
    ptc.resume_execution = AsyncMock(return_value=(completed, True))
    svc.client.invoke_model.side_effect = lambda **kw: native_wire(
        [
            {
                "type": "tool_use",
                "id": "toolu_recursive",
                "name": "execute_code",
                "input": {"code": "print(1)"},
            }
        ],
        "tool_use",
    )

    async def execute(*args):
        # Simulate a newly selected target during recursion, not a revocation of
        # the admitted snapshot. The next outbound guard must reject it.
        state.original_model = B
        yield completed

    ptc._sandbox_executor.execute_code = execute
    events = [
        x
        async for x in ptc.handle_tool_result_continuation_streaming(
            "session",
            "result",
            False,
            message(),
            ModelAccessService(svc, policy(A)),
            "r",
            "default",
        )
    ]
    assert any("message_start" in x for x in events)
    assert "permission_error" in events[-1]
    assert not any("message_stop" in x or "message_delta" in x for x in events)
    ptc.resume_execution.assert_awaited_once()
    assert [c.kwargs["modelId"] for c in svc.client.invoke_model.call_args_list] == [A]
