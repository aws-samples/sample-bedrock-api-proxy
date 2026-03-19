"""
Rate limiting middleware.

Implements token bucket rate limiting per API key using in-memory storage
with optional Redis backend for distributed systems.
"""
import time
from collections import defaultdict
from typing import Callable, Dict

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings


class TokenBucket:
    """Token bucket for rate limiting."""

    def __init__(self, capacity: int, refill_rate: float):
        """
        Initialize token bucket.

        Args:
            capacity: Maximum number of tokens
            refill_rate: Tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = time.time()

    def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens from bucket.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were consumed, False otherwise
        """
        self._refill()

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True

        return False

    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate

        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now

    def get_available_tokens(self) -> float:
        """
        Get current available tokens.

        Returns:
            Number of available tokens
        """
        self._refill()
        return self.tokens

    def get_time_until_available(self, tokens: int = 1) -> float:
        """
        Get time in seconds until tokens are available.

        Args:
            tokens: Number of tokens needed

        Returns:
            Seconds until tokens are available (0 if already available)
        """
        self._refill()

        if self.tokens >= tokens:
            return 0

        tokens_needed = tokens - self.tokens
        return tokens_needed / self.refill_rate


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting API requests."""

    def __init__(self, app):
        """
        Initialize rate limit middleware.

        Args:
            app: FastAPI application
        """
        super().__init__(app)
        self.buckets: Dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                capacity=settings.rate_limit_requests,
                refill_rate=settings.rate_limit_requests
                / settings.rate_limit_window,
            )
        )

    async def dispatch(self, request: Request, call_next: Callable):
        """
        Process request and check rate limits.

        Args:
            request: HTTP request
            call_next: Next middleware/handler

        Returns:
            HTTP response

        Raises:
            HTTPException: If rate limit exceeded
        """
        # Skip rate limiting if disabled
        if not settings.rate_limit_enabled:
            return await call_next(request)

        # Skip rate limiting for health check and docs
        if request.url.path in ["/health", "/docs", "/openapi.json", "/redoc"]:
            return await call_next(request)

        # Get API key info from request state (set by AuthMiddleware)
        api_key_info = getattr(request.state, "api_key_info", None)

        if not api_key_info:
            # If no API key info, skip rate limiting (auth will handle it)
            return await call_next(request)

        # Skip rate limiting for master key
        if api_key_info.get("is_master", False):
            return await call_next(request)

        # Get or create token bucket for this API key
        api_key = api_key_info.get("api_key")
        rate_limit = api_key_info.get("rate_limit", settings.rate_limit_requests)

        # Convert to int/float (DynamoDB returns Decimal which doesn't work with float operations)
        rate_limit = int(rate_limit) if rate_limit else settings.rate_limit_requests

        # Update bucket capacity if custom rate limit
        if api_key not in self.buckets:
            self.buckets[api_key] = TokenBucket(
                capacity=rate_limit,
                refill_rate=float(rate_limit) / float(settings.rate_limit_window),
            )

        bucket = self.buckets[api_key]

        # Try to consume a token
        if not bucket.consume(1):
            # Rate limit exceeded
            retry_after = int(bucket.get_time_until_available(1)) + 1

            print(f"[RATE_LIMIT] Rate limit exceeded for API key: {api_key[:20]}...")
            print(f"  - Limit: {rate_limit}")
            print(f"  - Retry after: {retry_after}s")

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "type": "rate_limit_error",
                    "message": f"Rate limit exceeded. Try again in {retry_after} seconds.",
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(rate_limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                },
            )

        # Add rate limit headers to response
        response = await call_next(request)

        response.headers["X-RateLimit-Limit"] = str(rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(
            int(bucket.get_available_tokens())
        )
        response.headers["X-RateLimit-Reset"] = str(
            int(time.time() + settings.rate_limit_window)
        )

        return response
