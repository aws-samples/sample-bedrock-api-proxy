"""Regression coverage for default Runtime routing and credential isolation."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from botocore.credentials import Credentials

from app.core.config import settings
from app.schemas.anthropic import MessageRequest, MessageResponse
from app.services.bedrock_openai import (
    BedrockSigV4Auth,
    is_runtime_model,
    resolve_runtime_base_url,
)
from app.services.bedrock_service import BedrockService


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setattr(settings, "enable_bedrock_responses", True)
    monkeypatch.setattr(settings, "enable_openai_compat", False)
    monkeypatch.setattr(settings, "openai_base_url", "")
    monkeypatch.setattr(settings, "openai_api_key", "test-bedrock-key")
    monkeypatch.setattr(settings, "bedrock_endpoint_url", None)
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)


def request(model):
    return MessageRequest(
        model=model, max_tokens=128, messages=[{"role": "user", "content": "hello"}]
    )


def response():
    return MessageResponse(
        id="msg_test",
        model="upstream",
        content=[{"type": "text", "text": "OK"}],
        stop_reason="end_turn",
        usage={"input_tokens": 2, "output_tokens": 1},
    )


def service():
    with patch("boto3.client") as client:
        client.return_value._request_signer._credentials = Credentials("ak", "sk", "st")
        svc = BedrockService(dynamodb_client=MagicMock())
    svc.anthropic_to_bedrock._convert_model_id = lambda model: model
    return svc


@pytest.mark.parametrize("prefix", ["global", "us", "us-gov", "eu", "apac", "ca"])
async def test_scoped_models_default_to_responses_without_compat(prefix):
    svc = service()
    target = MagicMock()
    target.invoke_responses = AsyncMock(return_value=response())
    model = f"{prefix}.openai.gpt-5.6-luna"
    with patch(
        "app.services.openai_compat_service.OpenAICompatService", return_value=target
    ) as factory:
        result = await svc.invoke_model(request(model))
    factory.assert_called_once_with(
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
        api_key="test-bedrock-key",
    )
    assert target.invoke_responses.await_args.args[0].model == model
    target.invoke_model.assert_not_called()
    assert result.model == model
    svc.client.converse.assert_not_called()


@pytest.mark.parametrize(
    "model",
    [
        "global.anthropic.claude-sonnet-4-5",
        "us.claude.example",
        "openai.gpt-5.6-luna",
        "amazon.nova-pro-v1:0",
        "custom.openai.model",
        "global.",
        "global.openai",
    ],
)
def test_only_scoped_non_claude_ids_match(model):
    assert not is_runtime_model(model)


async def test_mapped_alias_routes_and_preserves_request_model():
    svc = service()
    svc.anthropic_to_bedrock._convert_model_id = lambda _: "global.openai.gpt-5.6-luna"
    target = MagicMock()
    target.invoke_responses = AsyncMock(return_value=response())
    original = request("luna-alias")
    with patch(
        "app.services.openai_compat_service.OpenAICompatService", return_value=target
    ):
        result = await svc.invoke_model(original)
    assert (
        target.invoke_responses.await_args.args[0].model == "global.openai.gpt-5.6-luna"
    )
    assert original.model == result.model == "luna-alias"


async def test_alias_to_claude_stays_native():
    svc = service()
    svc.anthropic_to_bedrock._convert_model_id = (
        lambda _: "global.anthropic.claude-test"
    )
    with patch.object(svc, "_invoke_model_sync", return_value=response()) as native:
        await svc.invoke_model(request("alias"))
    native.assert_called_once()
    assert not svc._responses_services


async def test_disable_flag_restores_previous_dispatch(monkeypatch):
    monkeypatch.setattr(settings, "enable_bedrock_responses", False)
    svc = service()
    with patch.object(svc, "_invoke_model_sync", return_value=response()) as native:
        await svc.invoke_model(request("us.openai.gpt-5.6-luna"))
    native.assert_called_once()
    assert not svc._responses_services


async def test_stream_uses_responses_and_preserves_alias():
    svc = service()
    svc.anthropic_to_bedrock._convert_model_id = lambda _: "us.openai.gpt-5.6-luna"
    target = MagicMock()

    async def events(req, request_id):
        assert req.model == "us.openai.gpt-5.6-luna"
        yield 'event: message_start\ndata: {"type":"message_start","message":{"model":"us.openai.gpt-5.6-luna"}}\n\n'
        yield 'event: message_stop\ndata: {"type":"message_stop"}\n\n'

    target.invoke_responses_stream = events
    with patch(
        "app.services.openai_compat_service.OpenAICompatService", return_value=target
    ):
        result = [event async for event in svc.invoke_model_stream(request("alias"))]
    assert '"model": "alias"' in result[0]
    target.invoke_model_stream.assert_not_called()
    svc.client.converse_stream.assert_not_called()


def test_sync_entrypoint_uses_responses():
    svc = service()
    target = MagicMock()
    target.invoke_responses_sync.return_value = response()
    with patch(
        "app.services.openai_compat_service.OpenAICompatService", return_value=target
    ):
        svc._invoke_model_sync_inner(request("global.openai.gpt-5.6-luna"))
    target.invoke_responses_sync.assert_called_once()
    target.invoke_model_sync.assert_not_called()


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (None, "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1"),
        (
            "https://bedrock-mantle.eu-west-1.api.aws/v1",
            "https://bedrock-runtime.eu-west-1.amazonaws.com/openai/v1",
        ),
        (
            "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/",
            "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
        ),
        ("https://gateway.example/custom/v1", "https://gateway.example/custom/v1"),
    ],
)
def test_endpoint_selection(base, expected):
    assert resolve_runtime_base_url(base) == expected


def test_sigv4_signs_actual_body_and_endpoint_region():
    def handle(req):
        assert req.headers["authorization"].startswith(
            "AWS4-HMAC-SHA256 Credential=ak/"
        )
        assert "/us-west-2/bedrock/aws4_request" in req.headers["authorization"]
        assert req.headers["x-amz-security-token"] == "st"
        assert req.content == b'{"model":"us.openai.example","input":"hi"}'
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        transport=httpx.MockTransport(handle),
        auth=BedrockSigV4Auth(Credentials("ak", "sk", "st"), "us-east-1"),
    ) as client:
        assert (
            client.post(
                "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/responses",
                json={"model": "us.openai.example", "input": "hi"},
                headers={"Authorization": "Bearer aws-sigv4"},
            ).status_code
            == 200
        )


def test_provider_account_credentials_do_not_use_global_key():
    svc = service()
    manager = MagicMock()
    manager.get_provider.return_value = {
        "is_active": True,
        "auth_type": "ak_sk",
        "aws_region": "eu-west-1",
    }
    manager.get_decrypted_credentials.return_value = {
        "access_key_id": "provider-ak",
        "secret_access_key": "provider-sk",
        "session_token": "provider-st",
    }
    svc._provider_manager = manager
    with patch("app.services.openai_compat_service.OpenAICompatService") as factory:
        svc._responses_service_for_model("eu.openai.example", "provider-1")
    kwargs = factory.call_args.kwargs
    assert (
        kwargs["base_url"]
        == "https://bedrock-runtime.eu-west-1.amazonaws.com/openai/v1"
    )
    assert kwargs["api_key"] == "aws-sigv4"
    assert kwargs["http_client"]._auth.credentials.access_key == "provider-ak"
    kwargs["http_client"].close()


def test_default_iam_auth_uses_existing_aws_credentials(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    svc = service()
    with patch("app.services.openai_compat_service.OpenAICompatService") as factory:
        svc._responses_service_for_model("global.openai.example")
    kwargs = factory.call_args.kwargs
    assert kwargs["api_key"] == "aws-sigv4"
    assert kwargs["http_client"]._auth.credentials.access_key == "ak"
    kwargs["http_client"].close()


def test_explicit_provider_region_ignores_global_endpoint(monkeypatch):
    monkeypatch.setattr(
        settings,
        "bedrock_endpoint_url",
        "https://bedrock-runtime.us-east-1.amazonaws.com",
    )
    assert resolve_runtime_base_url(region="eu-west-1") == (
        "https://bedrock-runtime.eu-west-1.amazonaws.com/openai/v1"
    )
    assert resolve_runtime_base_url() == (
        "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1"
    )


def test_retired_iam_client_closes_after_active_reference_is_released(monkeypatch):
    import gc

    monkeypatch.setattr(settings, "openai_api_key", "")
    svc = service()
    svc._provider_client_ttl = 0
    active = svc._responses_service_for_model("us.openai.example")
    old_client = active.client
    replacement = svc._responses_service_for_model("us.openai.example")
    assert replacement is not active
    assert not old_client.is_closed()
    del active
    gc.collect()
    assert old_client.is_closed()
    replacement.client.close()
