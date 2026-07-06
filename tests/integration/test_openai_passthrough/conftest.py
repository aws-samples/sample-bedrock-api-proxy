"""Shared fixtures for openai-passthrough integration tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from fastapi.testclient import TestClient


@pytest.fixture
def web_search_enabled(request):
    return getattr(request, "param", True)


@pytest.fixture
def mock_settings(monkeypatch, web_search_enabled):
    """Set the env so the passthrough router mounts and points at a fake mantle."""
    monkeypatch.setattr("app.core.config.settings.enable_openai_passthrough", True)
    monkeypatch.setattr("app.core.config.settings.openai_api_key", "bedrock-key-test")
    monkeypatch.setattr(
        "app.core.config.settings.openai_base_url", "https://mantle.test/v1"
    )
    monkeypatch.setattr("app.core.config.settings.require_api_key", True)
    monkeypatch.setattr("app.core.config.settings.master_api_key", "")
    monkeypatch.setattr(
        "app.core.config.settings.enable_web_search", web_search_enabled
    )


@pytest.fixture
def mock_api_key_manager():
    """Patch APIKeyManager so any non-empty key validates as user 'u1'."""
    manager = MagicMock()
    manager.validate_api_key.return_value = {
        "api_key": "sk-test",
        "user_id": "u1",
        "is_master": False,
        "rate_limit": None,
        "cache_ttl": None,
    }
    with patch("app.middleware.auth.APIKeyManager", return_value=manager):
        yield manager


@pytest.fixture
def mock_model_mapping_manager():
    """Patch ModelMappingManager to return None (no mapping) by default."""
    manager = MagicMock()
    manager.get_mapping.return_value = None
    with patch(
        "app.api.openai_passthrough.router.ModelMappingManager", return_value=manager
    ):
        yield manager


@pytest.fixture
def mock_usage_tracker():
    from app.db.dynamodb import UsageTracker

    # spec= so assertions fail if the real method is renamed or removed
    tracker = MagicMock(spec=UsageTracker)
    with patch("app.api.openai_passthrough.router.UsageTracker", return_value=tracker):
        yield tracker


@pytest.fixture
def mock_response_context_store():
    store = MagicMock()
    with patch(
        "app.api.openai_passthrough.router.get_response_context_store",
        return_value=store,
    ):
        yield store


@pytest.fixture
def mock_web_search_service():
    service = MagicMock()
    service.handle_request = AsyncMock()
    get_service = MagicMock(return_value=service)
    service.get_service_mock = get_service
    with patch(
        "app.api.openai_passthrough.router.get_web_search_service",
        get_service,
        create=True,
    ):
        yield service


@pytest.fixture
def mock_bedrock_service():
    service = MagicMock()
    constructor = MagicMock(return_value=service)
    service.constructor_mock = constructor
    with patch(
        "app.api.openai_passthrough.router.BedrockService",
        constructor,
        create=True,
    ):
        yield service


@pytest.fixture
def respx_mock():
    """respx mock router for httpx calls."""
    with respx.mock(
        base_url="https://mantle.test/v1", assert_all_called=False
    ) as router:
        yield router


@pytest.fixture
def client(
    mock_settings,
    mock_api_key_manager,
    mock_model_mapping_manager,
    mock_usage_tracker,
    mock_response_context_store,
    mock_web_search_service,
    mock_bedrock_service,
):
    """FastAPI TestClient with all mocks wired in.

    Imports inside the fixture so module-level settings reads happen after
    monkeypatching.
    """
    import importlib

    # Reset httpx singleton so it picks up the patched base URL
    from app.api.openai_passthrough.chat_responses_adapter import (
        reset_unsupported_param_cache_for_testing,
    )
    from app.api.openai_passthrough.client import reset_client_for_testing

    reset_client_for_testing()
    reset_unsupported_param_cache_for_testing()

    # Access the actual router MODULE (not the APIRouter instance) via sys.modules.
    # We must do this because app/api/openai_passthrough/__init__.py shadows the
    # submodule name with `from .router import router`, so
    # `import app.api.openai_passthrough.router` returns the APIRouter instance.
    import sys as _sys

    # Ensure the router module is loaded
    import app.api.openai_passthrough.router  # noqa: F401  (triggers module load)

    _router_module = _sys.modules["app.api.openai_passthrough.router"]

    # Reset DDB manager cache so each test gets fresh mock instances
    _router_module._ddb = None
    _router_module._mapping = None
    _router_module._usage = None
    _router_module._context_store = None
    _router_module._provider = None

    with patch(
        "app.api.openai_passthrough.router.DynamoDBClient", return_value=MagicMock()
    ):
        # Reload app.main so the conditional router mount re-evaluates with
        # settings.enable_openai_passthrough=True (set by mock_settings above).
        import app.main as _main_mod

        importlib.reload(_main_mod)

        # Reset again after reload (reload may reinitialise globals)
        _router_module._ddb = None
        _router_module._mapping = None
        _router_module._usage = None
        _router_module._context_store = None

        yield TestClient(_main_mod.app)
