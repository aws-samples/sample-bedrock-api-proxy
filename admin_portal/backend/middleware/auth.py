"""
Master Key Authentication Middleware for Admin Portal.
"""
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
            # No master key configured - allow access (development mode)
            return await call_next(request)

        if not admin_key:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "authentication_required",
                    "message": "Admin key is required. Provide it in the x-admin-key header.",
                },
            )

        if admin_key != settings.master_api_key:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "invalid_admin_key",
                    "message": "Invalid admin key provided.",
                },
            )

        # Authentication successful
        return await call_next(request)
