"""Mocked HTTP coverage of the Runtime passthrough boundary."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from app.core.config import settings
from app.schemas.anthropic import MessageResponse, Usage

MANTLE = "https://bedrock-mantle.us-west-2.api.aws/openai/v1"
RUNTIME = "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1"
MODEL = "global.openai.gpt-5.6"
AUTH = {"Authorization": "Bearer sk-test"}
USAGE = {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}
RESPONSE = {
    "id": "resp-runtime",
    "object": "response",
    "model": MODEL,
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hello"}],
        }
    ],
    "usage": USAGE,
}
SSE = (
    "event: response.output_text.delta\n"
    'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
    "event: response.completed\n"
    "data: " + json.dumps({"type": "response.completed", "response": RESPONSE}) + "\n\n"
)


@pytest.fixture
def runtime_client(client, monkeypatch):
    monkeypatch.setattr(settings, "openai_base_url", MANTLE)
    monkeypatch.setattr(settings, "aws_access_key_id", "test-access")
    monkeypatch.setattr(settings, "aws_secret_access_key", "test-secret")
    monkeypatch.setattr(settings, "aws_session_token", "test-token")
    return client


def _reply(stream):
    if stream:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=SSE
        )
    return httpx.Response(200, json=RESPONSE)


def _native_request(model=MODEL):
    return {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "previous", "annotations": []}
                ],
            },
            {"type": "reasoning", "id": "rs-1", "encrypted_content": "opaque"},
            {
                "type": "custom_tool_call",
                "call_id": "c-1",
                "name": "edit",
                "input": "patch",
            },
            {"type": "custom_tool_call_output", "call_id": "c-1", "output": "done"},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "inspect"},
                    {"type": "input_image", "image_url": "https://images.test/pic.png"},
                ],
            },
        ],
        "tools": [
            {"type": "custom", "name": "edit", "format": {"type": "text"}},
            {
                "type": "namespace",
                "name": "files",
                "tools": [{"type": "function", "name": "read", "parameters": {}}],
            },
        ],
        "tool_choice": "required",
        "reasoning": {"effort": "ultra", "summary": "detailed"},
        "include": ["reasoning.encrypted_content"],
        "previous_response_id": "resp-previous",
        "store": True,
        "background": True,
        "truncation": "auto",
        "text": {"format": {"type": "json_object"}},
    }


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mapping_source", ["dynamodb", "defaults"])
def test_responses_preserves_native_fields_after_mapping(
    runtime_client,
    mock_model_mapping_manager,
    mock_usage_tracker,
    mock_response_context_store,
    mock_web_search_service,
    monkeypatch,
    stream,
    mapping_source,
):
    if mapping_source == "dynamodb":
        mock_model_mapping_manager.get_mapping.return_value = MODEL
        monkeypatch.setattr(
            settings, "default_model_mapping", {"alias": "wrong-default"}
        )
    else:
        monkeypatch.setattr(settings, "default_model_mapping", {"alias": MODEL})
    body = _native_request("alias")
    body["stream"] = stream
    with respx.mock() as upstream:
        route = upstream.post(RUNTIME + "/responses").mock(return_value=_reply(stream))
        response = runtime_client.post("/openai/v1/responses", headers=AUTH, json=body)

    assert response.status_code == 200
    sent = route.calls[0].request
    assert json.loads(sent.content) == {**body, "model": MODEL}
    assert sent.headers["authorization"] == "Bearer bedrock-key-test"
    if stream:
        assert response.text == SSE
        assert response.text.count("event: response.completed\n") == 1
    else:
        assert response.json() == RESPONSE
    mock_web_search_service.get_service_mock.assert_not_called()
    mock_response_context_store.load.assert_not_called()
    mock_response_context_store.save.assert_not_called()
    usage = mock_usage_tracker.record_usage_nowait.call_args.kwargs
    assert usage["model"] == MODEL
    assert usage["input_tokens"] == 8
    assert usage["output_tokens"] == 3


@pytest.mark.parametrize("stream", [False, True])
def test_chat_translates_to_runtime_responses(
    runtime_client, mock_model_mapping_manager, mock_usage_tracker, stream
):
    mock_model_mapping_manager.get_mapping.return_value = MODEL
    with respx.mock() as upstream:
        route = upstream.post(RUNTIME + "/responses").mock(return_value=_reply(stream))
        response = runtime_client.post(
            "/openai/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "alias",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": stream,
            },
        )
    assert response.status_code == 200
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == MODEL
    assert sent["input"] == [{"role": "user", "content": "hello"}]
    assert "messages" not in sent
    if stream:
        assert "event:" not in response.text
        chunks = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        assert any(
            choice["delta"].get("content") == "hello"
            for chunk in chunks
            for choice in chunk.get("choices", [])
        )
        assert "data: [DONE]" in response.text
    else:
        assert response.json()["choices"][0]["message"]["content"] == "hello"
    assert mock_usage_tracker.record_usage_nowait.call_args.kwargs["model"] == MODEL


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
def test_runtime_uses_sigv4_without_bearer(
    runtime_client, monkeypatch, surface, stream
):
    monkeypatch.setattr(settings, "openai_api_key", "")
    body = {"model": MODEL, "stream": stream}
    if surface == "responses":
        body["input"] = "hello"
    else:
        body["messages"] = [{"role": "user", "content": "hello"}]
    with respx.mock() as upstream:
        route = upstream.post(RUNTIME + "/responses").mock(return_value=_reply(stream))
        response = runtime_client.post(f"/openai/v1/{surface}", headers=AUTH, json=body)
    assert response.status_code == 200
    sent = route.calls[0].request
    assert sent.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "Credential=test-access/" in sent.headers["authorization"]
    assert "/us-west-2/bedrock/aws4_request" in sent.headers["authorization"]
    assert sent.headers["x-amz-security-token"] == "test-token"


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("endpoint", "auth_type", "expected"),
    [
        (
            "https://provider.test/v1",
            "bearer_token",
            "https://provider.test/v1",
        ),
        (
            "https://provider.test/custom",
            "ak_sk",
            "https://provider.test/custom",
        ),
        (
            "https://bedrock-mantle.eu-west-1.api.aws/v1",
            "bearer_token",
            "https://bedrock-runtime.eu-west-1.amazonaws.com/openai/v1",
        ),
        (None, "ak_sk", "https://bedrock-runtime.eu-west-1.amazonaws.com/openai/v1"),
    ],
)
def test_provider_endpoint_region_and_credentials(
    runtime_client, mock_api_key_manager, surface, stream, endpoint, auth_type, expected
):
    mock_api_key_manager.validate_api_key.return_value["provider_id"] = "provider-eu"
    manager = MagicMock()
    manager.get_provider.return_value = {
        "is_active": True,
        "endpoint_url": endpoint,
        "aws_region": "eu-west-1",
        "auth_type": auth_type,
    }
    manager.get_decrypted_credentials.return_value = {
        "bearer_token": "provider-key",
        "access_key_id": "provider-access",
        "secret_access_key": "provider-secret",
        "session_token": "provider-token",
    }
    body = {"model": MODEL, "stream": stream}
    if surface == "responses":
        body.update(_native_request())
    else:
        body["messages"] = [{"role": "user", "content": "hello"}]
    with (
        patch(
            "app.api.openai_passthrough.router.ProviderManager", return_value=manager
        ),
        respx.mock() as upstream,
    ):
        route = upstream.post(expected + "/responses").mock(return_value=_reply(stream))
        response = runtime_client.post(f"/openai/v1/{surface}", headers=AUTH, json=body)
    assert response.status_code == 200
    sent = route.calls[0].request
    if auth_type == "bearer_token":
        assert sent.headers["authorization"] == "Bearer provider-key"
    elif endpoint is None:
        assert "Credential=provider-access/" in sent.headers["authorization"]
        assert "/eu-west-1/bedrock/aws4_request" in sent.headers["authorization"]
        assert sent.headers["x-amz-security-token"] == "provider-token"
    else:
        assert "authorization" not in sent.headers
        assert "x-amz-security-token" not in sent.headers
    if surface == "responses":
        assert json.loads(sent.content) == body


@pytest.mark.parametrize("stream", [False, True])
def test_flag_off_restores_mantle_transforms(runtime_client, monkeypatch, stream):
    monkeypatch.setattr(settings, "enable_bedrock_responses", False)
    body = _native_request()
    body["tools"] = [{"type": "custom", "name": "edit"}]
    body["stream"] = stream
    with respx.mock() as upstream:
        route = upstream.post(MANTLE + "/responses").mock(return_value=_reply(stream))
        response = runtime_client.post("/openai/v1/responses", headers=AUTH, json=body)
    assert response.status_code == 200
    sent = json.loads(route.calls[0].request.content)
    assert sent["tools"][0]["type"] == "function"
    assert sent["tool_choice"] == "auto"
    assert sent["reasoning"]["effort"] != "ultra"
    assert sent["input"][0]["content"] == "previous"


@pytest.mark.parametrize("stream", [False, True])
def test_runtime_errors_return_unchanged(runtime_client, stream):
    error = {"error": {"type": "invalid_request_error", "message": "native rejection"}}
    with respx.mock() as upstream:
        upstream.post(RUNTIME + "/responses").mock(
            return_value=httpx.Response(400, json=error)
        )
        response = runtime_client.post(
            "/openai/v1/responses",
            headers=AUTH,
            json={"model": MODEL, "input": "hello", "stream": stream},
        )
    assert response.status_code == 400
    assert response.json() == error


def test_retrieve_keeps_existing_model_independent_target(runtime_client):
    with respx.mock() as upstream:
        upstream.get(MANTLE + "/responses/resp-runtime").mock(
            return_value=httpx.Response(200, json=RESPONSE)
        )
        response = runtime_client.get("/openai/v1/responses/resp-runtime", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == RESPONSE


def test_mixed_sse_synthesizes_only_missing_event(runtime_client):
    content = SSE + 'data: {"type":"response.done"}\n\n'
    with respx.mock() as upstream:
        upstream.post(RUNTIME + "/responses").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        )
        response = runtime_client.post(
            "/openai/v1/responses",
            headers=AUTH,
            json={"model": MODEL, "input": "hello", "stream": True},
        )
    assert (
        response.text
        == SSE + 'event: response.done\ndata: {"type":"response.done"}\n\n'
    )


def test_dynamodb_self_mapping_beats_defaults(
    runtime_client, mock_model_mapping_manager, monkeypatch
):
    mock_model_mapping_manager.get_mapping.return_value = "openai.gpt-oss-120b"
    monkeypatch.setattr(
        settings, "default_model_mapping", {"openai.gpt-oss-120b": MODEL}
    )
    with respx.mock() as upstream:
        route = upstream.post(
            "https://bedrock-mantle.us-west-2.api.aws/v1/responses"
        ).mock(return_value=httpx.Response(200, json=RESPONSE))
        response = runtime_client.post(
            "/openai/v1/responses",
            headers=AUTH,
            json={"model": "openai.gpt-oss-120b", "input": "hello"},
        )
    assert response.status_code == 200
    assert json.loads(route.calls[0].request.content)["model"] == "openai.gpt-oss-120b"


def test_defaults_work_when_dynamodb_lookup_fails(
    runtime_client, mock_model_mapping_manager, monkeypatch
):
    mock_model_mapping_manager.get_mapping.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr(settings, "default_model_mapping", {"alias": MODEL})
    with respx.mock() as upstream:
        route = upstream.post(RUNTIME + "/responses").mock(
            return_value=httpx.Response(200, json=RESPONSE)
        )
        response = runtime_client.post(
            "/openai/v1/responses",
            headers=AUTH,
            json={"model": "alias", "input": "hello"},
        )
    assert response.status_code == 200
    assert json.loads(route.calls[0].request.content)["model"] == MODEL


@pytest.mark.parametrize("surface", ["responses", "chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("auth_type", "credentials"),
    [
        ("ak_sk", {}),
        ("ak_sk", {"access_key_id": "some-access"}),
        ("ak_sk", {"access_key_id": "some-access", "secret_access_key": "  "}),
        ("bearer_token", {}),
        ("bearer_token", {"bearer_token": "  "}),
        ("ak_sk", RuntimeError("credential-secret-must-not-leak")),
        ("unsupported", {}),
    ],
)
def test_selected_provider_never_falls_back_to_global_credentials(
    runtime_client,
    mock_api_key_manager,
    caplog,
    surface,
    stream,
    auth_type,
    credentials,
):
    mock_api_key_manager.validate_api_key.return_value["provider_id"] = (
        "provider-broken"
    )
    manager = MagicMock()
    manager.get_provider.return_value = {
        "is_active": True,
        "aws_region": "eu-west-1",
        "auth_type": auth_type,
    }
    if isinstance(credentials, Exception):
        manager.get_decrypted_credentials.side_effect = credentials
    else:
        manager.get_decrypted_credentials.return_value = credentials
    body = {"model": MODEL, "stream": stream}
    if surface == "responses":
        body["input"] = "hello"
    else:
        body["messages"] = [{"role": "user", "content": "hello"}]
    with (
        patch(
            "app.api.openai_passthrough.router.ProviderManager", return_value=manager
        ),
        respx.mock() as upstream,
    ):
        response = runtime_client.post(f"/openai/v1/{surface}", headers=AUTH, json=body)
    assert response.status_code == 500
    assert response.json()["error"]["type"] == "api_error"
    assert "Selected Runtime provider" in response.json()["error"]["message"]
    assert "credential-secret-must-not-leak" not in response.text + caplog.text
    assert not upstream.calls


@pytest.mark.parametrize(
    "provider", [None, {"is_active": False}, RuntimeError("offline")]
)
def test_unavailable_runtime_provider_fails_closed(
    runtime_client, mock_api_key_manager, provider
):
    mock_api_key_manager.validate_api_key.return_value["provider_id"] = (
        "provider-broken"
    )
    manager = MagicMock()
    if isinstance(provider, Exception):
        manager.get_provider.side_effect = provider
    else:
        manager.get_provider.return_value = provider
    with (
        patch(
            "app.api.openai_passthrough.router.ProviderManager", return_value=manager
        ),
        respx.mock() as upstream,
    ):
        response = runtime_client.post(
            "/openai/v1/responses",
            headers=AUTH,
            json={"model": MODEL, "input": "hello"},
        )
    assert response.status_code == 500
    assert not upstream.calls


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("auth_type", ["ak_sk", "bearer_token"])
def test_runtime_web_search_retains_provider_identity(
    runtime_client,
    mock_api_key_manager,
    mock_bedrock_service,
    mock_web_search_service,
    mock_response_context_store,
    auth_type,
    stream,
):
    mock_api_key_manager.validate_api_key.return_value["provider_id"] = "provider-eu"
    manager = MagicMock()
    manager.get_provider.return_value = {
        "is_active": True,
        "aws_region": "eu-west-1",
        "auth_type": auth_type,
    }
    manager.get_decrypted_credentials.return_value = {
        "access_key_id": "provider-access",
        "secret_access_key": "provider-secret",
        "session_token": "provider-token",
        "bearer_token": "provider-key",
    }
    mock_web_search_service.handle_request.return_value = MessageResponse(
        id="msg-search",
        type="message",
        role="assistant",
        model=MODEL,
        stop_reason="end_turn",
        content=[{"type": "text", "text": "search answer"}],
        usage=Usage(input_tokens=8, output_tokens=3),
    )
    with (
        patch(
            "app.api.openai_passthrough.router.ProviderManager", return_value=manager
        ),
        respx.mock() as upstream,
    ):
        response = runtime_client.post(
            "/openai/v1/responses",
            headers=AUTH,
            json={
                "model": MODEL,
                "input": "search for weather",
                "tools": [{"type": "web_search"}],
                "stream": stream,
            },
        )
    assert response.status_code == 200
    assert "search answer" in response.text
    assert not upstream.calls
    constructor = mock_bedrock_service.constructor_mock.call_args.kwargs
    assert constructor["provider_id"] == "provider-eu"
    assert constructor["openai_base_url"] == (
        "https://bedrock-runtime.eu-west-1.amazonaws.com/openai/v1"
    )
    assert constructor["openai_api_key"] == (
        "" if auth_type == "ak_sk" else "provider-key"
    )
    loop = mock_web_search_service.handle_request.call_args.kwargs
    assert loop["bedrock_service"] is mock_bedrock_service
    assert loop["request"].model == MODEL
    mock_response_context_store.save.assert_called_once()


def test_legacy_provider_endpoint_wins_over_configured_runtime(
    runtime_client, mock_api_key_manager, monkeypatch
):
    monkeypatch.setattr(settings, "openai_base_url", RUNTIME)
    mock_api_key_manager.validate_api_key.return_value["provider_id"] = (
        "provider-mantle"
    )
    manager = MagicMock()
    manager.get_provider.return_value = {
        "is_active": True,
        "endpoint_url": MANTLE,
        "auth_type": "bearer_token",
    }
    manager.get_decrypted_credentials.return_value = {"bearer_token": "provider-key"}
    with (
        patch(
            "app.api.openai_passthrough.router.ProviderManager", return_value=manager
        ),
        respx.mock() as upstream,
    ):
        route = upstream.post(
            "https://bedrock-mantle.us-west-2.api.aws/v1/responses"
        ).mock(return_value=httpx.Response(200, json=RESPONSE))
        response = runtime_client.post(
            "/openai/v1/responses",
            headers=AUTH,
            json={"model": "openai.gpt-oss-120b", "input": "hello"},
        )
    assert response.status_code == 200
    assert route.calls[0].request.headers["authorization"] == "Bearer provider-key"


def test_explicit_runtime_gpt_oss_preserves_native_body(runtime_client, monkeypatch):
    monkeypatch.setattr(settings, "openai_base_url", RUNTIME)
    body = _native_request("openai.gpt-oss-120b")
    with respx.mock() as upstream:
        route = upstream.post(RUNTIME + "/responses").mock(
            return_value=httpx.Response(200, json=RESPONSE)
        )
        response = runtime_client.post("/openai/v1/responses", headers=AUTH, json=body)
    assert response.status_code == 200
    assert json.loads(route.calls[0].request.content) == body
