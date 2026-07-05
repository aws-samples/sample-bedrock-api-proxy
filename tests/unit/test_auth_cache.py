"""AuthMiddleware caches API key validation and keeps it off the event loop."""
import asyncio
import threading
from unittest.mock import MagicMock

from starlette.requests import Request
from starlette.responses import PlainTextResponse

VALID_INFO = {"api_key": "sk-valid", "user_id": "u1", "is_active": True}


def make_middleware(monkeypatch, cache_ttl_seconds=60):
    from app.middleware import auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "require_api_key", True)
    monkeypatch.setattr(auth_mod.settings, "master_api_key", "sk-master-secret")

    middleware = auth_mod.AuthMiddleware(
        app=MagicMock(),
        dynamodb_client=MagicMock(),
        cache_ttl_seconds=cache_ttl_seconds,
    )
    middleware.api_key_manager = MagicMock()
    return middleware


def make_request(api_key=None, path="/v1/messages"):
    from app.core.config import settings

    headers = []
    if api_key is not None:
        headers.append((settings.api_key_header.lower().encode(), api_key.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


async def run_dispatch(middleware, api_key):
    """Dispatch one request; return (response, api_key_info seen by handler)."""
    seen = {}

    async def call_next(request):
        seen["info"] = request.state.api_key_info
        return PlainTextResponse("ok")

    response = await middleware.dispatch(make_request(api_key), call_next)
    return response, seen.get("info")


def test_valid_key_hits_dynamo_once_within_ttl(monkeypatch):
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    middleware.api_key_manager.validate_api_key.return_value = VALID_INFO

    async def scenario():
        r1, info1 = await run_dispatch(middleware, "sk-valid")
        r2, info2 = await run_dispatch(middleware, "sk-valid")
        return r1, info1, r2, info2

    r1, info1, r2, info2 = asyncio.run(scenario())

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert info1["user_id"] == "u1"
    assert info2["user_id"] == "u1"
    assert middleware.api_key_manager.validate_api_key.call_count == 1


def test_cache_expiry_revalidates(monkeypatch):
    from app.core import ttl_cache as ttl_mod

    now = 1000.0
    monkeypatch.setattr(ttl_mod.time, "monotonic", lambda: now)
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    middleware.api_key_manager.validate_api_key.return_value = VALID_INFO

    asyncio.run(run_dispatch(middleware, "sk-valid"))
    monkeypatch.setattr(ttl_mod.time, "monotonic", lambda: now + 61)
    asyncio.run(run_dispatch(middleware, "sk-valid"))

    assert middleware.api_key_manager.validate_api_key.call_count == 2


def test_invalid_key_negative_cached_briefly(monkeypatch):
    from app.core import ttl_cache as ttl_mod

    now = 1000.0
    monkeypatch.setattr(ttl_mod.time, "monotonic", lambda: now)
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    middleware.api_key_manager.validate_api_key.return_value = None

    r1, _ = asyncio.run(run_dispatch(middleware, "sk-bogus"))
    r2, _ = asyncio.run(run_dispatch(middleware, "sk-bogus"))
    assert r1.status_code == 401
    assert r2.status_code == 401
    # Second rejection served from the negative cache
    assert middleware.api_key_manager.validate_api_key.call_count == 1

    # Negative entries expire much sooner than positive ones (~5s, not 60s)
    monkeypatch.setattr(ttl_mod.time, "monotonic", lambda: now + 6)
    asyncio.run(run_dispatch(middleware, "sk-bogus"))
    assert middleware.api_key_manager.validate_api_key.call_count == 2


def test_ttl_zero_disables_cache(monkeypatch):
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=0)
    middleware.api_key_manager.validate_api_key.return_value = VALID_INFO

    asyncio.run(run_dispatch(middleware, "sk-valid"))
    asyncio.run(run_dispatch(middleware, "sk-valid"))

    assert middleware.api_key_manager.validate_api_key.call_count == 2


def test_validation_runs_off_event_loop(monkeypatch):
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    validation_thread = {}

    def record_thread(api_key):
        validation_thread["ident"] = threading.get_ident()
        return VALID_INFO

    middleware.api_key_manager.validate_api_key.side_effect = record_thread

    async def scenario():
        loop_thread = threading.get_ident()
        await run_dispatch(middleware, "sk-valid")
        return loop_thread

    loop_thread = asyncio.run(scenario())

    assert validation_thread["ident"] != loop_thread


def test_master_key_never_touches_dynamo(monkeypatch):
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)

    response, info = asyncio.run(run_dispatch(middleware, "sk-master-secret"))

    assert response.status_code == 200
    assert info["is_master"] is True
    middleware.api_key_manager.validate_api_key.assert_not_called()


def test_cached_info_not_shared_between_requests(monkeypatch):
    """A handler mutating its api_key_info must not poison the cache."""
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    middleware.api_key_manager.validate_api_key.return_value = dict(VALID_INFO)

    async def scenario():
        _, info1 = await run_dispatch(middleware, "sk-valid")
        info1["user_id"] = "tampered"
        _, info2 = await run_dispatch(middleware, "sk-valid")
        return info2

    info2 = asyncio.run(scenario())
    assert info2["user_id"] == "u1"


def test_validation_error_returns_401_but_is_not_cached(monkeypatch):
    """A transient DynamoDB failure must not lock a good key out via cache."""
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    middleware.api_key_manager.validate_api_key.side_effect = [
        RuntimeError("dynamo blip"),
        VALID_INFO,
    ]

    r1, _ = asyncio.run(run_dispatch(middleware, "sk-valid"))
    r2, info2 = asyncio.run(run_dispatch(middleware, "sk-valid"))

    assert r1.status_code == 401
    assert r2.status_code == 200
    assert info2["user_id"] == "u1"
    assert middleware.api_key_manager.validate_api_key.call_count == 2
