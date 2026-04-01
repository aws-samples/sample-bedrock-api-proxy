"""Tests for BedrockService multi-provider client cache."""
import threading
import time
from unittest.mock import MagicMock, patch

from app.services.bedrock_service import BedrockService


def _make_service():
    """Create a BedrockService with __init__ bypassed."""
    with patch.object(BedrockService, '__init__', lambda self, **kwargs: None):
        service = BedrockService.__new__(BedrockService)
        service.client = MagicMock(name="default_client")
        service._provider_clients = {}
        service._provider_clients_lock = threading.Lock()
        service._provider_client_ttl = 300
        service._provider_manager = None
        return service


def test_get_client_no_provider_returns_default():
    service = _make_service()
    result = service.get_client(provider_id=None)
    assert result is service.client


def test_get_client_with_provider_creates_and_caches():
    service = _make_service()
    mock_client = MagicMock(name="provider_client")
    service._create_provider_client = MagicMock(return_value=mock_client)

    result1 = service.get_client(provider_id="prov-123")
    assert result1 is mock_client
    service._create_provider_client.assert_called_once_with("prov-123")

    result2 = service.get_client(provider_id="prov-123")
    assert result2 is mock_client
    assert service._create_provider_client.call_count == 1


def test_get_client_ttl_expired_recreates():
    service = _make_service()
    service._provider_client_ttl = 0  # expire immediately

    mock_client1 = MagicMock(name="client1")
    mock_client2 = MagicMock(name="client2")
    service._create_provider_client = MagicMock(side_effect=[mock_client1, mock_client2])

    result1 = service.get_client(provider_id="prov-123")
    assert result1 is mock_client1

    # TTL=0, so next call should recreate
    result2 = service.get_client(provider_id="prov-123")
    assert result2 is mock_client2
    assert service._create_provider_client.call_count == 2


def test_invalidate_provider_client():
    service = _make_service()
    service._provider_clients = {"prov-123": (MagicMock(), time.time())}
    service.invalidate_provider_client("prov-123")
    assert "prov-123" not in service._provider_clients


def test_invalidate_nonexistent_is_noop():
    service = _make_service()
    service.invalidate_provider_client("nonexistent")  # should not raise
