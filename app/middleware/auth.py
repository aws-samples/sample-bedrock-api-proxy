"""
Authentication middleware for API key validation.

Validates API keys from request headers and attaches user information to requests.

Validation results are cached in-process with a TTL so the hot path does
not pay a DynamoDB round trip per request, and cache misses run in a
dedicated worker thread pool so the synchronous boto3 call never blocks
the event loop (and never queues behind long-running default-executor
work like web search or docker pulls).
"""
import asyncio
import copy
import hashlib
import hmac
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

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

# Small dedicated pool for auth lookups. The event loop's default
# executor is shared with multi-second work elsewhere in this codebase
# (Tavily web search/fetch, docker pulls); auth is on the critical path
# of every request and must not queue behind those.
_auth_executor: ThreadPoolExecutor | None = None
_auth_executor_lock = threading.Lock()


def _get_auth_executor() -> ThreadPoolExecutor:
    global _auth_executor
    if _auth_executor is None:
        with _auth_executor_lock:
            if _auth_executor is None:
                _auth_executor = ThreadPoolExecutor(
                    max_workers=4, thread_name_prefix="auth-validate"
                )
    return _auth_executor


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
        cache_ttl_seconds: float | None = None,
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
        self._cache = TTLCache()
        # In-flight lookups by cache key (single-flight): only touched
        # from the event loop, so no lock is needed.
        self._inflight: dict[str, asyncio.Task] = {}

    async def _validate_api_key(self, api_key: str) -> dict | None:
        """Validate a key via cache, falling back to DynamoDB off-loop.

        The cache is keyed by a SHA-256 digest of the key, so plaintext
        credentials are not held as dict keys and attacker-supplied
        oversized "keys" cannot inflate per-entry memory. Values are
        deep-copied in and out so handlers mutating their
        ``api_key_info`` (including nested dicts) cannot poison the
        cache. Concurrent misses for the same key coalesce into one
        DynamoDB read (single-flight), and waiters ``shield`` that shared
        lookup: a client disconnecting mid-lookup must not cancel it and
        take every other coalesced request down with it. Validation
        errors are treated as invalid but never cached: a transient
        DynamoDB failure must not lock a good key out for the TTL window.
        """
        cache_key = hashlib.sha256(api_key.encode()).hexdigest()
        if self._cache_ttl > 0:
            hit, cached = self._cache.get(cache_key)
            if hit:
                return copy.deepcopy(cached) if cached is not None else None

        task = self._inflight.get(cache_key)
        if task is None:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._lookup_and_cache(api_key, cache_key))
            self._inflight[cache_key] = task

        # shield so cancelling *this* request (client disconnect) leaves
        # the shared lookup running for everyone else waiting on it.
        api_key_info = await asyncio.shield(task)
        return copy.deepcopy(api_key_info) if api_key_info is not None else None

    async def _lookup_and_cache(self, api_key: str, cache_key: str) -> dict | None:
        """Run one DynamoDB lookup, cache the outcome, never raise.

        Owns the cache write (and the in-flight slot) so the result is
        stored even if every waiting request is cancelled first, and
        returns ``None`` on failure instead of propagating: waiters see
        "invalid key" (401) and nothing is cached, so the next request
        retries.
        """
        try:
            try:
                api_key_info = await self._lookup(api_key)
            except Exception as e:
                print("\n[ERROR] Exception during API key validation")
                print(f"[ERROR] Type: {type(e).__name__}")
                print(f"[ERROR] Message: {str(e)}")
                import traceback
                print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")
                return None

            if self._cache_ttl > 0:
                if api_key_info:
                    self._cache.set(
                        cache_key, copy.deepcopy(api_key_info), self._cache_ttl
                    )
                else:
                    self._cache.set(
                        cache_key,
                        None,
                        min(self._cache_ttl, NEGATIVE_CACHE_TTL_SECONDS),
                    )
            return api_key_info
        finally:
            self._inflight.pop(cache_key, None)

    async def _lookup(self, api_key: str) -> dict | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _get_auth_executor(), self.api_key_manager.validate_api_key, api_key
        )

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
        API key information dictionary. When ``REQUIRE_API_KEY=False`` the
        middleware stores ``None``; this returns ``{}`` so handlers can call
        ``.get`` unconditionally while ``if api_key_info`` stays falsy.

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

    return request.state.api_key_info or {}
