"""The rate limiter must never log any part of a client API key."""

import logging

import pytest
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.core.config import settings
from app.core.redaction import key_fingerprint
from app.middleware.rate_limit import RateLimitMiddleware

API_KEY = "sk-test-0123456789abcdefghijklmnopqrstuvwxyz"


class _InjectKeyInfo(BaseHTTPMiddleware):
    """Stand-in for AuthMiddleware: attaches api_key_info to the request."""

    async def dispatch(self, request: Request, call_next):
        request.state.api_key_info = {
            "api_key": API_KEY,
            "user_id": "user-123",
            "is_master": False,
            "rate_limit": 1,
        }
        return await call_next(request)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    app = FastAPI()

    @app.get("/v1/ping")
    async def ping():
        return {"ok": True}

    # Starlette runs the last-added middleware first: inject, then rate-limit.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(_InjectKeyInfo)
    return TestClient(app, raise_server_exceptions=False)


def test_rate_limit_log_contains_no_key_material(client, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    assert client.get("/v1/ping").status_code == 200
    client.get("/v1/ping")  # bucket of 1 is now empty -> rate limited

    logged = caplog.text + "".join(capsys.readouterr())
    assert "Rate limit exceeded" in logged
    # No prefix of the key of any useful length may appear.
    for n in (8, 12, 20):
        assert API_KEY[:n] not in logged
    assert "user_id=user-123" in logged
    assert f"key_fp={key_fingerprint(API_KEY)}" in logged


def test_key_fingerprint_is_stable_and_not_a_prefix():
    fp = key_fingerprint(API_KEY)
    assert fp == key_fingerprint(API_KEY)
    assert len(fp) == 12
    assert not API_KEY.startswith(fp)
    assert key_fingerprint(None) == "none"
    assert key_fingerprint("") == "none"
