"""
Tests for admin portal authentication bypass security hardening.

Verifies that:
- CognitoAuthMiddleware returns 503 when Cognito is not configured and dev mode is off
- CognitoAuthMiddleware allows access with dev-user info when dev mode is on
- MasterKeyAuthMiddleware returns 503 when master key is unset and dev mode is off
- MasterKeyAuthMiddleware allows access when dev mode is on
- MasterKeyAuthMiddleware uses timing-safe comparison
"""

import hmac
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import AsyncClient, ASGITransport

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers to build minimal FastAPI apps with each middleware
# ---------------------------------------------------------------------------

def _build_cognito_app(env_overrides: dict) -> FastAPI:
    """Build a minimal FastAPI app with CognitoAuthMiddleware."""
    with patch.dict(os.environ, env_overrides, clear=False):
        # Remove Cognito env vars to simulate unconfigured state unless overridden
        env = os.environ.copy()
        for key in ("COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID"):
            if key not in env_overrides:
                env.pop(key, None)

        with patch.dict(os.environ, env, clear=True):
            # Import fresh to pick up env vars
            from admin_portal.backend.middleware.cognito_auth import CognitoAuthMiddleware

            app = FastAPI()
            app.add_middleware(CognitoAuthMiddleware)

            @app.get("/api/test")
            async def test_endpoint(request: Request):
                user = getattr(request.state, "user", None)
                return JSONResponse(content={"status": "ok", "user": user})

            return app


def _build_master_key_app(master_api_key: str, env_overrides: dict) -> FastAPI:
    """Build a minimal FastAPI app with MasterKeyAuthMiddleware."""
    from admin_portal.backend.middleware.auth import MasterKeyAuthMiddleware

    app = FastAPI()
    app.add_middleware(MasterKeyAuthMiddleware)

    @app.get("/api/test")
    async def test_endpoint(request: Request):
        return JSONResponse(content={"status": "ok"})

    return app


# ---------------------------------------------------------------------------
# CognitoAuthMiddleware tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cognito_unconfigured_dev_mode_off_returns_503():
    """When Cognito is not configured and ADMIN_DEV_MODE is off, return 503."""
    # Clear Cognito env vars and set dev mode off
    env = {
        "ADMIN_DEV_MODE": "false",
        "AWS_REGION": "us-east-1",
    }
    # Remove any existing Cognito config
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID", "ADMIN_DEV_MODE")}
    clean_env.update(env)

    with patch.dict(os.environ, clean_env, clear=True):
        from admin_portal.backend.middleware.cognito_auth import CognitoAuthMiddleware

        app = FastAPI()
        app.add_middleware(CognitoAuthMiddleware)

        @app.get("/api/test")
        async def test_endpoint(request: Request):
            return JSONResponse(content={"status": "ok"})

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/test")

        assert response.status_code == 503
        body = response.json()
        assert body["error"] == "auth_not_configured"
        assert "ADMIN_DEV_MODE" in body["message"]


@pytest.mark.asyncio
async def test_cognito_unconfigured_dev_mode_on_returns_200():
    """When Cognito is not configured but ADMIN_DEV_MODE is on, allow access with dev-user."""
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID", "ADMIN_DEV_MODE")}
    clean_env["ADMIN_DEV_MODE"] = "true"
    clean_env["AWS_REGION"] = "us-east-1"

    with patch.dict(os.environ, clean_env, clear=True):
        from admin_portal.backend.middleware.cognito_auth import CognitoAuthMiddleware

        app = FastAPI()
        app.add_middleware(CognitoAuthMiddleware)

        @app.get("/api/test")
        async def test_endpoint(request: Request):
            user = getattr(request.state, "user", None)
            return JSONResponse(content={"status": "ok", "user": user})

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/test")

        assert response.status_code == 200
        body = response.json()
        assert body["user"]["username"] == "dev-user"
        assert body["user"]["development_mode"] is True


# ---------------------------------------------------------------------------
# MasterKeyAuthMiddleware tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_master_key_unset_dev_mode_off_returns_503():
    """When master key is unset and ADMIN_DEV_MODE is off, return 503."""
    with patch("admin_portal.backend.middleware.auth.settings") as mock_settings:
        mock_settings.master_api_key = None

        clean_env = {k: v for k, v in os.environ.items() if k != "ADMIN_DEV_MODE"}
        clean_env["ADMIN_DEV_MODE"] = "false"

        with patch.dict(os.environ, clean_env, clear=True):
            from admin_portal.backend.middleware.auth import MasterKeyAuthMiddleware

            app = FastAPI()
            app.add_middleware(MasterKeyAuthMiddleware)

            @app.get("/api/test")
            async def test_endpoint(request: Request):
                return JSONResponse(content={"status": "ok"})

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/test")

            assert response.status_code == 503
            body = response.json()
            assert body["error"] == "auth_not_configured"
            assert "MASTER_API_KEY" in body["message"]


@pytest.mark.asyncio
async def test_master_key_unset_dev_mode_on_allows_access():
    """When master key is unset but ADMIN_DEV_MODE is on, allow access."""
    with patch("admin_portal.backend.middleware.auth.settings") as mock_settings:
        mock_settings.master_api_key = None

        clean_env = {k: v for k, v in os.environ.items() if k != "ADMIN_DEV_MODE"}
        clean_env["ADMIN_DEV_MODE"] = "true"

        with patch.dict(os.environ, clean_env, clear=True):
            from admin_portal.backend.middleware.auth import MasterKeyAuthMiddleware

            app = FastAPI()
            app.add_middleware(MasterKeyAuthMiddleware)

            @app.get("/api/test")
            async def test_endpoint(request: Request):
                return JSONResponse(content={"status": "ok"})

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/test")

            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_master_key_timing_safe_comparison():
    """MasterKeyAuthMiddleware uses hmac.compare_digest for timing-safe comparison."""
    correct_key = "super-secret-master-key-12345"

    with patch("admin_portal.backend.middleware.auth.settings") as mock_settings:
        mock_settings.master_api_key = correct_key

        from admin_portal.backend.middleware.auth import MasterKeyAuthMiddleware

        app = FastAPI()
        app.add_middleware(MasterKeyAuthMiddleware)

        @app.get("/api/test")
        async def test_endpoint(request: Request):
            return JSONResponse(content={"status": "ok"})

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Correct key should succeed
            response = await client.get(
                "/api/test",
                headers={"x-admin-key": correct_key},
            )
            assert response.status_code == 200

            # Wrong key should fail
            response = await client.get(
                "/api/test",
                headers={"x-admin-key": "wrong-key"},
            )
            assert response.status_code == 401
            assert response.json()["error"] == "invalid_admin_key"

            # No key should fail
            response = await client.get("/api/test")
            assert response.status_code == 401
            assert response.json()["error"] == "authentication_required"


@pytest.mark.asyncio
async def test_master_key_uses_hmac_compare_digest():
    """Verify that hmac.compare_digest is actually called (not ==)."""
    correct_key = "test-key-abc"

    with patch("admin_portal.backend.middleware.auth.settings") as mock_settings:
        mock_settings.master_api_key = correct_key

        with patch("admin_portal.backend.middleware.auth.hmac") as mock_hmac:
            mock_hmac.compare_digest.return_value = True

            from admin_portal.backend.middleware.auth import MasterKeyAuthMiddleware

            app = FastAPI()
            app.add_middleware(MasterKeyAuthMiddleware)

            @app.get("/api/test")
            async def test_endpoint(request: Request):
                return JSONResponse(content={"status": "ok"})

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/test",
                    headers={"x-admin-key": "any-key"},
                )

            # Verify hmac.compare_digest was called
            mock_hmac.compare_digest.assert_called_once_with("any-key", correct_key)
