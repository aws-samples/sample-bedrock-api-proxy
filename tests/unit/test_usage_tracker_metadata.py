"""UsageTracker injects resolved model into metadata for inference profiles."""
from unittest.mock import MagicMock

import pytest


APP_PROFILE_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/abcd"
)
UNDERLYING = (
    "arn:aws:bedrock:us-east-1::foundation-model/"
    "anthropic.claude-sonnet-4-5-20250929-v1:0"
)


@pytest.fixture
def tracker():
    from app.db.dynamodb import UsageTracker

    t = UsageTracker.__new__(UsageTracker)
    t.table = MagicMock()
    return t


def test_metadata_gets_resolved_model_for_profile(tracker, monkeypatch):
    from app.db import dynamodb as mod

    resolver = MagicMock()
    resolver.resolve.return_value = UNDERLYING
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: resolver)

    tracker.record_usage(
        api_key="k",
        request_id="r",
        model=APP_PROFILE_ARN,
        input_tokens=10,
        output_tokens=5,
    )

    _, kwargs = tracker.table.put_item.call_args
    item = kwargs["Item"]
    assert item["model"] == APP_PROFILE_ARN  # unchanged
    assert item["metadata"]["resolved_model"] == UNDERLYING


def test_metadata_unchanged_for_plain_model(tracker, monkeypatch):
    from app.db import dynamodb as mod

    resolver = MagicMock()
    resolver.resolve.return_value = "claude-sonnet-4-5-20250929"  # pass-through
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: resolver)

    tracker.record_usage(
        api_key="k",
        request_id="r",
        model="claude-sonnet-4-5-20250929",
        input_tokens=10,
        output_tokens=5,
    )

    _, kwargs = tracker.table.put_item.call_args
    item = kwargs["Item"]
    # resolved_model not added because it equals the original model
    assert "resolved_model" not in item["metadata"]


def test_resolver_failure_does_not_break_usage_recording(tracker, monkeypatch):
    from app.db import dynamodb as mod

    resolver = MagicMock()
    resolver.resolve.side_effect = RuntimeError("boom")
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: resolver)

    # Must not raise.
    tracker.record_usage(
        api_key="k",
        request_id="r",
        model=APP_PROFILE_ARN,
        input_tokens=10,
        output_tokens=5,
    )
    tracker.table.put_item.assert_called_once()
