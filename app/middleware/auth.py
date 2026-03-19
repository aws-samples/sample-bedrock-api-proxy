"""
Authentication middleware for API key validation.

Validates API keys from request headers and attaches user information to requests.
"""
import hmac
from typing import Callable

from fastapi import HTTPException, Request, status
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.db.dynamodb import APIKeyManager, DynamoDBClient


# API Key header scheme
api_key_header_scheme = APIKeyHeader(
    name=settings.api_key_header,
    auto_error=False,
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for API key authentication."""

    def __init__(self, app, dynamodb_client: DynamoDBClient):
        """
        Initialize auth middleware.

        Args:
            app: FastAPI application
            dynamodb_client: DynamoDB client instance
        """
        super().__init__(app)
        self.api_key_manager = APIKeyManager(dynamodb_client)

    async def dispatch(self, request: Request, call_next: Callable):
        """
        Process request and validate API key.

        Args:
            request: HTTP request
            call_next: Next middleware/handler

        Returns:
            HTTP response

        Raises:
            HTTPException: If authentication fails
        """
        # Skip authentication for health check and docs endpoints
        skip_auth_paths = ["/health", "/health/ptc", "/ready", "/liveness", "/docs", "/openapi.json", "/redoc", "/"]
        if request.url.path in skip_auth_paths:
            return await call_next(request)

        # Skip if API key is not required
        if not settings.require_api_key:
            request.state.api_key_info = None
            return await call_next(request)

        # Extract API key from header
        api_key = request.headers.get(settings.api_key_header)

        if not api_key:
            print(f"[AUTH] Missing API key for {request.url.path}")
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "type": "error",
                    "error": {
                        "type": "authentication_error",
                        "message": f"Missing API key in {settings.api_key_header} header",
                    },
                },
            )

        # Check master API key first (if configured)
        if settings.master_api_key and hmac.compare_digest(api_key, settings.master_api_key):
            request.state.api_key_info = {
                "api_key": api_key,
                "user_id": "master",
                "is_master": True,
                "rate_limit": None,  # No rate limit for master key
                "cache_ttl": None,
            }
            return await call_next(request)

        # Validate API key in DynamoDB
        try:
            api_key_info = self.api_key_manager.validate_api_key(api_key)
        except Exception as e:
            print(f"\n[ERROR] Exception during API key validation")
            print(f"[ERROR] Type: {type(e).__name__}")
            print(f"[ERROR] Message: {str(e)}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")
            api_key_info = None

        if not api_key_info:
            print(f"[AUTH] Invalid API key: {api_key[:20]}...")
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "type": "error",
                    "error": {
                        "type": "authentication_error",
                        "message": "Invalid API key",
                    },
                },
            )

        # Attach API key info to request state
        request.state.api_key_info = api_key_info

        # Process request
        response = await call_next(request)

        return response


async def get_api_key_info(request: Request) -> dict:
    """
    Dependency to extract API key info from request state.

    Args:
        request: HTTP request

    Returns:
        API key information dictionary

    Raises:
        HTTPException: If not authenticated
    """
    if not hasattr(request.state, "api_key_info"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "type": "authentication_error",
                "message": "Not authenticated",
            },
        )

    return request.state.api_key_info
