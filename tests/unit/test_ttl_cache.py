"""Tests for the shared TTL cache utility."""
import threading

import pytest


@pytest.fixture
def cache():
    from app.core.ttl_cache import TTLCache

    return TTLCache(max_entries=4)


def test_miss_returns_no_hit(cache):
    hit, value = cache.get("absent")
    assert hit is False
    assert value is None


def test_set_then_get_returns_value(cache):
    cache.set("k", {"user": "u1"}, ttl_seconds=60)
    hit, value = cache.get("k")
    assert hit is True
    assert value == {"user": "u1"}


def test_cached_none_is_a_hit(cache):
    """Negative caching: a stored None must be distinguishable from a miss."""
    cache.set("invalid-key", None, ttl_seconds=5)
    hit, value = cache.get("invalid-key")
    assert hit is True
    assert value is None


def test_expired_entry_is_a_miss(cache, monkeypatch):
    from app.core import ttl_cache as mod

    now = 1000.0
    monkeypatch.setattr(mod.time, "monotonic", lambda: now)
    cache.set("k", "v", ttl_seconds=60)

    monkeypatch.setattr(mod.time, "monotonic", lambda: now + 61)
    hit, value = cache.get("k")
    assert hit is False
    assert value is None


def test_invalidate_removes_entry(cache):
    cache.set("k", "v", ttl_seconds=60)
    cache.invalidate("k")
    hit, _ = cache.get("k")
    assert hit is False


def test_invalidate_missing_key_is_noop(cache):
    cache.invalidate("never-set")  # must not raise


def test_eviction_purges_expired_before_clearing(cache, monkeypatch):
    """When full, expired entries are purged so fresh ones survive."""
    from app.core import ttl_cache as mod

    now = 1000.0
    monkeypatch.setattr(mod.time, "monotonic", lambda: now)
    cache.set("stale-1", "v", ttl_seconds=10)
    cache.set("stale-2", "v", ttl_seconds=10)
    cache.set("fresh", "v", ttl_seconds=600)

    # Advance past the stale TTLs, then fill to the max_entries=4 bound
    monkeypatch.setattr(mod.time, "monotonic", lambda: now + 30)
    cache.set("new-1", "v", ttl_seconds=600)
    cache.set("new-2", "v", ttl_seconds=600)  # triggers eviction of stale-*

    assert cache.get("fresh") == (True, "v")
    assert cache.get("new-1") == (True, "v")
    assert cache.get("new-2") == (True, "v")


def test_eviction_drops_soonest_expiring_when_nothing_expired(cache):
    """Overflow must not wipe hot entries: short-TTL (negative-cache spam)
    entries are dropped first, long-lived positive entries survive."""
    cache.set("short-1", "v", ttl_seconds=5)
    cache.set("short-2", "v", ttl_seconds=5)
    cache.set("long-1", "v", ttl_seconds=600)
    cache.set("long-2", "v", ttl_seconds=600)
    cache.set("overflow", "v", ttl_seconds=600)

    assert cache.get("overflow") == (True, "v")
    assert cache.get("long-1") == (True, "v")
    assert cache.get("long-2") == (True, "v")
    # Bound is respected: never more entries than max_entries
    assert len(cache) <= 4


def test_exactly_at_expiry_is_miss(cache, monkeypatch):
    """Pins the >= boundary: an entry is expired at exactly now + ttl."""
    from app.core import ttl_cache as mod

    now = 1000.0
    monkeypatch.setattr(mod.time, "monotonic", lambda: now)
    cache.set("k", "v", ttl_seconds=60)

    monkeypatch.setattr(mod.time, "monotonic", lambda: now + 60)
    assert cache.get("k") == (False, None)


def test_just_before_expiry_is_hit(cache, monkeypatch):
    from app.core import ttl_cache as mod

    now = 1000.0
    monkeypatch.setattr(mod.time, "monotonic", lambda: now)
    cache.set("k", "v", ttl_seconds=60)

    monkeypatch.setattr(mod.time, "monotonic", lambda: now + 59.999)
    assert cache.get("k") == (True, "v")


def test_concurrent_access_does_not_corrupt(cache):
    """Smoke test: concurrent set/get from threads must not raise."""
    errors = []

    def worker(n):
        try:
            for i in range(200):
                cache.set(f"k{n}-{i}", i, ttl_seconds=60)
                cache.get(f"k{n}-{i}")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
