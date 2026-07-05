"""
Authentication middleware for API key validation.

Validates API keys from request headers and attaches user information to requests.

Validation results are cached in-process with a TTL so the hot path does
not pay a DynamoDB round trip per request, and cache misses run in a
worker thread so the synchronous boto3 call never blocks the event loop.
"""
import asyncio
import hmac
from typing import Callable, Optional

from fastapi import HTTPException, Request, status
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.ttl_cache import TTLCache
from app.db.dynamodb import APIKeyManager, DynamoDBClient

# Invalid keys are cached only briefly: long enough to blunt brute-force
# spam against DynamoDB, short enough that a freshly created key works
# almost immediately.
NEGATIVE_CACHE_TTL_SECONDS = 5.0

# Bound on cached keys; api_key is client-controlled input, so the cache
# must not grow without limit under invalid-key spam.
MAX_CACHE_ENTRIES = 10_000


# API Key header scheme
api_key_header_scheme = APIKeyHeader(
    name=settings.api_key_header,
    auto_error=False,
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for API key authentication."""

    def __init__(
        self,
        app,
        dynamodb_client: DynamoDBClient,
        cache_ttl_seconds: Optional[float] = None,
    ):
        """
        Initialize auth middleware.

        Args:
            app: FastAPI application
            dynamodb_client: DynamoDB client instance
            cache_ttl_seconds: TTL for cached validation results.
                Defaults to ``settings.api_key_cache_ttl_seconds``;
                0 disables caching. Key changes made in another process
                (e.g. the admin portal) take up to this long to apply.
        """
        super().__init__(app)
        self.api_key_manager = APIKeyManager(dynamodb_client)
        if cache_ttl_seconds is None:
            cache_ttl_seconds = settings.api_key_cache_ttl_seconds
        self._cache_ttl = cache_ttl_seconds
        self._cache = TTLCache(max_entries=MAX_CACHE_ENTRIES)

    async def _validate_api_key(self, api_key: str) -> Optional[dict]:
        """Validate a key via cache, falling back to DynamoDB off-loop.

        Returns a copy of the cached info so handlers mutating their
        ``api_key_info`` cannot poison the cache. Validation errors are
        treated as invalid but never cached: a transient DynamoDB
        failure must not lock a good key out for the TTL window.
        """
        if self._cache_ttl > 0:
            hit, cached = self._cache.get(api_key)
            if hit:
                return dict(cached) if cached is not None else None

        try:
            api_key_info = await asyncio.to_thread(
                self.api_key_manager.validate_api_key, api_key
            )
        except Exception as e:
            print(f"\n[ERROR] Exception during API key validation")
            print(f"[ERROR] Type: {type(e).__name__}")
            print(f"[ERROR] Message: {str(e)}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")
            return None

        if self._cache_ttl > 0:
            if api_key_info:
                self._cache.set(api_key, dict(api_key_info), self._cache_ttl)
            else:
                self._cache.set(
                    api_key,
                    None,
                    min(self._cache_ttl, NEGATIVE_CACHE_TTL_SECONDS),
                )
        return api_key_info

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

        # Extract API key from header (x-api-key first, fall back to Authorization: Bearer)
        api_key = request.headers.get(settings.api_key_header)
        if not api_key:
            authz = request.headers.get("Authorization")
            if authz and authz.startswith("Bearer "):
                api_key = authz[len("Bearer "):].strip()

        if not api_key:
            print(f"[AUTH] Missing API key for {request.url.path}")
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "type": "error",
                    "error": {
                        "type": "authentication_error",
                        "message": f"Missing API key in {settings.api_key_header} or Authorization: Bearer header",
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

        # Validate API key (in-process cache, DynamoDB on miss)
        api_key_info = await self._validate_api_key(api_key)

        if not api_key_info:
            # Deliberately do not log the rejected key (or any derivative of
            # it). The 401 response is the operational signal; aggregate
            # rate-limit metrics are the right place to fingerprint abuse.
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
