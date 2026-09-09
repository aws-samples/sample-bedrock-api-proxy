"""Unit tests for OpenAICompatService Responses API invocation + endpoint override.

These tests verify:

1. The constructor forwards per-call ``base_url`` / ``api_key`` overrides to the
   underlying OpenAI client (used by the multi-provider / per-key path).
2. With no overrides, the client is built from global settings.
3. ``invoke_responses_sync`` wires the Responses converters together and returns
   a well-formed Anthropic ``MessageResponse`` (a ``function_call`` output item
   becomes a ``tool_use`` block with ``stop_reason == "tool_use"``).
4. ``invoke_responses_sync`` forwards ``store=False`` (and a model) to
   ``client.responses.create``.
5. The async ``invoke_responses`` wrapper returns the same result as the sync path.

All tests patch ``app.services.openai_compat_service.OpenAI`` so no network
calls happen.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.schemas.anthropic import Message, MessageRequest


def _request() -> MessageRequest:
    """A minimal non-Claude MessageRequest."""
    return MessageRequest(
        model="openai.gpt-5.5",
        messages=[Message(role="user", content="hi")],
        max_tokens=1024,
    )


def _responses_dict() -> dict[str, Any]:
    """A realistic OpenAI Responses API response dict with a function_call item."""
    return {
        "id": "resp_x",
        "model": "openai.gpt-5.5",
        "output": [
            {
                "type": "function_call",
                "call_id": "call_0",
                "name": "web_search",
                "arguments": '{"query":"hi"}',
            }
        ],
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }


def _make_service_with_fake_response(resp_dict: dict[str, Any]):
    """Build a service whose patched client.responses.create returns resp_dict.

    Returns (service, fake_client).
    """
    with patch("app.services.openai_compat_service.OpenAI") as mock_openai:
        from app.services.openai_compat_service import OpenAICompatService

        fake_client = mock_openai.return_value
        fake_response = MagicMock(name="responses_response")
        fake_response.model_dump.return_value = resp_dict
        fake_client.responses.create.return_value = fake_response

        svc = OpenAICompatService()
        return svc, fake_client


# ---------------------------------------------------------------------------
# Constructor override tests
# ---------------------------------------------------------------------------


def test_constructor_forwards_base_url_and_api_key_overrides():
    with patch("app.services.openai_compat_service.OpenAI") as mock_openai:
        from app.services.openai_compat_service import OpenAICompatService

        OpenAICompatService(base_url="https://prov.test/openai/v1", api_key="prov-key")

        _, kwargs = mock_openai.call_args
        assert kwargs["base_url"] == "https://prov.test/openai/v1"
        assert kwargs["api_key"] == "prov-key"


def test_constructor_defaults_to_global_settings(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.openai_base_url",
        "https://global.test/openai/v1",
        raising=False,
    )
    monkeypatch.setattr(
        "app.core.config.settings.openai_api_key",
        "global-key",
        raising=False,
    )

    with patch("app.services.openai_compat_service.OpenAI") as mock_openai:
        from app.services.openai_compat_service import OpenAICompatService

        OpenAICompatService()

        _, kwargs = mock_openai.call_args
        assert kwargs["base_url"] == "https://global.test/openai/v1"
        assert kwargs["api_key"] == "global-key"
        assert "http_client" not in kwargs


def test_constructor_forwards_supplied_http_client():
    with httpx.Client() as client:
        with patch("app.services.openai_compat_service.OpenAI") as mock_openai:
            from app.services.openai_compat_service import OpenAICompatService

            OpenAICompatService(http_client=client)
            assert mock_openai.call_args.kwargs["http_client"] is client


# ---------------------------------------------------------------------------
# invoke_responses_sync tests
# ---------------------------------------------------------------------------


def test_invoke_responses_sync_returns_tool_use_response():
    svc, _ = _make_service_with_fake_response(_responses_dict())

    response = svc.invoke_responses_sync(_request())

    assert response.stop_reason == "tool_use"
    tool_blocks = [b for b in response.content if b.type == "tool_use"]
    assert len(tool_blocks) == 1
    block = tool_blocks[0]
    assert block.id == "call_0"
    assert block.name == "web_search"
    assert block.input == {"query": "hi"}


def test_invoke_responses_sync_passes_store_false_and_model():
    svc, fake_client = _make_service_with_fake_response(_responses_dict())

    svc.invoke_responses_sync(_request())

    _, kwargs = fake_client.responses.create.call_args
    assert kwargs["store"] is False
    assert kwargs["model"] == "openai.gpt-5.5"


# ---------------------------------------------------------------------------
# async invoke_responses test (asyncio_mode = "auto", no marker needed)
# ---------------------------------------------------------------------------


async def test_invoke_responses_async_matches_sync():
    svc, _ = _make_service_with_fake_response(_responses_dict())

    response = await svc.invoke_responses(_request())

    assert response.stop_reason == "tool_use"
    tool_blocks = [b for b in response.content if b.type == "tool_use"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].id == "call_0"
    assert tool_blocks[0].input == {"query": "hi"}


def test_sync_real_sdk_uses_supplied_transport_and_overrides_stream():
    import json

    from app.services.openai_compat_service import OpenAICompatService

    bodies = []

    def handle(request):
        assert request.url.path == "/openai/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_responses_dict())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        service = OpenAICompatService(
            base_url="https://runtime.test/openai/v1",
            api_key="test-key",
            http_client=client,
        )
        request = _request().model_copy(update={"stream": True})
        response = service.invoke_responses_sync(request)
    assert bodies[0]["stream"] is False
    assert bodies[0]["store"] is False
    assert response.content[0].id == "call_0"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_sync_real_sdk_maps_http_errors(status):
    from app.core.exceptions import BedrockAPIError
    from app.services.openai_compat_service import OpenAICompatService

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status, json={"error": {"message": "upstream error"}}
            )
        )
    ) as client:
        service = OpenAICompatService(
            base_url="https://runtime.test", api_key="test-key", http_client=client
        )
        service.client.max_retries = 0
        with pytest.raises(BedrockAPIError) as error:
            service.invoke_responses_sync(_request())
    assert error.value.http_status == status


@pytest.mark.parametrize(
    "body",
    [
        {
            "status": "failed",
            "output": [],
            "error": {"code": "server_error", "message": "Failed generation"},
        },
        {"__type": "UnknownOperationException", "message": "Unsupported operation"},
    ],
)
def test_sync_real_sdk_rejects_error_in_successful_http_response(body):
    from app.core.exceptions import BedrockAPIError
    from app.services.openai_compat_service import OpenAICompatService

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        service = OpenAICompatService(
            base_url="https://runtime.test", api_key="test-key", http_client=client
        )
        with pytest.raises(BedrockAPIError):
            service.invoke_responses_sync(_request())
