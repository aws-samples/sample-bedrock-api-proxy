"""UsageTracker.record_usage_nowait writes usage without blocking the caller."""
import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tracker():
    from app.db.dynamodb import UsageTracker

    t = UsageTracker.__new__(UsageTracker)
    t.table = MagicMock()
    return t


USAGE_KWARGS = {
    "api_key": "k",
    "request_id": "r",
    "model": "claude-sonnet-4-5-20250929",
    "input_tokens": 10,
    "output_tokens": 5,
}


def test_nowait_eventually_writes_same_item(tracker):
    future = tracker.record_usage_nowait(**USAGE_KWARGS)
    future.result(timeout=5)

    _, kwargs = tracker.table.put_item.call_args
    item = kwargs["Item"]
    assert item["api_key"] == "k"
    assert item["request_id"] == "r"
    assert item["input_tokens"] == 10
    assert item["output_tokens"] == 5


def test_nowait_returns_before_write_happens(tracker):
    """The caller must not wait for the DynamoDB write."""
    from app.db import dynamodb as mod

    release = threading.Event()
    started = threading.Event()

    def blocker():
        started.set()
        release.wait(timeout=5)

    # Occupy the single writer thread so the pending write cannot run yet
    blocker_future = mod._get_usage_write_executor().submit(blocker)
    try:
        started.wait(timeout=5)

        future = tracker.record_usage_nowait(**USAGE_KWARGS)
        assert tracker.table.put_item.call_count == 0  # caller returned, no write yet
    finally:
        release.set()
    blocker_future.result(timeout=5)
    future.result(timeout=5)
    assert tracker.table.put_item.call_count == 1


def test_nowait_swallows_write_errors(tracker):
    """A failed usage write is telemetry loss, never a request failure."""
    tracker.table.put_item.side_effect = RuntimeError("dynamo down")

    future = tracker.record_usage_nowait(**USAGE_KWARGS)
    future.result(timeout=5)  # must not raise

    assert future.exception() is None


def test_nowait_rejects_unknown_kwargs_at_call_site(tracker):
    """A misspelled kwarg must raise at the call site, not become a
    TypeError swallowed inside the background thread (silent data loss)."""
    with pytest.raises(TypeError):
        tracker.record_usage_nowait(**USAGE_KWARGS, tokens_input=1)


def test_nowait_survives_shutdown_executor(tracker, monkeypatch):
    """Call sites invoke nowait inside except/finally blocks; a submit-time
    RuntimeError during shutdown must not mask the original exception."""
    from concurrent.futures import ThreadPoolExecutor

    from app.db import dynamodb as mod

    dead = ThreadPoolExecutor(max_workers=1)
    dead.shutdown()
    monkeypatch.setattr(mod, "_usage_write_executor", dead)

    future = tracker.record_usage_nowait(**USAGE_KWARGS)  # must not raise

    assert future.done()
    assert future.exception() is None
    assert tracker.table.put_item.call_count == 0


def test_nowait_drops_when_backlog_full(tracker, monkeypatch):
    """Backpressure: when DynamoDB hangs and the queue backs up, new writes
    are dropped (bounded memory) instead of accumulating without limit."""
    from app.db import dynamodb as mod

    monkeypatch.setattr(mod, "_MAX_PENDING_USAGE_WRITES", 0)

    future = tracker.record_usage_nowait(**USAGE_KWARGS)

    assert future.done()
    assert future.exception() is None
    assert tracker.table.put_item.call_count == 0


def test_nowait_timestamp_captured_at_submit_with_ms_precision(tracker, monkeypatch):
    """Timestamps must reflect when usage happened, not when the backlog
    drained, and carry ms precision so same-second rows don't overwrite
    each other (the usage table key is api_key + timestamp)."""
    from app.db import dynamodb as mod

    monkeypatch.setattr(mod.time, "time", lambda: 1234.567)
    future = tracker.record_usage_nowait(**USAGE_KWARGS)
    future.result(timeout=5)

    _, kwargs = tracker.table.put_item.call_args
    assert kwargs["Item"]["timestamp"] == "1234567"


def test_drain_usage_writes_flushes_pending(tracker):
    """The shutdown hook must be able to flush queued writes."""
    from app.db import dynamodb as mod

    tracker.record_usage_nowait(**USAGE_KWARGS)
    remaining = mod.drain_usage_writes(timeout=5)

    assert remaining == 0
    assert tracker.table.put_item.call_count == 1
