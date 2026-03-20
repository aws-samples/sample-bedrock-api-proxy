"""
Master Key Authentication Middleware for Admin Portal.
"""
import hmac
import os
import sys
from pathlib import Path
from typing import Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Add parent directory to path to import from app
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.core.config import settings


# Paths that don't require authentication
SKIP_AUTH_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/api/auth/login",
}


class MasterKeyAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to authenticate admin requests using master API key."""

    async def dispatch(self, request: Request, call_next: Callable):
        """Process the request and check authentication."""
        # Skip auth for certain paths
        if request.url.path in SKIP_AUTH_PATHS:
            return await call_next(request)

        # Skip auth for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Get admin key from header
        admin_key = request.headers.get("x-admin-key")

        # Validate master key
        if not settings.master_api_key:
            dev_mode = os.getenv("ADMIN_DEV_MODE", "false").lower() in ("true", "1", "yes")
            if dev_mode:
                # Explicit dev mode - allow access without master key
                return await call_next(request)
            else:
                # Master key not configured and dev mode not enabled - reject
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={
                        "error": "auth_not_configured",
                        "message": (
                            "MASTER_API_KEY is not configured. "
                            "Set MASTER_API_KEY, or set ADMIN_DEV_MODE=true for development."
                        ),
                    },
                )

        if not admin_key:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "authentication_required",
                    "message": "Admin key is required. Provide it in the x-admin-key header.",
                },
            )

        if not hmac.compare_digest(admin_key, settings.master_api_key):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "invalid_admin_key",
                    "message": "Invalid admin key provided.",
                },
            )

        # Authentication successful
        return await call_next(request)
