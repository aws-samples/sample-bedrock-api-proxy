"""
In-memory session-to-trace mapping for agent loop aggregation.

Maps session IDs to OTEL trace contexts so that multiple HTTP requests
in the same agent loop share a single trace in the observability backend.
"""
import threading
import time
from typing import Any, Dict, Optional, Tuple


class SessionTraceStore:
    """Thread-safe store mapping session_id → (trace_id, span_id, turn_count, root_span, timestamp)."""

    def __init__(self, ttl_seconds: int = 600):
        self._store: Dict[str, Tuple[int, int, int, Any, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def get(self, session_id: str) -> Optional[Tuple[int, int, int, Any]]:
        """Get stored (trace_id, span_id, turn_count, root_span) for a session, or None if expired/missing."""
        with self._lock:
            self._cleanup()
            entry = self._store.get(session_id)
            if entry is None:
                return None
            trace_id, span_id, turn_count, root_span, ts = entry
            if time.time() - ts > self._ttl:
                # End the root span on expiry
                if root_span is not None:
                    try:
                        root_span.end()
                    except Exception:
                        pass
                del self._store[session_id]
                return None
            # Update timestamp on access
            self._store[session_id] = (trace_id, span_id, turn_count, root_span, time.time())
            return (trace_id, span_id, turn_count, root_span)

    def put(self, session_id: str, trace_id: int, span_id: int, root_span: Any = None) -> None:
        """Store trace context for a session with turn_count=0."""
        with self._lock:
            # Only store if not already present (first request wins)
            if session_id not in self._store:
                self._store[session_id] = (trace_id, span_id, 0, root_span, time.time())
            self._cleanup()

    def next_turn(self, session_id: str) -> int:
        """Atomically increment and return the new turn number for a session."""
        with self._lock:
            self._cleanup()
            entry = self._store.get(session_id)
            if entry is None:
                return 1
            trace_id, span_id, turn_count, root_span, ts = entry
            turn_count += 1
            self._store[session_id] = (trace_id, span_id, turn_count, root_span, time.time())
            return turn_count

    def _cleanup(self) -> None:
        """Remove expired entries (called under lock)."""
        now = time.time()
        expired = [k for k, (_, _, _, _, ts) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            # End root spans on cleanup
            entry = self._store.get(k)
            if entry and entry[3] is not None:
                try:
                    entry[3].end()
                except Exception:
                    pass
            del self._store[k]


# Global singleton
_session_store = SessionTraceStore()


def get_session_store() -> SessionTraceStore:
    return _session_store
