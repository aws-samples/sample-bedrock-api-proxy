"""Tests for the auth middleware's Authorization: Bearer support."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from app.middleware.auth import AuthMiddleware


@pytest.mark.asyncio
async def test_authorization_bearer_resolves_when_xapikey_missing(monkeypatch):
    """Authorization: Bearer <key> should authenticate when x-api-key is absent."""
    # Patch settings
    monkeypatch.setattr("app.core.config.settings.require_api_key", True)
    monkeypatch.setattr("app.core.config.settings.master_api_key", "")
    monkeypatch.setattr("app.core.config.settings.api_key_header", "x-api-key")

    # Create mock request with Authorization: Bearer header
    request = MagicMock(spec=Request)
    request.url.path = "/test"
    request.headers = Headers({"Authorization": "Bearer sk-abc"})
    request.state = MagicMock()

    # Mock the API key manager
    mock_manager = MagicMock()
    mock_manager.validate_api_key.return_value = {"user_id": "u1", "api_key": "sk-abc"}

    # Mock the call_next
    mock_call_next = AsyncMock()
    mock_call_next.return_value = MagicMock(status_code=200)

    # Create middleware with mocked APIKeyManager
    ddb_client = MagicMock()
    with patch("app.middleware.auth.APIKeyManager", return_value=mock_manager):
        middleware = AuthMiddleware(MagicMock(), dynamodb_client=ddb_client)

    # Call dispatch
    await middleware.dispatch(request, mock_call_next)

    # Verify the API key was extracted and validated
    mock_manager.validate_api_key.assert_called_once_with("sk-abc")
    assert request.state.api_key_info == {"user_id": "u1", "api_key": "sk-abc"}


@pytest.mark.asyncio
async def test_xapikey_takes_precedence_when_both_present(monkeypatch):
    """If both headers are present, x-api-key wins."""
    # Patch settings
    monkeypatch.setattr("app.core.config.settings.require_api_key", True)
    monkeypatch.setattr("app.core.config.settings.master_api_key", "")
    monkeypatch.setattr("app.core.config.settings.api_key_header", "x-api-key")

    # Create mock request with both headers
    request = MagicMock(spec=Request)
    request.url.path = "/test"
    request.headers = Headers({
        "x-api-key": "sk-from-xapikey",
        "Authorization": "Bearer sk-from-bearer"
    })
    request.state = MagicMock()

    # Mock the API key manager
    mock_manager = MagicMock()
    mock_manager.validate_api_key.return_value = {"user_id": "u1", "api_key": "sk-from-xapikey"}

    # Mock the call_next
    mock_call_next = AsyncMock()
    mock_call_next.return_value = MagicMock(status_code=200)

    # Create middleware with mocked APIKeyManager
    ddb_client = MagicMock()
    with patch("app.middleware.auth.APIKeyManager", return_value=mock_manager):
        middleware = AuthMiddleware(MagicMock(), dynamodb_client=ddb_client)

    # Call dispatch
    await middleware.dispatch(request, mock_call_next)

    # Verify x-api-key took precedence
    mock_manager.validate_api_key.assert_called_once_with("sk-from-xapikey")


@pytest.mark.asyncio
async def test_missing_both_headers_returns_401(monkeypatch):
    """Missing both headers should return 401."""
    # Patch settings
    monkeypatch.setattr("app.core.config.settings.require_api_key", True)
    monkeypatch.setattr("app.core.config.settings.master_api_key", "")
    monkeypatch.setattr("app.core.config.settings.api_key_header", "x-api-key")

    # Create mock request with no auth headers
    request = MagicMock(spec=Request)
    request.url.path = "/test"
    request.headers = Headers({})
    request.state = MagicMock()

    # Mock the call_next
    mock_call_next = AsyncMock()

    # Create middleware with mocked APIKeyManager
    ddb_client = MagicMock()
    with patch("app.middleware.auth.APIKeyManager", return_value=MagicMock()):
        middleware = AuthMiddleware(MagicMock(), dynamodb_client=ddb_client)

    # Call dispatch
    response = await middleware.dispatch(request, mock_call_next)

    # Verify 401 response
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key_does_not_leak_to_log(monkeypatch, capsys):
    """The 401 invalid-key path must not log any substring of the rejected key.

    Guards against CodeQL py/clear-text-logging-sensitive-data and any
    accidental future reintroduction of a key-derived log line.
    """
    monkeypatch.setattr("app.core.config.settings.require_api_key", True)
    monkeypatch.setattr("app.core.config.settings.master_api_key", "")
    monkeypatch.setattr("app.core.config.settings.api_key_header", "x-api-key")

    secret = "sk-deadbeef-DO-NOT-LEAK-1234567890abcdef"  # nosec B105 - fake test fixture value, not a real credential

    request = MagicMock(spec=Request)
    request.url.path = "/test"
    request.headers = Headers({"x-api-key": secret})
    request.state = MagicMock()

    mock_manager = MagicMock()
    mock_manager.validate_api_key.return_value = None  # invalid key path

    mock_call_next = AsyncMock()
    ddb_client = MagicMock()
    with patch("app.middleware.auth.APIKeyManager", return_value=mock_manager):
        middleware = AuthMiddleware(MagicMock(), dynamodb_client=ddb_client)
    response = await middleware.dispatch(request, mock_call_next)

    captured = capsys.readouterr().out + capsys.readouterr().err
    # No prefix of the key (length >= 4) should appear anywhere in the log
    for n in range(4, len(secret) + 1):
        assert secret[:n] not in captured, (
            f"log leaked prefix of length {n}: secret[:{n}]={secret[:n]!r}"
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_authorization_non_bearer_is_ignored(monkeypatch):
    """Authorization: Basic ... should not be treated as an API key."""
    # Patch settings
    monkeypatch.setattr("app.core.config.settings.require_api_key", True)
    monkeypatch.setattr("app.core.config.settings.master_api_key", "")
    monkeypatch.setattr("app.core.config.settings.api_key_header", "x-api-key")

    # Create mock request with Basic auth
    request = MagicMock(spec=Request)
    request.url.path = "/test"
    request.headers = Headers({"Authorization": "Basic dXNlcjpwYXNz"})
    request.state = MagicMock()

    # Mock the call_next
    mock_call_next = AsyncMock()

    # Create middleware with mocked APIKeyManager
    ddb_client = MagicMock()
    with patch("app.middleware.auth.APIKeyManager", return_value=MagicMock()):
        middleware = AuthMiddleware(MagicMock(), dynamodb_client=ddb_client)

    # Call dispatch
    response = await middleware.dispatch(request, mock_call_next)

    # Verify 401 response
    assert response.status_code == 401
