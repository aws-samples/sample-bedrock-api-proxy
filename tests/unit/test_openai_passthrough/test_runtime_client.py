"""Runtime URL selection and authentication boundaries."""

import threading
from unittest.mock import Mock

import httpx
import pytest
from botocore.credentials import Credentials

from app.api.openai_passthrough import client as upstream
from app.core.config import settings
from app.services.bedrock_openai import sign_request


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setattr(settings, "enable_bedrock_responses", True)
    monkeypatch.setattr(settings, "openai_base_url", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "bedrock_endpoint_url", None)
    monkeypatch.setattr(settings, "aws_region", "us-east-1")


@pytest.mark.parametrize(
    "prefix", ["global", "us", "us-gov", "eu", "apac", "ca", "sa", "af", "me", "cn"]
)
def test_scoped_models_use_runtime(prefix):
    assert (
        upstream.upstream_url("/responses", model=f"{prefix}.openai.gpt-oss-120b")
        == "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/responses"
    )


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (
            "https://bedrock-mantle.us-west-2.api.aws/v1",
            "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
        ),
        (
            "https://bedrock-runtime.us-west-2.amazonaws.com",
            "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
        ),
        (
            "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/",
            "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
        ),
        ("https://provider.test/v1", "https://provider.test/v1"),
        ("https://provider.test/custom", "https://provider.test/custom"),
    ],
)
def test_explicit_endpoint_precedence(base, expected, monkeypatch):
    monkeypatch.setattr(
        settings, "openai_base_url", "https://bedrock-mantle.eu-west-1.api.aws/v1"
    )
    assert (
        upstream.upstream_url("responses", base, "global.openai.gpt-oss-120b")
        == expected + "/responses"
    )


def test_configured_runtime_region_wins_over_aws_region(monkeypatch):
    monkeypatch.setattr(
        settings,
        "openai_base_url",
        "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
    )
    assert (
        upstream.upstream_url("/responses", model="global.openai.gpt-5.6")
        == "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/responses"
    )


def test_empty_openai_endpoint_uses_configured_bedrock_endpoint(monkeypatch):
    monkeypatch.setattr(
        settings,
        "bedrock_endpoint_url",
        "https://bedrock-runtime.eu-west-1.amazonaws.com",
    )
    assert (
        upstream.upstream_url("/responses", model="global.openai.gpt-5.6")
        == "https://bedrock-runtime.eu-west-1.amazonaws.com/openai/v1/responses"
    )


def test_unprefixed_gpt_oss_keeps_runtime_path(monkeypatch):
    monkeypatch.setattr(
        settings,
        "openai_base_url",
        "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
    )
    assert (
        upstream.upstream_url("/responses", model="openai.gpt-oss-120b")
        == "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/responses"
    )


@pytest.mark.parametrize(
    ("model", "path"),
    [
        ("openai.gpt-oss-120b", "/v1"),
        ("openai.gpt-5.6", "/openai/v1"),
        ("globalanthropic.claude-sonnet", "/openai/v1"),
        ("global.anthropic.claude-sonnet", "/openai/v1"),
        ("usopenai.gpt-5.6", "/openai/v1"),
        ("global", "/openai/v1"),
    ],
)
def test_legacy_models_keep_mantle_routing(model, path):
    base = "https://bedrock-mantle.us-east-1.api.aws"
    assert (
        upstream.upstream_url("/responses", base + "/openai/v1", model)
        == base + path + "/responses"
    )


def test_flag_off_keeps_mantle_routing(monkeypatch):
    monkeypatch.setattr(settings, "enable_bedrock_responses", False)
    base = "https://bedrock-mantle.us-west-2.api.aws/openai/v1"
    assert (
        upstream.upstream_url("/responses", base, "global.openai.gpt-5.6")
        == base + "/responses"
    )


@pytest.mark.parametrize(
    "authorization", [None, "", "Bearer", "Bearer   ", "bearer \t"]
)
async def test_request_hook_signs_without_nonempty_bearer(monkeypatch, authorization):
    event_loop_thread = threading.get_ident()
    signing_threads = []
    credentials = Credentials("provider-access", "provider-secret", "provider-token")

    def sign(request, credentials=None):
        signing_threads.append(threading.get_ident())
        sign_request(request, credentials=credentials)

    signer = Mock(side_effect=sign)
    monkeypatch.setattr(upstream, "sign_request", signer)
    captured = []

    def handle(request):
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        event_hooks={"request": [upstream._sign_runtime_request]},
    ) as client:
        await client.post(
            "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/responses",
            json={"model": "global.openai.gpt-5.6", "input": "hello"},
            headers={} if authorization is None else {"Authorization": authorization},
            extensions={"bedrock_credentials": credentials},
        )

    signer.assert_called_once()
    sent = captured[0]
    assert "provider-access/" in sent.headers["authorization"]
    assert "/us-west-2/bedrock/aws4_request" in sent.headers["authorization"]
    assert sent.headers["x-amz-security-token"] == "provider-token"
    assert signing_threads[0] != event_loop_thread


@pytest.mark.parametrize(
    "url",
    [
        "https://provider.test/openai/v1/responses",
        "https://bedrock-mantle.us-west-2.api.aws/openai/v1/responses",
        "https://bedrock-runtime.us-west-2.amazonaws.com.evil.test/openai/v1/responses",
        "https://evil-bedrock-runtime.us-west-2.amazonaws.com/openai/v1/responses",
        "http://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/responses",
    ],
)
async def test_request_hook_never_signs_other_hosts(monkeypatch, url):
    signer = Mock()
    monkeypatch.setattr(upstream, "sign_request", signer)
    request = httpx.Request("POST", url, json={"input": "hello"})
    await upstream._sign_runtime_request(request)
    signer.assert_not_called()
    assert "authorization" not in request.headers


@pytest.mark.parametrize("authorization", ["Bearer api-key", "bearer api-key"])
async def test_request_hook_preserves_bearer(monkeypatch, authorization):
    signer = Mock()
    monkeypatch.setattr(upstream, "sign_request", signer)
    request = httpx.Request(
        "POST",
        "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1/responses",
        headers={"Authorization": authorization},
    )
    await upstream._sign_runtime_request(request)
    signer.assert_not_called()
    assert request.headers["authorization"] == authorization


def test_explicit_empty_key_suppresses_global_bearer(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "global-key")
    assert upstream.upstream_headers()["Authorization"] == "Bearer global-key"
    assert "Authorization" not in upstream.upstream_headers(api_key="")


async def test_shared_client_installs_signing_hook(monkeypatch):
    monkeypatch.setattr(upstream, "_client", None)
    client = upstream.get_client()
    try:
        assert upstream._sign_runtime_request in client.event_hooks["request"]
    finally:
        await client.aclose()
