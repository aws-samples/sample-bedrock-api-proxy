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
    mod._get_usage_write_executor().submit(blocker)
    started.wait(timeout=5)

    future = tracker.record_usage_nowait(**USAGE_KWARGS)
    assert tracker.table.put_item.call_count == 0  # caller returned, no write yet

    release.set()
    future.result(timeout=5)
    assert tracker.table.put_item.call_count == 1


def test_nowait_swallows_write_errors(tracker):
    """A failed usage write is telemetry loss, never a request failure."""
    tracker.table.put_item.side_effect = RuntimeError("dynamo down")

    future = tracker.record_usage_nowait(**USAGE_KWARGS)
    future.result(timeout=5)  # must not raise

    assert future.exception() is None
