"""Minimal thread-safe TTL cache shared by hot-path lookups.

Used to keep per-request DynamoDB reads (API key validation, model
mapping) off the request path. Deliberately small instead of pulling in
cachetools: get/set/invalidate with per-entry expiry and a hard entry
bound so client-controlled keys cannot grow memory without limit.
"""
import threading
import time
from typing import Any


class TTLCache:
    """Thread-safe key/value cache with per-entry TTL and a size bound.

    ``get`` returns ``(hit, value)`` so a cached ``None`` (negative
    caching) is distinguishable from a miss.

    Eviction: when full, expired entries are purged first; if the cache
    is still full, the soonest-expiring entries are dropped. Cache keys
    are client-controlled input, and negative entries carry the shortest
    TTLs — so spam evicts itself before it can evict hot positive
    entries. Entries are cheap to recompute (single DynamoDB reads), so
    bounded memory matters more than hit-rate precision.
    """

    def __init__(self, max_entries: int = 10_000):
        self._max_entries = max_entries
        self._entries: dict[Any, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> tuple[bool, Any]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False, None
            value, expires_at = entry
            if time.monotonic() >= expires_at:
                del self._entries[key]
                return False, None
            return True, value

    def set(self, key: Any, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            if key not in self._entries and len(self._entries) >= self._max_entries:
                self._evict_locked()
            self._entries[key] = (value, time.monotonic() + ttl_seconds)

    def invalidate(self, key: Any) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _evict_locked(self) -> None:
        """Purge expired entries; drop the soonest-expiring if still full."""
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._entries.items() if now >= exp]
        for k in expired:
            del self._entries[k]
        if len(self._entries) >= self._max_entries:
            n_drop = max(1, len(self._entries) // 10)
            by_expiry = sorted(self._entries.items(), key=lambda kv: kv[1][1])
            for k, _ in by_expiry[:n_drop]:
                del self._entries[k]
