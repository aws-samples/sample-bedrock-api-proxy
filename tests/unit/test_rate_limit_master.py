"""
Tests for master API key rate limiting.

Verifies that the master key is subject to configurable rate limits
instead of bypassing rate limiting entirely (DoS mitigation).
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.middleware.rate_limit import RateLimitMiddleware, TokenBucket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(api_key_info: dict = None):
    """Build a mock Request with the given api_key_info on request.state."""
    request = MagicMock()
    request.url.path = "/v1/messages"
    state = MagicMock()
    state.api_key_info = api_key_info
    request.state = state
    return request


def _make_ok_response():
    """Build a mock response returned by call_next."""
    response = MagicMock()
    response.headers = {}
    return response


async def _run_dispatch(middleware, request):
    """
    Call the middleware's dispatch method with a mock call_next.

    Returns the response on success, or raises HTTPException on rate limit.
    """
    ok_response = _make_ok_response()
    call_next = AsyncMock(return_value=ok_response)
    return await middleware.dispatch(request, call_next)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests -- Master key rate limiting
# ---------------------------------------------------------------------------

class TestMasterKeyRateLimit:
    """Master key should be rate-limited according to master_key_rate_limit."""

    @patch("app.middleware.rate_limit.settings")
    def test_master_key_rate_limited_after_capacity(self, mock_settings):
        """Master key with configured limit gets 429 after that many requests."""
        limit = 5
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests = 1000
        mock_settings.rate_limit_window = 60
        mock_settings.master_key_rate_limit = limit

        app = MagicMock()
        middleware = RateLimitMiddleware(app)
        request = _make_request({"is_master": True, "api_key": "master-key-abc"})

        # First `limit` requests should succeed
        for i in range(limit):
            resp = _run(_run_dispatch(middleware, request))
            assert resp.headers.get("X-RateLimit-Limit") == str(limit), (
                f"Request {i+1} should succeed"
            )

        # Next request should be rate-limited (HTTPException with 429)
        with pytest.raises(HTTPException) as exc_info:
            _run(_run_dispatch(middleware, request))
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail["type"] == "rate_limit_error"

    @patch("app.middleware.rate_limit.settings")
    def test_master_key_unlimited_when_zero(self, mock_settings):
        """MASTER_KEY_RATE_LIMIT=0 means unlimited (backward compat)."""
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests = 1000
        mock_settings.rate_limit_window = 60
        mock_settings.master_key_rate_limit = 0

        app = MagicMock()
        middleware = RateLimitMiddleware(app)
        request = _make_request({"is_master": True, "api_key": "master-key-abc"})

        # Send many requests -- none should be blocked
        for i in range(50):
            resp = _run(_run_dispatch(middleware, request))
            # When unlimited, call_next is called directly -- response is the mock
            # No rate limit headers should be set (the mock response has empty headers dict)
            assert "X-RateLimit-Limit" not in resp.headers, (
                f"Request {i+1} should succeed with no rate limit headers when limit=0"
            )

    @patch("app.middleware.rate_limit.settings")
    def test_master_key_response_includes_rate_limit_headers(self, mock_settings):
        """Successful master key responses include X-RateLimit-* headers."""
        limit = 100
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests = 1000
        mock_settings.rate_limit_window = 60
        mock_settings.master_key_rate_limit = limit

        app = MagicMock()
        middleware = RateLimitMiddleware(app)
        request = _make_request({"is_master": True, "api_key": "master-key-abc"})

        resp = _run(_run_dispatch(middleware, request))

        assert resp.headers["X-RateLimit-Limit"] == str(limit)
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers

    @patch("app.middleware.rate_limit.settings")
    def test_master_key_429_includes_rate_limit_headers(self, mock_settings):
        """429 responses for master key include Retry-After and X-RateLimit-* headers."""
        limit = 1
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests = 1000
        mock_settings.rate_limit_window = 60
        mock_settings.master_key_rate_limit = limit

        app = MagicMock()
        middleware = RateLimitMiddleware(app)
        request = _make_request({"is_master": True, "api_key": "master-key-abc"})

        # Exhaust the bucket
        _run(_run_dispatch(middleware, request))

        # This should raise HTTPException with 429 and rate limit headers
        with pytest.raises(HTTPException) as exc_info:
            _run(_run_dispatch(middleware, request))

        exc = exc_info.value
        assert exc.status_code == 429
        assert exc.headers["Retry-After"]
        assert exc.headers["X-RateLimit-Limit"] == str(limit)
        assert exc.headers["X-RateLimit-Remaining"] == "0"
        assert "X-RateLimit-Reset" in exc.headers

    @patch("app.middleware.rate_limit.settings")
    def test_master_key_uses_dedicated_bucket(self, mock_settings):
        """Master key uses __master_key__ bucket, separate from normal key buckets."""
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests = 1000
        mock_settings.rate_limit_window = 60
        mock_settings.master_key_rate_limit = 10

        app = MagicMock()
        middleware = RateLimitMiddleware(app)
        request = _make_request({"is_master": True, "api_key": "master-key-abc"})

        _run(_run_dispatch(middleware, request))

        assert "__master_key__" in middleware.buckets
        assert middleware.buckets["__master_key__"].capacity == 10


# ---------------------------------------------------------------------------
# Tests -- Normal keys unaffected
# ---------------------------------------------------------------------------

class TestNormalKeyUnaffected:
    """Non-master keys should use their own rate limits, not master_key_rate_limit."""

    @patch("app.middleware.rate_limit.settings")
    def test_normal_key_uses_own_rate_limit(self, mock_settings):
        """Normal keys rate-limit at their own capacity, not master_key_rate_limit."""
        normal_limit = 3
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests = normal_limit
        mock_settings.rate_limit_window = 60
        mock_settings.master_key_rate_limit = 999

        app = MagicMock()
        middleware = RateLimitMiddleware(app)
        request = _make_request({
            "is_master": False,
            "api_key": "normal-key-xyz",
            "rate_limit": normal_limit,
        })

        # Should be able to send `normal_limit` requests
        for i in range(normal_limit):
            resp = _run(_run_dispatch(middleware, request))
            assert resp.headers["X-RateLimit-Limit"] == str(normal_limit), (
                f"Request {i+1} should succeed"
            )

        # Next should be rate-limited
        with pytest.raises(HTTPException) as exc_info:
            _run(_run_dispatch(middleware, request))
        assert exc_info.value.status_code == 429

    @patch("app.middleware.rate_limit.settings")
    def test_normal_key_headers_reflect_own_limit(self, mock_settings):
        """Non-master key responses show their own limit in X-RateLimit-Limit."""
        normal_limit = 50
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests = normal_limit
        mock_settings.rate_limit_window = 60
        mock_settings.master_key_rate_limit = 999

        app = MagicMock()
        middleware = RateLimitMiddleware(app)
        request = _make_request({
            "is_master": False,
            "api_key": "normal-key-xyz",
            "rate_limit": normal_limit,
        })

        resp = _run(_run_dispatch(middleware, request))
        assert resp.headers["X-RateLimit-Limit"] == str(normal_limit)

    @patch("app.middleware.rate_limit.settings")
    def test_normal_key_bucket_separate_from_master(self, mock_settings):
        """Normal key and master key use separate buckets."""
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests = 100
        mock_settings.rate_limit_window = 60
        mock_settings.master_key_rate_limit = 50

        app = MagicMock()
        middleware = RateLimitMiddleware(app)

        # Send a master key request
        master_req = _make_request({"is_master": True, "api_key": "master-key"})
        _run(_run_dispatch(middleware, master_req))

        # Send a normal key request
        normal_req = _make_request({
            "is_master": False,
            "api_key": "normal-key-xyz",
            "rate_limit": 100,
        })
        _run(_run_dispatch(middleware, normal_req))

        assert "__master_key__" in middleware.buckets
        assert "normal-key-xyz" in middleware.buckets
        assert middleware.buckets["__master_key__"].capacity == 50
        assert middleware.buckets["normal-key-xyz"].capacity == 100


# ---------------------------------------------------------------------------
# Tests -- TokenBucket standalone
# ---------------------------------------------------------------------------

class TestTokenBucketUnit:
    """Standalone unit tests for TokenBucket to validate consume/refill logic."""

    def test_consume_within_capacity(self):
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        for _ in range(5):
            assert bucket.consume(1)
        assert not bucket.consume(1)

    def test_refill_over_time(self):
        bucket = TokenBucket(capacity=2, refill_rate=100.0)
        assert bucket.consume(1)
        assert bucket.consume(1)
        assert not bucket.consume(1)

        # Simulate time passing so tokens refill
        bucket.last_refill -= 1.0  # 1 second ago -> adds 100 tokens, capped at 2
        assert bucket.consume(1)

    def test_get_time_until_available(self):
        bucket = TokenBucket(capacity=1, refill_rate=1.0)
        bucket.consume(1)
        wait = bucket.get_time_until_available(1)
        assert wait > 0

    def test_capacity_is_ceiling(self):
        """Tokens never exceed capacity even after long refill period."""
        bucket = TokenBucket(capacity=3, refill_rate=100.0)
        bucket.last_refill -= 1000.0  # way in the past
        assert bucket.get_available_tokens() == 3
