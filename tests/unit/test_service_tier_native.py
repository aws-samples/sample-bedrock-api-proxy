"""Tests for service_tier support in InvokeModel / InvokeModelWithResponseStream paths.

The InvokeModel API (used for Claude models) takes serviceTier as a plain string
kwarg ('reserved', 'flex', etc.), unlike the Converse API which uses a dict
({"type": "reserved"}).
"""
import json
import threading
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.services.bedrock_service import BedrockService
from app.schemas.anthropic import MessageRequest, Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service():
    """Create a BedrockService with __init__ bypassed."""
    with patch.object(BedrockService, "__init__", lambda self, **kw: None):
        service = BedrockService.__new__(BedrockService)
        service.client = MagicMock(name="default_client")
        service._provider_clients = {}
        service._provider_clients_lock = threading.Lock()
        service._provider_client_ttl = 300
        service._provider_manager = None
        # Ensure get_client returns the mock client
        service.get_client = MagicMock(return_value=service.client)
        # Stub converters (not under test)
        service.anthropic_to_bedrock = MagicMock()
        service.bedrock_to_anthropic = MagicMock()
        return service


def _simple_request(model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0") -> MessageRequest:
    return MessageRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        max_tokens=1024,
    )


def _native_response_body() -> dict:
    """Minimal native Anthropic response body."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _mock_invoke_model_response(service_tier_value: str = "default") -> dict:
    """Mock return value for client.invoke_model()."""
    body_mock = MagicMock()
    body_mock.read.return_value = json.dumps(_native_response_body()).encode()
    resp = {
        "body": body_mock,
        "contentType": "application/json",
        "serviceTier": service_tier_value,
    }
    return resp


def _client_error(message: str, code: str = "ValidationException") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "InvokeModel",
    )


# ---------------------------------------------------------------------------
# Non-streaming: _invoke_model_native_sync
# ---------------------------------------------------------------------------

class TestInvokeModelNativeServiceTier:
    """service_tier handling in _invoke_model_native_sync (InvokeModel API)."""

    def test_service_tier_reserved_passed_as_string(self):
        """serviceTier='reserved' is passed as a plain string kwarg."""
        service = _make_service()
        service.client.invoke_model.return_value = _mock_invoke_model_response("reserved")

        service._invoke_model_native_sync(
            _simple_request(), request_id="r1", service_tier="reserved",
        )

        call_kwargs = service.client.invoke_model.call_args.kwargs
        assert call_kwargs["serviceTier"] == "reserved"
        assert isinstance(call_kwargs["serviceTier"], str)

    def test_service_tier_default_omitted(self):
        """When service_tier is 'default', serviceTier kwarg is NOT sent."""
        service = _make_service()
        service.client.invoke_model.return_value = _mock_invoke_model_response()

        service._invoke_model_native_sync(
            _simple_request(), request_id="r1", service_tier="default",
        )

        call_kwargs = service.client.invoke_model.call_args.kwargs
        assert "serviceTier" not in call_kwargs

    def test_service_tier_none_uses_settings_default(self):
        """When service_tier is None, falls back to settings.default_service_tier."""
        service = _make_service()
        service.client.invoke_model.return_value = _mock_invoke_model_response()

        with patch("app.services.bedrock_service.settings") as mock_settings:
            mock_settings.default_service_tier = "default"
            mock_settings.strip_cache_scope = False
            service._invoke_model_native_sync(
                _simple_request(), request_id="r1", service_tier=None,
            )

        call_kwargs = service.client.invoke_model.call_args.kwargs
        assert "serviceTier" not in call_kwargs

    def test_service_tier_flex_passed(self):
        """serviceTier='flex' is passed correctly."""
        service = _make_service()
        service.client.invoke_model.return_value = _mock_invoke_model_response("flex")

        service._invoke_model_native_sync(
            _simple_request(), request_id="r1", service_tier="flex",
        )

        call_kwargs = service.client.invoke_model.call_args.kwargs
        assert call_kwargs["serviceTier"] == "flex"

    def test_retry_on_service_tier_error(self):
        """On service-tier-related ClientError, retries without serviceTier."""
        service = _make_service()
        # Use "does not support" phrasing — matches the retry heuristic
        service.client.invoke_model.side_effect = [
            _client_error("Model does not support serviceTier reserved"),
            _mock_invoke_model_response("default"),
        ]

        result = service._invoke_model_native_sync(
            _simple_request(), request_id="r1", service_tier="reserved",
        )

        assert service.client.invoke_model.call_count == 2
        # First call includes serviceTier
        first_kwargs = service.client.invoke_model.call_args_list[0].kwargs
        assert first_kwargs["serviceTier"] == "reserved"
        # Retry call does NOT include serviceTier
        retry_kwargs = service.client.invoke_model.call_args_list[1].kwargs
        assert "serviceTier" not in retry_kwargs
        assert result is not None

    def test_no_retry_on_unrelated_error(self):
        """Unrelated ClientError is NOT retried when service_tier is default."""
        service = _make_service()
        service.client.invoke_model.side_effect = _client_error("Access denied")

        from app.services.bedrock_service import BedrockAPIError
        with pytest.raises(BedrockAPIError):
            service._invoke_model_native_sync(
                _simple_request(), request_id="r1", service_tier="default",
            )

        assert service.client.invoke_model.call_count == 1


# ---------------------------------------------------------------------------
# Streaming: _stream_worker_native
# ---------------------------------------------------------------------------

class TestStreamWorkerNativeServiceTier:
    """service_tier handling in _stream_worker_native (InvokeModelWithResponseStream)."""

    def _make_stream_response(self, events: list, service_tier_value: str = "default") -> dict:
        """Create a mock response for invoke_model_with_response_stream."""
        body = [
            {"chunk": {"bytes": json.dumps(ev).encode()}}
            for ev in events
        ]
        return {"body": body, "serviceTier": service_tier_value}

    def _basic_stream_events(self) -> list:
        return [
            {
                "type": "message_start",
                "message": {"id": "msg_1", "usage": {"input_tokens": 10}},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hi"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
        ]

    def test_service_tier_reserved_passed_as_string(self):
        """serviceTier='reserved' kwarg is a plain string for streaming."""
        import queue
        service = _make_service()
        service.client.invoke_model_with_response_stream.return_value = (
            self._make_stream_response(self._basic_stream_events(), "reserved")
        )

        q = queue.Queue()
        service._stream_worker_native(
            bedrock_model_id="anthropic.claude-sonnet-4-5-20250929-v1:0",
            native_request={"anthropic_version": "bedrock-2023-05-31", "messages": [], "max_tokens": 1024},
            _request=_simple_request(),
            _message_id="msg_1",
            effective_service_tier="reserved",
            event_queue=q,
        )

        call_kwargs = service.client.invoke_model_with_response_stream.call_args.kwargs
        assert call_kwargs["serviceTier"] == "reserved"
        assert isinstance(call_kwargs["serviceTier"], str)

    def test_service_tier_default_omitted(self):
        """serviceTier kwarg is omitted when effective_service_tier is 'default'."""
        import queue
        service = _make_service()
        service.client.invoke_model_with_response_stream.return_value = (
            self._make_stream_response(self._basic_stream_events())
        )

        q = queue.Queue()
        service._stream_worker_native(
            bedrock_model_id="anthropic.claude-sonnet-4-5-20250929-v1:0",
            native_request={"anthropic_version": "bedrock-2023-05-31", "messages": [], "max_tokens": 1024},
            _request=_simple_request(),
            _message_id="msg_1",
            effective_service_tier="default",
            event_queue=q,
        )

        call_kwargs = service.client.invoke_model_with_response_stream.call_args.kwargs
        assert "serviceTier" not in call_kwargs

    def test_stream_events_reach_queue(self):
        """Stream events are correctly forwarded to the event queue."""
        import queue
        service = _make_service()
        events = self._basic_stream_events()
        service.client.invoke_model_with_response_stream.return_value = (
            self._make_stream_response(events, "reserved")
        )

        q = queue.Queue()
        service._stream_worker_native(
            bedrock_model_id="anthropic.claude-sonnet-4-5-20250929-v1:0",
            native_request={"anthropic_version": "bedrock-2023-05-31", "messages": [], "max_tokens": 1024},
            _request=_simple_request(),
            _message_id="msg_1",
            effective_service_tier="reserved",
            event_queue=q,
        )

        collected = []
        while not q.empty():
            collected.append(q.get_nowait())

        # 3 SSE events + 1 done signal
        event_items = [item for item in collected if item[0] == "event"]
        done_items = [item for item in collected if item[0] == "done"]
        assert len(event_items) == 3
        assert len(done_items) == 1

    def test_retry_on_service_tier_error(self):
        """On service-tier ClientError, retries without serviceTier."""
        import queue
        service = _make_service()

        service.client.invoke_model_with_response_stream.side_effect = [
            _client_error("Model does not support serviceTier reserved"),
            self._make_stream_response(self._basic_stream_events(), "default"),
        ]

        q = queue.Queue()
        service._stream_worker_native(
            bedrock_model_id="anthropic.claude-sonnet-4-5-20250929-v1:0",
            native_request={"anthropic_version": "bedrock-2023-05-31", "messages": [], "max_tokens": 1024},
            _request=_simple_request(),
            _message_id="msg_1",
            effective_service_tier="reserved",
            event_queue=q,
        )

        assert service.client.invoke_model_with_response_stream.call_count == 2
        # First call has serviceTier
        first_kwargs = service.client.invoke_model_with_response_stream.call_args_list[0].kwargs
        assert first_kwargs["serviceTier"] == "reserved"
        # Retry call does NOT
        retry_kwargs = service.client.invoke_model_with_response_stream.call_args_list[1].kwargs
        assert "serviceTier" not in retry_kwargs

        # Events should still arrive via retry
        collected = []
        while not q.empty():
            collected.append(q.get_nowait())
        done_items = [item for item in collected if item[0] == "done"]
        assert len(done_items) == 1
