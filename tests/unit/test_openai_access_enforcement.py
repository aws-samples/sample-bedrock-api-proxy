"""Wire-level passthrough policy and metadata proofs (moto + respx, no AWS calls)."""

import asyncio
import importlib
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.openai_passthrough.backend_target import resolve_verified_target
from app.api.openai_passthrough.chat_responses_adapter import (
    reset_unsupported_param_cache_for_testing,
)
from app.api.openai_passthrough.context_store import ShardedResponseContextStore
from app.api.openai_passthrough.response_access import (
    ResponseAccessError,
    ResponseAuthorizationStore,
    response_id_from_payload,
)
from app.core.access_policy import policy_from_key_info
from app.core.config import settings
from app.db.dynamodb import DynamoDBClient
from app.middleware.auth import AuthMiddleware

routes = importlib.import_module("app.api.openai_passthrough.router")
client_module = importlib.import_module("app.api.openai_passthrough.client")
A = "openai.allowed"
B = "openai.forbidden"
BASE = "https://mantle.test/openai/v1"


def key_info(key="owner", allow=(A,)):
    result = {"api_key": key, "user_id": "test", "is_master": False}
    if allow is not None:
        result["access_policy"] = {
            "version": 1,
            "ip": {"enabled": False, "allow": []},
            "model": {"enabled": True, "allow": list(allow)},
        }
    return result


def response(response_id="resp_1", model=A):
    return {
        "id": response_id,
        "object": "response",
        "model": model,
        "status": "completed",
        "output": [],
        "usage": {"input_tokens": 9, "output_tokens": 3},
    }


def sse(*events, named=False):
    return "".join(
        (f"event: {event['type']}\n" if named else "")
        + "data: "
        + json.dumps(event)
        + "\n\n"
        for event in events
    ).encode()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    monkeypatch.setattr(settings, "openai_base_url", BASE)
    monkeypatch.setattr(settings, "openai_api_key", "upstream-test")
    monkeypatch.setattr(settings, "enable_bedrock_responses", True)
    monkeypatch.setattr(settings, "default_model_mapping", {})
    monkeypatch.setattr(settings, "require_api_key", True)
    monkeypatch.setattr(settings, "master_api_key", "master")
    monkeypatch.setattr(settings, "client_ip_trusted_proxy_hops", 0)
    monkeypatch.setattr(settings, "client_ip_trusted_proxy_cidrs", "")
    monkeypatch.setattr(settings, "enable_web_search", True)
    reset_unsupported_param_cache_for_testing()
    with mock_aws():
        db = DynamoDBClient()
        db._create_response_context_table()
        table = db.dynamodb.Table(db.response_context_table_name)
        store = ShardedResponseContextStore(table)
        auth_store = ResponseAuthorizationStore(table)
        mapping = MagicMock()
        mapping.get_mapping.side_effect = lambda model: {"alias": A, "alias2": A}.get(
            model
        )
        usage = MagicMock()
        monkeypatch.setattr(routes, "_managers", lambda: (mapping, usage, store))
        keys = {k: key_info(k) for k in ("owner", "foreign")}
        keys["legacy"] = key_info("legacy", None)
        keys["both"] = key_info("both", (A, B))
        manager = MagicMock()
        manager.validate_api_key.side_effect = lambda key: keys.get(key)
        monkeypatch.setattr("app.middleware.auth.APIKeyManager", lambda *_: manager)
        app = FastAPI()
        app.include_router(routes.router, prefix="/openai/v1")
        app.add_middleware(AuthMiddleware, dynamodb_client=db)
        with httpx.Client() as _unused:
            upstream = httpx.AsyncClient()
            monkeypatch.setattr(client_module, "_client", upstream)
            with respx.mock(assert_all_called=False) as wire:
                with TestClient(app) as client:
                    yield SimpleNamespace(
                        client=client,
                        wire=wire,
                        table=table,
                        store=store,
                        auth_store=auth_store,
                        mapping=mapping,
                        usage=usage,
                        keys=keys,
                        manager=manager,
                    )
            asyncio.run(upstream.aclose())


def post(
    env, surface="responses", *, key="owner", model="alias", stream=False, **extra
):
    body = {"model": model, "stream": stream, **extra}
    body.setdefault("input" if surface == "responses" else "messages", [])
    return env.client.post(
        f"/openai/v1/{surface}",
        json=body,
        headers={"x-api-key": key},
    )


def save(
    env, response_id="resp_1", *, key="owner", model=A, kind="upstream", backend=None
):
    target = resolve_verified_target(env.keys[key], model)
    env.auth_store.register(
        response_id,
        api_key=key,
        model=model,
        backend=backend or target.identity,
        kind=kind,
    )
    return target


def operate(env, operation="get", *, response_id="resp_1", key="owner", **kwargs):
    method = {"get": "GET", "delete": "DELETE", "cancel": "POST", "input_items": "GET"}[
        operation
    ]
    suffix = "" if operation in {"get", "delete"} else f"/{operation}"
    return env.client.request(
        method,
        f"/openai/v1/responses/{response_id}{suffix}",
        headers={"x-api-key": key},
        **kwargs,
    )


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "model", [B, "us." + A, "arn:aws:bedrock:us-east-1:123:inference-profile/x"]
)
def test_forbidden_never_connects(env, surface, stream, model):
    forbidden = env.wire.post(BASE + "/responses").respond(200, json=response())
    result = post(env, surface, model=model, stream=stream)
    assert result.status_code == 403
    assert result.json()["error"]["type"] == "permission_error"
    assert not forbidden.called
    assert not env.usage.record_usage_nowait.called


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model", ["alias", "alias2", A])
def test_authorized_wire_is_pinned_and_registered(
    env, surface, stream, model, monkeypatch
):
    def upstream(request):
        assert json.loads(request.content)["model"] == A
        # Simulate refresh after authorization; no subsequent mapping read.
        env.mapping.get_mapping.side_effect = lambda _: B
        monkeypatch.setattr(settings, "default_model_mapping", {A: B})
        if stream:
            return httpx.Response(
                200,
                content=sse(
                    {"type": "response.created", "response": {"id": "resp_1"}},
                    {"type": "response.completed", "response": response()},
                ),
            )
        return httpx.Response(200, json=response())

    call = env.wire.post(BASE + "/responses").mock(side_effect=upstream)
    result = post(env, surface, stream=stream, model=model)
    assert result.status_code == 200
    assert call.call_count == 1
    item = env.table.get_item(Key=env.auth_store.key("resp_1"))["Item"]
    assert item["model"] == A
    assert item["backend"]["endpoint"] == BASE
    assert set(item) == {
        "response_id",
        "chunk_id",
        "owner",
        "model",
        "backend",
        "kind",
        "created_at",
        "expires_at",
        "deleted",
        "version",
    }
    assert "owner" != item["owner"] and "upstream-test" not in str(item)
    assert env.usage.record_usage_nowait.call_count == 1
    assert env.table.item_count == 1  # no ordinary content or CHUNK/META rows


@pytest.mark.parametrize("stream", [False, True])
def test_parameter_retry_retains_exact_checked_model(env, stream):
    bodies = []

    def upstream(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            env.mapping.get_mapping.side_effect = lambda _: B
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "unsupported_parameter",
                        "param": "temperature",
                    }
                },
            )
        if stream:
            return httpx.Response(
                200, content=sse({"type": "response.completed", "response": response()})
            )
        return httpx.Response(200, json=response())

    env.wire.post(BASE + "/responses").mock(side_effect=upstream)
    result = post(env, "chat/completions", stream=stream, temperature=0.2)
    assert result.status_code == 200
    assert [body["model"] for body in bodies] == [A, A]
    assert "temperature" not in bodies[-1]


@pytest.mark.parametrize("stream", [False, True])
def test_model_removal_retry_cannot_call_without_authorized_model(env, stream):
    call = env.wire.post(BASE + "/responses").respond(
        400,
        json={
            "error": {"code": "unsupported_parameter", "param": "model"},
        },
    )
    result = post(env, "chat/completions", stream=stream)
    assert result.status_code == 403
    assert call.call_count == 1


@pytest.mark.parametrize("operation", ["get", "delete", "cancel", "input_items"])
@pytest.mark.parametrize(
    "state",
    [
        "missing",
        "legacy",
        "expired",
        "ttl_deleted",
        "foreign",
        "forbidden",
        "backend_changed",
        "deleted",
    ],
)
def test_stateful_denials_never_probe_upstream(env, operation, state):
    if state not in {"missing", "legacy"}:
        save(
            env,
            key="foreign" if state == "foreign" else "owner",
            model=B if state == "forbidden" else A,
        )
    if state == "legacy":
        env.table.put_item(
            Item={
                "response_id": "resp_1",
                "chunk_id": "META",
                "expires_at": int(time.time()) + 100,
            }
        )
    if state == "expired":
        env.table.update_item(
            Key=env.auth_store.key("resp_1"),
            UpdateExpression="SET expires_at = :t",
            ExpressionAttributeValues={":t": int(time.time()) - 1},
        )
    if state == "ttl_deleted":
        env.table.delete_item(Key=env.auth_store.key("resp_1"))
    if state == "deleted":
        env.auth_store.mark_deleted(env.auth_store._read("resp_1"))
    if state == "backend_changed":
        env.table.update_item(
            Key=env.auth_store.key("resp_1"),
            UpdateExpression="SET backend = :b",
            ExpressionAttributeValues={":b": {"endpoint": "https://attacker.test"}},
        )
    result = operate(env, operation)
    assert result.status_code == (403 if state == "forbidden" else 404)
    assert not env.wire.calls


@pytest.mark.parametrize("operation", ["get", "delete", "cancel", "input_items"])
def test_owned_crud_uses_verified_backend_and_tombstone(env, operation):
    save(env)
    suffix = "" if operation in {"get", "delete"} else f"/{operation}"
    call = env.wire.route(url=BASE + "/responses/resp_1" + suffix).respond(
        200, json={"ok": True}
    )
    result = operate(env, operation, json={"model": B})
    assert result.status_code == 200
    assert call.call_count == 1
    if operation == "delete":
        assert operate(env, operation).status_code == 404
        assert call.call_count == 1
        assert env.auth_store._read("resp_1")["deleted"] is True


@pytest.mark.parametrize("key", ["legacy", "master"])
@pytest.mark.parametrize("state", ["missing", "expired", "foreign", "deleted"])
def test_unrestricted_metadata_never_changes_crud(env, key, state):
    if state != "missing":
        save(env, key="foreign")
    if state == "expired":
        env.table.update_item(
            Key=env.auth_store.key("resp_1"),
            UpdateExpression="SET expires_at = :t",
            ExpressionAttributeValues={":t": 1},
        )
    if state == "deleted":
        env.auth_store.mark_deleted(env.auth_store._read("resp_1"))
    call = env.wire.get(BASE + "/responses/resp_1").respond(200, json={"prior": True})
    assert operate(env, key=key).status_code == 200
    assert call.called


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize(
    "state",
    [
        "missing",
        "foreign",
        "historical_forbidden",
        "new_forbidden",
        "allowed",
        "new_allowed",
    ],
)
def test_continuation_checks_both_models_before_call(env, surface, state):
    key = "both" if state == "new_allowed" else "owner"
    if state != "missing":
        save(
            env,
            key="foreign" if state == "foreign" else key,
            model=B if state == "historical_forbidden" else A,
        )
    call = env.wire.post(BASE + "/responses").respond(200, json=response("resp_2"))
    result = post(
        env,
        surface,
        key=key,
        model=B if state in {"new_forbidden", "new_allowed"} else A,
        previous_response_id="resp_1",
    )
    expected = (
        200
        if state in {"allowed", "new_allowed"}
        else 403 if "forbidden" in state else 404
    )
    assert result.status_code == expected
    assert call.called == (expected == 200)


@pytest.mark.parametrize("operation", ["get", "delete", "cancel", "input_items"])
def test_proxy_ids_not_sent_to_upstream_crud(env, operation):
    save(env, kind="proxy")
    assert operate(env, operation).status_code == 400
    assert not env.wire.calls


def test_metadata_lookup_failure_is_503(env, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("do not disclose")

    monkeypatch.setattr(env.table, "get_item", fail)
    assert operate(env).status_code == 503
    assert not env.wire.calls


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("key", ["owner", "legacy"])
def test_registration_outage_blocks_only_restricted_ids_and_records_usage(
    env, monkeypatch, surface, stream, key
):
    def fail(*args, **kwargs):
        raise RuntimeError("secret storage details")

    monkeypatch.setattr(env.table, "put_item", fail)
    data = response()
    call = env.wire.post(BASE + "/responses").respond(
        200,
        **(
            {"content": sse({"type": "response.completed", "response": data})}
            if stream
            else {"json": data}
        ),
    )
    result = post(env, surface, stream=stream, key=key)
    assert call.called
    assert env.usage.record_usage_nowait.call_count == 1
    assert "secret storage details" not in result.text
    if key == "owner":
        assert result.status_code == (200 if stream else 503)
        assert "resp_1" not in result.text
        assert "api_error" in result.text
        assert "[DONE]" not in result.text
    else:
        assert result.status_code == 200
        assert "api_error" not in result.text


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize(
    "event_type",
    [
        "response.created",
        "response.completed",
        "response.in_progress",
        "response.failed",
        "response.incomplete",
    ],
)
def test_first_id_event_registered_even_without_completion(env, named, event_type):
    env.wire.post(BASE + "/responses").respond(
        200,
        content=sse(
            {"type": event_type, "response": {"id": "resp_1"}},
            named=named,
        ),
    )
    result = post(env, stream=True)
    assert result.status_code == 200
    assert "resp_1" in result.text
    assert env.auth_store._read("resp_1")["model"] == A


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"type": "response.output_item.added", "item": {"id": "tool_id"}}, None),
        ({"type": "response.output_item.added", "id": "tool_id"}, None),
        ({"type": "response.created", "response": {"id": "resp_1"}}, "resp_1"),
        ({"type": "response.completed", "id": "resp_1"}, "resp_1"),
        (
            {
                "type": "response.output_text.delta",
                "response_id": "resp_1",
                "item_id": "tool_id",
            },
            "resp_1",
        ),
        ({"object": "response", "id": "resp_1"}, "resp_1"),
        ({"id": "tool_id", "type": "function_call"}, None),
    ],
)
def test_id_parser_never_captures_tool_ids(payload, expected):
    assert response_id_from_payload(payload) == expected


def test_conditional_registration_no_owner_backend_overwrite_or_ttl_refresh(env):
    target = save(env)
    first = env.auth_store._read("resp_1")
    save(env)
    assert env.auth_store._read("resp_1") == first
    for kwargs in (
        {"api_key": "foreign"},
        {"backend": {"endpoint": "https://wrong"}},
        {"model": B},
    ):
        args = {
            "api_key": "owner",
            "model": A,
            "backend": target.identity,
            "kind": "upstream",
        }
        args.update(kwargs)
        with pytest.raises(ResponseAccessError):
            env.auth_store.register("resp_1", **args)
        assert env.auth_store._read("resp_1") == first


def test_list_filter_paginates_only_visible_aliases(env):
    env.wire.get(BASE + "/models").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": B}],
                    "has_more": True,
                    "first_id": B,
                    "last_id": B,
                    "count": 1,
                },
            ),
            httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "alias"}, {"id": "alias2"}],
                    "has_more": False,
                    "last_id": "alias2",
                },
            ),
        ]
    )
    result = env.client.get("/openai/v1/models?limit=1", headers={"x-api-key": "owner"})
    assert result.json() == {
        "object": "list",
        "data": [{"id": "alias"}],
        "has_more": True,
        "first_id": "alias",
        "last_id": "alias",
        "count": 1,
    }
    assert env.wire.calls[1].request.url.params["after"] == B


@pytest.mark.parametrize(
    "change",
    ["endpoint", "region", "revision", "inactive", "credentials", "association"],
)
def test_provider_changes_do_not_rebind_ids(env, monkeypatch, change):
    manager = MagicMock()
    provider = {
        "provider_id": "p1",
        "endpoint_url": "https://provider.test/openai/v1",
        "aws_region": "us-east-1",
        "auth_type": "bearer_token",
        "is_active": True,
        "updated_at": "v1",
        "encrypted_credentials": "encrypted",
    }
    manager.table.get_item.side_effect = lambda **_: {"Item": dict(provider)}
    manager._decrypt_credentials.return_value = {"bearer_token": "provider-token"}
    monkeypatch.setattr(routes, "_provider_manager", lambda: manager)
    env.keys["owner"]["provider_id"] = "p1"
    target = resolve_verified_target(env.keys["owner"], A, manager)
    env.auth_store.register(
        "resp_1", api_key="owner", model=A, backend=target.identity, kind="upstream"
    )
    if change == "endpoint":
        provider["endpoint_url"] = "https://different.test/openai/v1"
    elif change == "region":
        provider["aws_region"] = "us-west-2"
    elif change in {"revision", "credentials"}:
        provider["updated_at"] = "v2"
        manager._decrypt_credentials.return_value = {"bearer_token": "fresh-token"}
    elif change == "inactive":
        provider["is_active"] = False
    else:
        env.keys["owner"]["provider_id"] = "p2"
    assert operate(env).status_code == 404
    assert not env.wire.calls
    assert manager.table.get_item.call_args.kwargs["ConsistentRead"] is True


@pytest.mark.parametrize("stream", [False, True])
def test_runtime_create_and_crud_use_same_verified_url(env, monkeypatch, stream):
    runtime_model = "us.vendor.allowed"
    env.keys["owner"]["access_policy"]["model"]["allow"] = [runtime_model]
    monkeypatch.setattr(
        settings,
        "openai_base_url",
        "https://bedrock-mantle.us-east-1.api.aws/openai/v1",
    )
    runtime = "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1"

    def upstream(request):
        assert json.loads(request.content)["model"] == runtime_model
        # Change routing configuration after preparation, not authorization target.
        monkeypatch.setattr(settings, "enable_bedrock_responses", False)
        return httpx.Response(
            200,
            **(
                {
                    "content": sse(
                        {
                            "type": "response.completed",
                            "response": response(model=runtime_model),
                        }
                    )
                }
                if stream
                else {"json": response(model=runtime_model)}
            ),
        )

    call = env.wire.post(runtime + "/responses").mock(side_effect=upstream)
    assert post(env, model=runtime_model, stream=stream).status_code == 200
    assert call.called
    monkeypatch.setattr(settings, "enable_bedrock_responses", True)
    get = env.wire.get(runtime + "/responses/resp_1").respond(200, json={"ok": True})
    assert operate(env).status_code == 200
    assert get.called


@pytest.mark.parametrize("named", [False, True])
def test_id_registration_precedes_any_frame_delivery_without_stream_buffer(env, named):
    from app.api.openai_passthrough.response_access import ResponseRegistration
    from app.api.openai_passthrough.streaming import stream_passthrough_response

    policy = policy_from_key_info(env.keys["owner"])
    target = resolve_verified_target(env.keys["owner"], A)
    registration = ResponseRegistration(
        env.auth_store, policy, "owner", A, target.identity
    )

    class Incremental(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse(
                {"type": "response.created", "response": {"id": "resp_1"}}, named=named
            )
            # Generator must NOT prefetch the rest of the response for registration.
            assert env.auth_store._read("resp_1")
            yield sse({"type": "response.output_text.delta", "delta": "next"})
            raise httpx.ReadError("interrupted")

    async def run():
        resp = httpx.Response(200, stream=Incremental())
        result = []
        async for chunk in stream_passthrough_response(
            resp, "responses", lambda _: None, registration
        ):
            assert env.auth_store._read("resp_1")
            result.append(chunk)
        assert resp.is_closed
        return b"".join(result)

    output = asyncio.run(run())
    assert b"resp_1" in output and b"next" in output


def test_multiline_id_cannot_escape_registration_failure(env, monkeypatch):
    monkeypatch.setattr(env.table, "put_item", MagicMock(side_effect=RuntimeError()))
    env.wire.post(BASE + "/responses").respond(
        200,
        content=(
            b'event: response.created\ndata: {"type":"response.created",\n'
            b'data: "response":{"id":"resp_secret"}}\n\n'
        ),
    )
    result = post(env, stream=True)
    assert "resp_secret" not in result.text
    assert "response.created" not in result.text
    assert "api_error" in result.text


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model", [A, "us.vendor.allowed"])
def test_search_iterations_use_pinned_adapter_model_and_proxy_continuation(
    env, monkeypatch, stream, model
):
    env.keys["owner"]["access_policy"]["model"]["allow"] = [model]
    env.mapping.get_mapping.side_effect = lambda name: model if name == "alias" else B
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        assert requests[-1]["model"] == model
        return httpx.Response(200, json=response(f"up_{len(requests)}", model))

    env.wire.post(BASE + "/responses").mock(side_effect=upstream)

    async def search(*, request, bedrock_service, **kwargs):
        from app.services.model_access import ModelAccessService

        assert isinstance(bedrock_service, ModelAccessService)
        first = await bedrock_service.invoke_model(request)
        env.mapping.get_mapping.side_effect = lambda _: B
        await bedrock_service.invoke_model(request)
        return first

    service = MagicMock()
    service.handle_request.side_effect = search
    monkeypatch.setattr(routes, "get_web_search_service", lambda: service)
    # Bedrock's DDB constructor is not part of the wire test; the actual adapter
    # and OpenAI SDK HTTP client still run (respx intercepts sync + async).
    original = routes.BedrockService
    monkeypatch.setattr(
        routes,
        "BedrockService",
        lambda **kw: original(dynamodb_client=MagicMock(), **kw),
    )
    result = post(
        env, model="alias", stream=stream, input="hello", tools=[{"type": "web_search"}]
    )
    assert result.status_code == 200
    assert len(requests) == 2
    rows = env.table.scan()["Items"]
    meta = next(row for row in rows if row["chunk_id"] == "AUTH#v1")
    assert meta["kind"] == "proxy" and meta["model"] == model
    saved = env.store.load(meta["response_id"], api_key="owner")
    assert saved
    # Restore client alias resolution, keep direct target remap forbidden.
    env.mapping.get_mapping.side_effect = lambda name: model if name == "alias" else B
    result = post(
        env,
        model="alias",
        input="next",
        previous_response_id=meta["response_id"],
        tools=[{"type": "web_search"}],
    )
    assert result.status_code == 200
    assert len(requests) == 4
    assert operate(env, response_id=meta["response_id"]).status_code == 400
    assert all(req["model"] == model for req in requests)


@pytest.mark.parametrize("stream", [False, True])
def test_forbidden_search_does_not_create_tool_service(env, monkeypatch, stream):
    service = MagicMock()
    monkeypatch.setattr(routes, "get_web_search_service", service)
    assert (
        post(
            env, model=B, stream=stream, input="hello", tools=[{"type": "web_search"}]
        ).status_code
        == 403
    )
    assert not service.called and not env.wire.calls


@pytest.mark.parametrize("stream", [False, True])
def test_native_search_checks_exact_model_and_pins_native_client(
    env, monkeypatch, stream
):
    import io

    from app.services.bedrock_service import BedrockService

    model = "global.anthropic.claude-test"
    env.keys["owner"]["access_policy"]["model"]["allow"] = [model]
    env.mapping.get_mapping.side_effect = lambda _: model
    native = MagicMock()
    native.invoke_model.return_value = {
        "body": io.BytesIO(
            json.dumps(
                {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                }
            ).encode()
        )
    }
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: native)
    monkeypatch.setattr(
        routes,
        "BedrockService",
        lambda **kw: BedrockService(dynamodb_client=MagicMock(), **kw),
    )

    async def search(*, request, bedrock_service, **kwargs):
        assert bedrock_service.prepare(model).target == model
        return await bedrock_service.invoke_model(request)

    service = MagicMock()
    service.handle_request.side_effect = search
    monkeypatch.setattr(routes, "get_web_search_service", lambda: service)
    result = post(
        env, model=model, stream=stream, input="hello", tools=[{"type": "web_search"}]
    )
    assert result.status_code == 200
    assert native.invoke_model.call_args.kwargs["modelId"] == model
    item = next(
        item for item in env.table.scan()["Items"] if item["chunk_id"] == "AUTH#v1"
    )
    assert item["backend"]["api"] == "native"
    assert (
        item["backend"]["endpoint"] == "https://bedrock-runtime.us-east-1.amazonaws.com"
    )
    assert not env.wire.calls


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
def test_owner_collision_prevents_id_delivery_but_preserves_usage(env, surface, stream):
    save(env, key="foreign")
    first = env.auth_store._read("resp_1")
    env.wire.post(BASE + "/responses").respond(
        200,
        **(
            {"content": sse({"type": "response.completed", "response": response()})}
            if stream
            else {"json": response()}
        ),
    )
    result = post(env, surface, stream=stream)
    assert result.status_code == (200 if stream else 503)
    assert "resp_1" not in result.text
    assert env.auth_store._read("resp_1") == first
    assert env.usage.record_usage_nowait.called


def test_named_sse_without_type_still_registers(env):
    env.wire.post(BASE + "/responses").respond(
        200, content=b'event: response.created\ndata: {"response":{"id":"resp_1"}}\n\n'
    )
    assert post(env, stream=True).status_code == 200
    assert env.auth_store._read("resp_1")


def test_direct_call_fallback_never_treats_failed_auth_as_permission(env):
    from starlette.requests import Request

    from app.core.access_policy import AccessPolicyDenied

    request = Request({"type": "http"})
    assert routes._policy(request, env.keys["owner"]).model_enabled
    with pytest.raises(AccessPolicyDenied):
        routes._policy(request, None)


def test_unrestricted_stream_remains_byte_compatible(env, monkeypatch):
    frames = sse({"type": "response.created", "response": {"id": "resp_1"}}, named=True)
    monkeypatch.setattr(env.table, "put_item", MagicMock(side_effect=RuntimeError()))
    env.wire.post(BASE + "/responses").respond(200, content=frames)
    assert post(env, key="legacy", stream=True).content == frames


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize(
    "named,multiline", [(True, False), (True, True), (False, True)]
)
def test_restricted_sse_delivers_only_the_registered_id(env, surface, named, multiline):
    payload = {"response": response()}
    if not named:
        payload["type"] = "response.completed"
    raw = json.dumps(payload, indent=2) if multiline else json.dumps(payload)
    frame = (
        ("event: response.completed\n" if named else "")
        + "".join("data: " + line + "\n" for line in raw.splitlines())
        + "\n"
    )
    env.wire.post(BASE + "/responses").respond(200, content=frame.encode())
    result = post(env, surface, stream=True)
    assert "resp_1" in result.text
    assert "chatcmpl-" not in result.text
    assert env.auth_store._read("resp_1")["model"] == A
    assert env.usage.record_usage_nowait.call_count == 1


@pytest.mark.parametrize("multiline", [False, True])
def test_unrestricted_named_frames_get_metadata_without_byte_changes(env, multiline):
    raw = json.dumps({"response": {"id": "resp_1"}}, indent=2 if multiline else None)
    frame = (
        "event: response.created\n"
        + "".join("data: " + line + "\n" for line in raw.splitlines())
        + "\n"
    )
    env.wire.post(BASE + "/responses").respond(200, content=frame.encode())
    assert post(env, key="legacy", stream=True).content == frame.encode()
    assert env.auth_store._read("resp_1")["model"] == A


@pytest.mark.parametrize("stream", [False, True])
def test_legacy_search_records_actual_model_and_can_be_restricted_later(
    env, monkeypatch, stream
):
    from app.services.bedrock_service import BedrockService

    monkeypatch.setattr(settings, "enable_openai_compat", True)
    env.mapping.get_mapping.side_effect = lambda name: A if name == "alias" else None
    monkeypatch.setattr(
        routes,
        "BedrockService",
        lambda **kw: BedrockService(dynamodb_client=MagicMock(), **kw),
    )
    calls = []

    def upstream(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response(f"up_{len(calls)}"))

    env.wire.post(BASE + "/responses").mock(side_effect=upstream)

    async def search(*, request, bedrock_service, **kwargs):
        return await bedrock_service.invoke_model(request)

    service = MagicMock()
    service.handle_request.side_effect = search
    monkeypatch.setattr(routes, "get_web_search_service", lambda: service)
    result = post(
        env, key="legacy", stream=stream, input="first", tools=[{"type": "web_search"}]
    )
    assert result.status_code == 200
    meta = next(
        row for row in env.table.scan()["Items"] if row["chunk_id"] == "AUTH#v1"
    )
    assert meta["kind"] == "proxy" and meta["model"] == A
    # Stateful checks use the current admitted snapshot, not the creating policy.
    restricted = policy_from_key_info(key_info("legacy"))

    async def run():
        return await routes._authorize_response(
            meta["response_id"], restricted, env.keys["legacy"]
        )

    assert asyncio.run(run())[0]["model"] == A
    assert calls[0]["model"] == A


@pytest.mark.parametrize("stream", [False, True])
def test_nested_search_new_forbidden_model_never_calls_upstream(
    env, monkeypatch, stream
):
    from app.services.bedrock_service import BedrockService

    original = BedrockService
    monkeypatch.setattr(
        routes,
        "BedrockService",
        lambda **kw: original(dynamodb_client=MagicMock(), **kw),
    )
    call = env.wire.post(BASE + "/responses").respond(200, json=response())

    async def search(*, request, bedrock_service, **kwargs):
        await bedrock_service.invoke_model(request)
        await bedrock_service.invoke_model(request.model_copy(update={"model": B}))

    service = MagicMock()
    service.handle_request.side_effect = search
    monkeypatch.setattr(routes, "get_web_search_service", lambda: service)
    assert (
        post(
            env, stream=stream, input="first", tools=[{"type": "web_search"}]
        ).status_code
        == 403
    )
    assert call.call_count == 1
    assert json.loads(call.calls[0].request.content)["model"] == A
    env.usage.record_usage_nowait.assert_called_once()
    assert env.usage.record_usage_nowait.call_args.kwargs["input_tokens"] == 9
    assert env.usage.record_usage_nowait.call_args.kwargs["output_tokens"] == 3


@pytest.mark.parametrize("stream", [False, True])
def test_search_metadata_outage_preserves_billed_usage(env, monkeypatch, stream):
    from app.schemas.anthropic import MessageResponse, TextContent, Usage

    service = MagicMock()
    service.handle_request.return_value = MessageResponse(
        id="msg_test",
        type="message",
        role="assistant",
        model=A,
        content=[TextContent(type="text", text="ok")],
        stop_reason="end_turn",
        usage=Usage(input_tokens=9, output_tokens=3),
    )
    # MagicMock async function for both route modes.
    from unittest.mock import AsyncMock

    service.handle_request = AsyncMock(return_value=service.handle_request.return_value)
    monkeypatch.setattr(routes, "get_web_search_service", lambda: service)
    monkeypatch.setattr(
        env.table, "put_item", MagicMock(side_effect=RuntimeError("private"))
    )
    result = post(env, stream=stream, input="hello", tools=[{"type": "web_search"}])
    assert result.status_code == 503
    assert "private" not in result.text
    assert env.usage.record_usage_nowait.call_count == 1


def test_list_totals_and_empty_filtered_page_are_accurate(env):
    data = {
        "object": "list",
        "data": [{"id": "alias"}, {"id": B}, {"id": "alias2"}],
        "has_more": False,
        "first_id": "alias",
        "last_id": "alias2",
        "count": 3,
        "total": 3,
    }
    env.wire.get(BASE + "/models").respond(200, json=data)
    result = env.client.get(
        "/openai/v1/models?after=alias2&limit=1", headers={"x-api-key": "owner"}
    )
    assert result.json() == {
        "object": "list",
        "data": [],
        "has_more": False,
        "first_id": None,
        "last_id": None,
        "count": 0,
        "total": 2,
    }


def test_default_native_credentials_are_pinned_without_storing_them(env, monkeypatch):
    monkeypatch.setattr(settings, "aws_access_key_id", "configured-access")
    monkeypatch.setattr(settings, "aws_secret_access_key", "configured-secret")
    monkeypatch.setattr(settings, "aws_session_token", "configured-session")
    target = resolve_verified_target(env.keys["owner"], "anthropic.claude", native=True)
    assert target.extensions["bedrock_credentials"].access_key == "configured-access"
    assert "configured-secret" not in str(target.identity)
    calls = []
    monkeypatch.setattr(
        "boto3.client", lambda *args, **kw: calls.append(kw) or MagicMock()
    )
    routes._restricted_search_service(
        MagicMock(), policy_from_key_info(env.keys["owner"]), A, target, None
    )
    assert calls[0]["aws_access_key_id"] == "configured-access"
    assert calls[0]["aws_session_token"] == "configured-session"


def test_native_provider_endpoint_is_not_rewritten_as_responses(env):
    manager = MagicMock()
    manager.table.get_item.return_value = {
        "Item": {
            "endpoint_url": "https://bedrock-runtime.us-west-2.amazonaws.com",
            "aws_region": "us-west-2",
            "updated_at": "v1",
            "auth_type": "ak_sk",
            "encrypted_credentials": "encrypted",
        }
    }
    manager._decrypt_credentials.return_value = {
        "access_key_id": "ak",
        "secret_access_key": "sk",
    }
    target = resolve_verified_target(
        {"provider_id": "p1"}, "anthropic.claude", manager, native=True
    )
    assert target.base_url == "https://bedrock-runtime.us-west-2.amazonaws.com"
    assert "sk" not in target.identity.values()


@pytest.mark.parametrize(
    "state", ["expired", "missing", "changed_backend", "lookup_error"]
)
@pytest.mark.parametrize("stream", [False, True])
def test_proxy_continuation_denied_before_tool_or_context_load(
    env, monkeypatch, state, stream
):
    save(env, kind="proxy")
    if state == "expired":
        env.table.update_item(
            Key=env.auth_store.key("resp_1"),
            UpdateExpression="SET expires_at = :t",
            ExpressionAttributeValues={":t": 1},
        )
    elif state == "missing":
        env.table.delete_item(Key=env.auth_store.key("resp_1"))
    elif state == "changed_backend":
        monkeypatch.setattr(settings, "openai_base_url", "https://new.test/openai/v1")
    else:
        monkeypatch.setattr(
            env.table, "get_item", MagicMock(side_effect=RuntimeError())
        )
    load = MagicMock()
    service = MagicMock()
    monkeypatch.setattr(env.store, "load", load)
    monkeypatch.setattr(routes, "get_web_search_service", service)
    result = post(
        env,
        stream=stream,
        input="next",
        previous_response_id="resp_1",
        tools=[{"type": "web_search"}],
    )
    assert result.status_code == (503 if state == "lookup_error" else 404)
    assert not load.called and not service.called and not env.wire.calls


def test_continuation_cannot_move_id_to_another_backend(env):
    other = "openai.gpt-oss-120b"
    env.keys["both"]["access_policy"]["model"]["allow"] = [A, other]
    save(env, key="both")
    assert (
        post(env, key="both", model=other, previous_response_id="resp_1").status_code
        == 404
    )
    assert not env.wire.calls


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
def test_provider_sigv4_wire_uses_verified_snapshot(env, monkeypatch, surface, stream):
    from app.api.openai_passthrough.client import _sign_runtime_request

    model = "us.vendor.allowed"
    env.keys["owner"]["provider_id"] = "p1"
    env.keys["owner"]["access_policy"]["model"]["allow"] = [model]
    manager = MagicMock()
    manager.table.get_item.return_value = {
        "Item": {
            "aws_region": "us-west-2",
            "updated_at": "v1",
            "auth_type": "ak_sk",
            "encrypted_credentials": "encrypted",
        }
    }
    manager._decrypt_credentials.return_value = {
        "access_key_id": "PROVIDERACCESS",
        "secret_access_key": "private-secret",
    }
    monkeypatch.setattr(routes, "_provider_manager", lambda: manager)
    upstream = client_module.get_client()
    upstream.event_hooks["request"] = [_sign_runtime_request]
    base = "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1"

    def wire(request):
        assert "Credential=PROVIDERACCESS/" in request.headers["authorization"]
        assert "us-west-2/bedrock" in request.headers["authorization"]
        return httpx.Response(
            200,
            **(
                {
                    "content": sse(
                        {
                            "type": "response.completed",
                            "response": response(model=model),
                        }
                    )
                }
                if stream
                else {"json": response(model=model)}
            ),
        )

    env.wire.post(base + "/responses").mock(side_effect=wire)
    assert post(env, surface, model=model, stream=stream).status_code == 200
    env.wire.get(base + "/responses/resp_1").mock(side_effect=wire)
    assert operate(env).status_code == 200
    item = env.auth_store._read("resp_1")
    assert "PROVIDERACCESS" not in str(item) and "private-secret" not in str(item)


def test_request_snapshot_not_reparsed_after_admission(env, monkeypatch):
    original = routes.resolve_model_id

    def remap(*args):
        # key_info is mutable, the admitted ParsedAccessPolicy is not.
        env.keys["owner"]["access_policy"]["model"]["allow"] = [B]
        return original(*args)

    monkeypatch.setattr(routes, "resolve_model_id", remap)
    env.wire.post(BASE + "/responses").respond(200, json=response())
    assert post(env).status_code == 200


@pytest.mark.parametrize(
    "field,value", [("kind", []), ("expires_at", "invalid"), ("backend", None)]
)
def test_corrupt_metadata_fails_closed_without_probe(env, field, value):
    save(env)
    env.table.update_item(
        Key=env.auth_store.key("resp_1"),
        UpdateExpression="SET #f = :v",
        ExpressionAttributeNames={"#f": field},
        ExpressionAttributeValues={":v": value},
    )
    assert operate(env).status_code == 404
    assert not env.wire.calls


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
def test_first_id_write_outage_drains_usage_without_delivering_success(
    env, monkeypatch, surface
):
    monkeypatch.setattr(env.table, "put_item", MagicMock(side_effect=RuntimeError()))
    env.wire.post(BASE + "/responses").respond(
        200,
        content=sse(
            {"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.output_text.delta", "delta": "not delivered"},
            {"type": "response.completed", "response": response()},
        ),
    )
    result = post(env, surface, stream=True)
    assert "api_error" in result.text
    assert "resp_1" not in result.text and "not delivered" not in result.text
    assert "[DONE]" not in result.text and "response.completed" not in result.text
    assert env.usage.record_usage_nowait.call_count == 1
    assert env.usage.record_usage_nowait.call_args.kwargs["input_tokens"] == 9


def test_restricted_malformed_multi_json_frame_cannot_leak_id(env):
    env.wire.post(BASE + "/responses").respond(
        200,
        content=(
            b'data: {"type":"response.created","response":{"id":"resp_secret"}}\n'
            b'data: {"type":"response.completed","response":{"id":"resp_secret"}}\n\n'
        ),
    )
    result = post(env, stream=True)
    assert "resp_secret" not in result.text and "api_error" in result.text


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@bedrock-mantle.us-east-1.api.aws/openai/v1",
        "https://bedrock-mantle.us-east-1.api.aws/openai/v1?token=secret",
    ],
)
def test_configured_url_credentials_never_enter_metadata(env, monkeypatch, url):
    monkeypatch.setattr(settings, "openai_base_url", url)
    with pytest.raises(ResponseAccessError):
        resolve_verified_target(env.keys["owner"], "us.vendor.allowed")


@pytest.mark.parametrize(
    "api,model",
    [
        ("native", "anthropic.claude-test"),
        ("native", "global.anthropic.claude-test"),
        ("converse", A),
    ],
)
@pytest.mark.parametrize("associated", [False, True])
@pytest.mark.parametrize("stream", [False, True])
def test_legacy_boto_search_attribution_matches_actual_wire(
    env, monkeypatch, api, model, associated, stream
):
    import io

    from app.services.bedrock_service import BedrockService

    monkeypatch.setattr(settings, "enable_openai_compat", False)
    monkeypatch.setattr(settings, "openai_api_key", "")
    # Runtime URL populates provider_context even for the legacy Converse path.
    monkeypatch.setattr(
        settings,
        "openai_base_url",
        "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
    )
    if associated:
        env.keys["legacy"]["provider_id"] = "p1"
        manager = MagicMock()
        manager.get_provider.return_value = {
            "aws_region": "us-west-2",
            "auth_type": "ak_sk",
        }
        manager.get_decrypted_credentials.return_value = {
            "access_key_id": "P1ACCESS",
            "secret_access_key": "p1-secret",
        }
        monkeypatch.setattr(routes, "_provider_manager", lambda: manager)
    provider_client = MagicMock()
    get_provider_client = MagicMock(return_value=provider_client)
    monkeypatch.setattr(BedrockService, "_create_provider_client", get_provider_client)
    env.mapping.get_mapping.side_effect = lambda name: None
    wire = MagicMock()
    wire.meta.endpoint_url = "https://bedrock-runtime.us-east-1.amazonaws.com"
    native_data = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }
    wire.invoke_model.return_value = {
        "body": io.BytesIO(json.dumps(native_data).encode())
    }
    wire.converse.return_value = {
        "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 2, "outputTokens": 1},
    }
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: wire)
    db = MagicMock()
    monkeypatch.setattr(
        routes, "BedrockService", lambda **kw: BedrockService(dynamodb_client=db, **kw)
    )

    async def search(*, request, bedrock_service, **kwargs):
        return await bedrock_service.invoke_model(request)

    service = MagicMock()
    service.handle_request.side_effect = search
    monkeypatch.setattr(routes, "get_web_search_service", lambda: service)
    result = post(
        env,
        key="legacy",
        model=model,
        input="hello",
        stream=stream,
        tools=[{"type": "web_search"}],
    )
    assert result.status_code == 200
    meta = next(
        row for row in env.table.scan()["Items"] if row["chunk_id"] == "AUTH#v1"
    )
    assert meta["backend"]["api"] == api and meta["model"] == model
    assert meta["backend"]["provider_id"] == ""
    assert meta["backend"]["endpoint"] == wire.meta.endpoint_url
    sent = wire.invoke_model if api == "native" else wire.converse
    assert sent.call_args.kwargs["modelId"] == model
    get_provider_client.assert_not_called()
    provider_client.invoke_model.assert_not_called()
    provider_client.converse.assert_not_called()


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize("fail_write", [False, True])
@pytest.mark.parametrize(
    "fields,payload_type,event_type",
    [
        ("event: ignored\nevent: response.created\n", None, "response.created"),
        ("event: ignored\nevent: response.created\n", "ignored", "response.created"),
        ("event: response.created\nevent: ignored\n", "response.created", "ignored"),
        ("event: ignored\nevent:response.created\n", None, "response.created"),
    ],
)
def test_sse_last_event_and_payload_interpretations_are_gated(
    env, monkeypatch, surface, fail_write, fields, payload_type, event_type
):
    from openai._streaming import SSEDecoder

    payload = {"response": {"id": "unregistered"}}
    if payload_type is not None:
        payload["type"] = payload_type
    frame = (fields + "data: " + json.dumps(payload) + "\n\n").encode()
    decoded = list(SSEDecoder().iter_bytes(iter([frame])))
    assert len(decoded) == 1 and decoded[0].event == event_type
    assert decoded[0].json() == payload
    if fail_write:
        monkeypatch.setattr(
            env.table, "put_item", MagicMock(side_effect=RuntimeError())
        )
    env.wire.post(BASE + "/responses").respond(200, content=frame)
    result = post(env, surface, stream=True)
    if fail_write:
        assert "unregistered" not in result.text
        assert "response.created" not in result.text
        assert "[DONE]" not in result.text
        assert "api_error" in result.text
    else:
        assert env.auth_store._read("unregistered")["model"] == A
        if surface == "responses":
            assert result.content == frame


@pytest.mark.parametrize("reset", ["event", "event:", "event: "])
def test_sse_empty_last_event_resets_earlier_name(env, reset):
    from openai._streaming import SSEDecoder

    frame = (
        "event: response.created\n"
        + reset
        + '\ndata: {"response":{"id":"not-a-response"}}\n\n'
    ).encode()
    assert list(SSEDecoder().iter_bytes(iter([frame])))[0].event is None
    env.wire.post(BASE + "/responses").respond(200, content=frame)
    assert post(env, stream=True).content == frame
    assert not env.auth_store._read("not-a-response")


@pytest.mark.parametrize(
    "outcome", ["complete", "read_error", "disconnect", "http_error"]
)
@pytest.mark.parametrize("auth_type", ["bearer_token", "ak_sk"])
def test_restricted_response_retrieval_is_incremental_and_not_billed(
    env, monkeypatch, outcome, auth_type
):
    from starlette.requests import Request

    from app.api.openai_passthrough.client import _sign_runtime_request

    model = "us.vendor.allowed"
    key = env.keys["owner"]
    key["provider_id"] = "p1"
    key["access_policy"]["model"]["allow"] = [model]
    manager = MagicMock()
    manager.table.get_item.return_value = {
        "Item": {
            "aws_region": "us-west-2",
            "auth_type": auth_type,
            "updated_at": "v1",
            "encrypted_credentials": "encrypted",
        }
    }
    manager._decrypt_credentials.return_value = {
        "access_key_id": "PROVIDERACCESS",
        "secret_access_key": "secret",
        "bearer_token": "p1-token",
    }
    monkeypatch.setattr(routes, "_provider_manager", lambda: manager)
    target = resolve_verified_target(key, model, manager)
    env.auth_store.register(
        "resp_1", api_key="owner", model=model, backend=target.identity, kind="upstream"
    )
    frames = sse({"type": "response.created", "response": {"id": "resp_1"}})
    terminal = sse({"type": "response.completed", "response": response(model=model)})
    state = {"first_received": False, "closed": 0, "completed": False}

    class Incremental(httpx.AsyncByteStream):
        async def __aiter__(self):
            if outcome == "http_error":
                yield b'{"error":{"type":"upstream_error","message":"failed"}}'
                return
            yield frames
            assert state["first_received"], "retrieval buffered before first delivery"
            if outcome == "read_error":
                raise httpx.ReadError("private upstream failure")
            yield terminal
            state["completed"] = True

        async def aclose(self):
            state["closed"] += 1

    def wire(request):
        assert str(request.url).split("?")[0] == target.base_url + "/responses/resp_1"
        assert request.url.params.multi_items() == [
            ("stream", "true"),
            ("starting_after", "7"),
            ("include", "a"),
            ("include", "b"),
        ]
        if auth_type == "bearer_token":
            assert request.headers["authorization"] == "Bearer p1-token"
        else:
            assert "Credential=PROVIDERACCESS/" in request.headers["authorization"]
            assert "us-west-2/bedrock" in request.headers["authorization"]
        return httpx.Response(
            400 if outcome == "http_error" else 200,
            headers={"content-type": "text/event-stream"},
            stream=Incremental(),
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(wire),
            event_hooks={"request": [_sign_runtime_request]},
        ) as client:
            monkeypatch.setattr(client_module, "_client", client)
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "headers": [],
                    "query_string": b"stream=true&starting_after=7&include=a&include=b",
                }
            )
            result = await routes._passthrough_request(
                request, "/responses/resp_1", key, "resp_1"
            )
            if outcome == "http_error":
                assert result.status_code == 400
                return result.body
            assert result.status_code == 200
            assert not state["completed"]
            iterator = result.body_iterator
            first = await anext(iterator)
            assert first == frames
            state["first_received"] = True
            if outcome == "disconnect":
                await iterator.aclose()
                return first
            return first + b"".join([chunk async for chunk in iterator])

    output = asyncio.run(run())
    assert state["closed"] == 1
    assert state["completed"] == (outcome == "complete")
    env.usage.record_usage_nowait.assert_not_called()
    if outcome == "read_error":
        assert b"upstream_error" in output
        assert b"response.completed" not in output and b"[DONE]" not in output
        assert b"private upstream failure" not in output


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("outcome", ["complete", "late_denial", "metadata_failure"])
def test_real_search_loop_preserves_observed_usage_once(
    env, monkeypatch, stream, outcome
):
    from unittest.mock import AsyncMock

    from app.services.bedrock_service import BedrockService
    from app.services.web_search_service import WebSearchService

    original_build = routes.build_message_request
    admitted = []

    def build(*args, **kwargs):
        request = original_build(*args, **kwargs)
        admitted.append(request)
        return request

    monkeypatch.setattr(routes, "build_message_request", build)
    monkeypatch.setattr(
        routes,
        "BedrockService",
        lambda **kw: BedrockService(dynamodb_client=MagicMock(), **kw),
    )
    calls = []

    def upstream(request):
        calls.append(json.loads(request.content))
        assert calls[-1]["model"] == A
        data = response(f"up_{len(calls)}")
        if len(calls) < 3:
            data["output"] = [
                {
                    "id": f"fc_{len(calls)}",
                    "type": "function_call",
                    "call_id": f"call_{len(calls)}",
                    "name": "web_search",
                    "arguments": '{"query":"test"}',
                    "status": "completed",
                }
            ]
        return httpx.Response(200, json=data)

    env.wire.post(BASE + "/responses").mock(side_effect=upstream)
    service = WebSearchService()

    async def execute(*args):
        if len(calls) == 2 and outcome == "late_denial":
            admitted[0].model = B
        return []

    service._execute_search = AsyncMock(side_effect=execute)
    monkeypatch.setattr(routes, "get_web_search_service", lambda: service)
    if outcome == "metadata_failure":
        monkeypatch.setattr(
            env.table, "put_item", MagicMock(side_effect=RuntimeError())
        )
    result = post(env, stream=stream, input="hello", tools=[{"type": "web_search"}])
    assert (
        result.status_code
        == {"complete": 200, "late_denial": 403, "metadata_failure": 503}[outcome]
    )
    iterations = 2 if outcome == "late_denial" else 3
    assert len(calls) == iterations
    assert service._execute_search.await_count == 2
    env.usage.record_usage_nowait.assert_called_once()
    recorded = env.usage.record_usage_nowait.call_args.kwargs
    assert recorded["input_tokens"] == 9 * iterations
    assert recorded["output_tokens"] == 3 * iterations
    if outcome != "complete":
        assert "response.completed" not in result.text and "[DONE]" not in result.text
        assert "response.created" not in result.text
        assert (
            "permission_error" in result.text
            if outcome == "late_denial"
            else "api_error" in result.text
        )
    elif stream:
        assert "response.completed" in result.text
    else:
        assert result.json()["usage"]["input_tokens"] == 27


@pytest.mark.parametrize(
    "state", ["missing", "foreign", "forbidden", "backend_changed"]
)
def test_streaming_retrieval_denies_before_opening_upstream(env, state):
    if state != "missing":
        save(
            env,
            key="foreign" if state == "foreign" else "owner",
            model=B if state == "forbidden" else A,
            backend=(
                {"endpoint": "https://changed.test"}
                if state == "backend_changed"
                else None
            ),
        )
    result = operate(env, params={"stream": "true"})
    assert result.status_code == (403 if state == "forbidden" else 404)
    assert not env.wire.calls
    env.usage.record_usage_nowait.assert_not_called()


def test_http_streaming_retrieval_does_not_rebill_creation(env):
    env.wire.post(BASE + "/responses").respond(200, json=response())
    assert post(env).status_code == 200
    frame = sse({"type": "response.completed", "response": response()}, named=True)
    call = env.wire.get(BASE + "/responses/resp_1", params={"stream": "true"}).respond(
        200, content=frame, headers={"content-type": "text/event-stream"}
    )
    result = operate(env, params={"stream": "true"})
    assert result.status_code == 200 and result.content == frame
    assert call.called
    env.usage.record_usage_nowait.assert_called_once()


@pytest.mark.parametrize("key", ["legacy", "master"])
def test_unrestricted_stream_query_keeps_historical_retrieval(env, monkeypatch, key):
    authorize = MagicMock(side_effect=AssertionError("legacy must not consult AUTH"))
    monkeypatch.setattr(routes, "_authorization_store", authorize)
    frame = sse({"type": "response.completed", "response": response()})
    call = env.wire.get(BASE + "/responses/resp_1").respond(
        200, content=frame, headers={"content-type": "text/event-stream"}
    )
    result = operate(env, key=key, params={"stream": "true"})
    assert result.content == frame
    assert not call.calls[0].request.url.query  # Historical unrestricted behavior.
    authorize.assert_not_called()
    env.usage.record_usage_nowait.assert_not_called()
