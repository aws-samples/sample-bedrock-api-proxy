"""AuthMiddleware caches API key validation and keeps it off the event loop."""
import asyncio
import threading
from unittest.mock import MagicMock

import pytest
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


def test_manager_reraises_client_error_instead_of_returning_none():
    """DynamoDB throttling must surface as an error, not as "invalid key".

    If validate_api_key swallows ClientError and returns None, the
    middleware negative-caches valid keys for 5s during any DynamoDB
    throttling event — rolling 401 lockouts for legitimate traffic.
    """
    from botocore.exceptions import ClientError

    from app.db.dynamodb import APIKeyManager

    manager = APIKeyManager.__new__(APIKeyManager)
    manager.table = MagicMock()
    manager.table.get_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
        "GetItem",
    )

    with pytest.raises(ClientError):
        manager.validate_api_key("sk-valid")


def test_nested_mutation_does_not_poison_cache(monkeypatch):
    """DynamoDB items carry nested mutables (metadata); a handler mutating
    one must not leak into other requests' cached copies."""
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    middleware.api_key_manager.validate_api_key.return_value = {
        **VALID_INFO,
        "metadata": {"team": "alpha"},
    }

    async def scenario():
        _, info1 = await run_dispatch(middleware, "sk-valid")
        info1["metadata"]["team"] = "tampered"
        _, info2 = await run_dispatch(middleware, "sk-valid")
        return info2

    info2 = asyncio.run(scenario())
    assert info2["metadata"]["team"] == "alpha"


def test_validation_runs_on_dedicated_auth_executor(monkeypatch):
    """Auth must not share the default executor with multi-second work
    (Tavily web search, docker pulls) or cache misses queue behind them."""
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    seen = {}

    def record_thread(api_key):
        seen["name"] = threading.current_thread().name
        return VALID_INFO

    middleware.api_key_manager.validate_api_key.side_effect = record_thread
    asyncio.run(run_dispatch(middleware, "sk-valid"))

    assert seen["name"].startswith("auth-validate")


def test_concurrent_misses_share_one_lookup(monkeypatch):
    """Single-flight: when a hot key's entry expires, concurrent misses
    must coalesce into one DynamoDB read, not a thundering herd."""
    import time as time_mod

    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)

    def slow_validate(api_key):
        time_mod.sleep(0.2)
        return VALID_INFO

    middleware.api_key_manager.validate_api_key.side_effect = slow_validate

    async def scenario():
        results = await asyncio.gather(
            run_dispatch(middleware, "sk-valid"),
            run_dispatch(middleware, "sk-valid"),
            run_dispatch(middleware, "sk-valid"),
        )
        return results

    results = asyncio.run(scenario())

    assert all(r.status_code == 200 for r, _ in results)
    assert all(info["user_id"] == "u1" for _, info in results)
    assert middleware.api_key_manager.validate_api_key.call_count == 1


def gated_validate(gate):
    """validate_api_key that blocks in the executor until released."""

    def _validate(api_key):
        gate.wait(timeout=5)
        return VALID_INFO

    return _validate


def test_first_request_cancellation_does_not_break_coalesced_waiters(monkeypatch):
    """Single-flight must not turn one client disconnect into an outage.

    The request that starts the shared lookup is an ordinary request: it
    can be cancelled (client disconnect) mid-lookup. Awaiting that task
    unshielded would propagate the cancellation to every request
    coalesced behind it, so a single dropped connection fails all of them.
    """
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    gate = threading.Event()
    middleware.api_key_manager.validate_api_key.side_effect = gated_validate(gate)

    async def scenario():
        first = asyncio.create_task(run_dispatch(middleware, "sk-valid"))
        await asyncio.sleep(0.05)  # let `first` start the shared lookup
        others = [
            asyncio.create_task(run_dispatch(middleware, "sk-valid"))
            for _ in range(2)
        ]
        await asyncio.sleep(0.05)  # let them coalesce onto it
        first.cancel()  # client disconnect
        gate.set()
        return await asyncio.gather(first, *others, return_exceptions=True)

    first_outcome, *other_outcomes = asyncio.run(scenario())

    assert isinstance(first_outcome, asyncio.CancelledError)
    for response, info in other_outcomes:
        assert response.status_code == 200
        assert info["user_id"] == "u1"
    assert middleware.api_key_manager.validate_api_key.call_count == 1


def test_waiter_cancellation_does_not_break_the_shared_lookup(monkeypatch):
    """The mirror case: a coalesced request disconnecting must not cancel
    the shared lookup out from under the request that started it."""
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    gate = threading.Event()
    middleware.api_key_manager.validate_api_key.side_effect = gated_validate(gate)

    async def scenario():
        first = asyncio.create_task(run_dispatch(middleware, "sk-valid"))
        await asyncio.sleep(0.05)
        waiter = asyncio.create_task(run_dispatch(middleware, "sk-valid"))
        await asyncio.sleep(0.05)
        waiter.cancel()
        gate.set()
        return await asyncio.gather(first, waiter, return_exceptions=True)

    first_outcome, waiter_outcome = asyncio.run(scenario())

    assert isinstance(waiter_outcome, asyncio.CancelledError)
    response, info = first_outcome
    assert response.status_code == 200
    assert info["user_id"] == "u1"
    assert middleware.api_key_manager.validate_api_key.call_count == 1


def test_cancelled_requests_still_populate_the_cache(monkeypatch):
    """The shared lookup owns the cache write, so a DynamoDB read already
    paid for is not discarded when its requester disappears."""
    middleware = make_middleware(monkeypatch, cache_ttl_seconds=60)
    gate = threading.Event()
    middleware.api_key_manager.validate_api_key.side_effect = gated_validate(gate)

    async def scenario():
        request = asyncio.create_task(run_dispatch(middleware, "sk-valid"))
        await asyncio.sleep(0.05)
        request.cancel()
        gate.set()
        await asyncio.gather(request, return_exceptions=True)
        for _ in range(100):  # let the shielded lookup finish and cache
            if len(middleware._cache):
                break
            await asyncio.sleep(0.01)
        return await run_dispatch(middleware, "sk-valid")

    response, info = asyncio.run(scenario())

    assert response.status_code == 200
    assert info["user_id"] == "u1"
    assert middleware.api_key_manager.validate_api_key.call_count == 1
