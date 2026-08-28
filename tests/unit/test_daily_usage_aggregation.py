"""Daily (per-day, per-model) usage aggregation behavior.

Buckets are keyed by the *resolved Bedrock model ID*: recorded model IDs
(Anthropic aliases) are mapped via the custom mapping cache, then the default
config mapping, before grouping — so aliases of the same Bedrock model
aggregate together.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from app.db.dynamodb import UsageStatsManager


def _manager(items):
    """Build a UsageStatsManager whose usage_table.query returns `items`."""
    manager = UsageStatsManager.__new__(UsageStatsManager)
    manager.usage_table = MagicMock()
    manager.usage_table.query.return_value = {"Items": items}
    return manager


# 2026-06-20 00:00:00 UTC and 2026-06-21 12:00:00 UTC in ms.
TS_DAY1 = 1781913600000  # 2026-06-20T00:00:00Z
TS_DAY2 = 1782043200000  # 2026-06-21T12:00:00Z

# Default-config mapping targets (see settings.default_model_mapping).
FABLE_BEDROCK_ID = "global.anthropic.claude-fable-5"
OPUS_BEDROCK_ID = "global.anthropic.claude-opus-4-8"


def test_buckets_by_utc_date_and_bedrock_model_id():
    manager = _manager([
        {"timestamp": str(TS_DAY1), "model": "claude-fable-5",
         "input_tokens": 100, "output_tokens": 50, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
        {"timestamp": str(TS_DAY1), "model": "claude-opus-4-8",
         "input_tokens": 200, "output_tokens": 20, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
        {"timestamp": str(TS_DAY2), "model": "claude-fable-5",
         "input_tokens": 10, "output_tokens": 5, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
    ])

    buckets = manager.aggregate_daily_usage(["sk-test"], since_timestamp=0)

    assert set(buckets.keys()) == {"2026-06-20", "2026-06-21"}
    # Anthropic aliases are resolved to Bedrock model IDs before bucketing.
    assert set(buckets["2026-06-20"].keys()) == {FABLE_BEDROCK_ID, OPUS_BEDROCK_ID}
    assert buckets["2026-06-20"][FABLE_BEDROCK_ID]["tokens"] == 150
    assert buckets["2026-06-20"][FABLE_BEDROCK_ID]["input_tokens"] == 100
    assert buckets["2026-06-20"][FABLE_BEDROCK_ID]["output_tokens"] == 50
    assert buckets["2026-06-20"][FABLE_BEDROCK_ID]["requests"] == 1
    assert buckets["2026-06-20"][OPUS_BEDROCK_ID]["tokens"] == 220
    assert buckets["2026-06-21"][FABLE_BEDROCK_ID]["tokens"] == 15


def test_aliases_of_same_bedrock_model_merge():
    """Different aliases mapping to one Bedrock model aggregate into one entry."""
    manager = _manager([
        {"timestamp": str(TS_DAY1), "model": "claude-fable-5",
         "input_tokens": 100, "output_tokens": 50, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
        {"timestamp": str(TS_DAY1), "model": "claude-fable-5[1m]",
         "input_tokens": 40, "output_tokens": 10, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
        # Already a Bedrock ID — identity-resolved into the same bucket.
        {"timestamp": str(TS_DAY1), "model": FABLE_BEDROCK_ID,
         "input_tokens": 5, "output_tokens": 5, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
    ])

    buckets = manager.aggregate_daily_usage(["sk-test"], since_timestamp=0)

    assert set(buckets["2026-06-20"].keys()) == {FABLE_BEDROCK_ID}
    entry = buckets["2026-06-20"][FABLE_BEDROCK_ID]
    assert entry["input_tokens"] == 145
    assert entry["output_tokens"] == 65
    assert entry["tokens"] == 210
    assert entry["requests"] == 3


def test_same_model_same_day_accumulates():
    manager = _manager([
        {"timestamp": str(TS_DAY1), "model": "claude-fable-5",
         "input_tokens": 100, "output_tokens": 50, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
        {"timestamp": str(TS_DAY1 + 1000), "model": "claude-fable-5",
         "input_tokens": 30, "output_tokens": 10, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
    ])

    buckets = manager.aggregate_daily_usage(["sk-test"], since_timestamp=0)

    entry = buckets["2026-06-20"][FABLE_BEDROCK_ID]
    assert entry["input_tokens"] == 130
    assert entry["output_tokens"] == 60
    assert entry["tokens"] == 190
    assert entry["requests"] == 2


def test_cost_matches_pricing_and_fable_rates():
    """Cost reuses the shared _record_cost formula: input/output per 1M tokens."""
    manager = _manager([
        {"timestamp": str(TS_DAY1), "model": "claude-fable-5",
         "input_tokens": 1_000_000, "output_tokens": 1_000_000, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
    ])
    pricing_cache = {
        "global.anthropic.claude-fable-5": {
            "input_price": Decimal("10.00"),
            "output_price": Decimal("50.00"),
            "cache_read_price": Decimal("1.00"),
            "cache_write_price": Decimal("12.50"),
        }
    }
    model_mapping_cache = {"claude-fable-5": "global.anthropic.claude-fable-5"}

    buckets = manager.aggregate_daily_usage(
        ["sk-test"], since_timestamp=0,
        pricing_cache=pricing_cache, model_mapping_cache=model_mapping_cache,
    )

    # 1M input @ $10 + 1M output @ $50 = $60.00
    assert abs(buckets["2026-06-20"][FABLE_BEDROCK_ID]["cost"] - 60.0) < 1e-9


def test_cost_applies_service_tier_multiplier():
    manager = _manager([
        {"timestamp": str(TS_DAY1), "model": "claude-fable-5",
         "input_tokens": 1_000_000, "output_tokens": 0, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
    ])
    pricing_cache = {
        "global.anthropic.claude-fable-5": {
            "input_price": Decimal("10.00"),
            "output_price": Decimal("50.00"),
            "cache_read_price": Decimal("1.00"),
            "cache_write_price": Decimal("12.50"),
        }
    }

    buckets = manager.aggregate_daily_usage(
        ["sk-priority"],
        since_timestamp=0,
        pricing_cache=pricing_cache,
        model_mapping_cache={"claude-fable-5": "global.anthropic.claude-fable-5"},
        service_tier_cache={"sk-priority": "priority"},
    )

    # Priority tier is a 1.75x markup: $10 base input cost -> $17.50.
    assert abs(buckets["2026-06-20"][FABLE_BEDROCK_ID]["cost"] - 17.5) < 1e-9


def test_1h_cache_write_priced_at_2x_input():
    manager = _manager([
        {"timestamp": str(TS_DAY1), "model": "claude-fable-5",
         "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
         "cache_write_input_tokens": 1_000_000, "cache_ttl": "1h", "metadata": {}},
    ])
    pricing_cache = {
        "global.anthropic.claude-fable-5": {
            "input_price": Decimal("10.00"),
            "output_price": Decimal("50.00"),
            "cache_read_price": Decimal("1.00"),
            "cache_write_price": Decimal("12.50"),
        }
    }
    buckets = manager.aggregate_daily_usage(
        ["sk-test"], since_timestamp=0,
        pricing_cache=pricing_cache,
        model_mapping_cache={"claude-fable-5": "global.anthropic.claude-fable-5"},
    )
    # 1h cache-write = 2.0x input price = $20/1M  ->  1M tokens = $20
    assert abs(buckets["2026-06-20"][FABLE_BEDROCK_ID]["cost"] - 20.0) < 1e-9


def test_openai_cache_inclusive_input_normalized():
    """OpenAI-format records report input_tokens inclusive of cached; displayed
    tokens must subtract the cached subset (Anthropic convention)."""
    manager = _manager([
        {"timestamp": str(TS_DAY1), "model": "openai.gpt-oss-120b",
         "input_tokens": 100, "output_tokens": 50, "cached_tokens": 30,
         "cache_write_input_tokens": 0,
         "metadata": {"input_tokens_include_cached_tokens": True}},
    ])
    buckets = manager.aggregate_daily_usage(["sk-test"], since_timestamp=0)
    entry = buckets["2026-06-20"]["openai.gpt-oss-120b"]
    assert entry["input_tokens"] == 70   # 100 - 30 cached
    assert entry["cached_tokens"] == 30
    assert entry["tokens"] == 120         # 70 displayed input + 50 output


def test_missing_pricing_counts_tokens_zero_cost():
    manager = _manager([
        {"timestamp": str(TS_DAY1), "model": "unpriced-model",
         "input_tokens": 100, "output_tokens": 50, "cached_tokens": 0,
         "cache_write_input_tokens": 0, "metadata": {}},
    ])
    buckets = manager.aggregate_daily_usage(
        ["sk-test"], since_timestamp=0, pricing_cache={"some-other": {}},
    )
    entry = buckets["2026-06-20"]["unpriced-model"]
    assert entry["tokens"] == 150
    assert entry["cost"] == 0.0


def test_empty_usage_returns_empty_buckets():
    manager = _manager([])
    buckets = manager.aggregate_daily_usage(["sk-test"], since_timestamp=0)
    assert buckets == {}


# --- build_daily_usage_response (dashboard/API-key daily-usage panels) ---


def _entry(tokens=0, cost=0.0, requests=1):
    return {
        "input_tokens": tokens,
        "output_tokens": 0,
        "tokens": tokens,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "cost": cost,
        "requests": requests,
    }


def test_response_hides_zero_usage_model_entries():
    from admin_portal.backend.api.dashboard import build_daily_usage_response

    start = datetime(2026, 6, 20, tzinfo=timezone.utc)
    buckets = {
        "2026-06-20": {
            "global.anthropic.claude-fable-5": _entry(tokens=150, cost=1.5),
            # Zero tokens and zero cost (e.g. errored requests) — hidden.
            "zero-usage-model": _entry(tokens=0, cost=0.0, requests=3),
            # Tokens but no pricing configured — still shown.
            "unpriced-model": _entry(tokens=42, cost=0.0),
        }
    }

    resp = build_daily_usage_response(buckets, start, start, days=1)

    models = {m.model for m in resp.daily[0].models}
    assert models == {"global.anthropic.claude-fable-5", "unpriced-model"}
    assert resp.daily[0].total_tokens == 192
    assert abs(resp.daily[0].total_cost - 1.5) < 1e-9


def test_response_keeps_zero_days_for_continuous_axis():
    """Empty days stay zero-filled (continuous chart axis), just with no models."""
    from admin_portal.backend.api.dashboard import build_daily_usage_response

    start = datetime(2026, 6, 20, tzinfo=timezone.utc)
    end = datetime(2026, 6, 22, tzinfo=timezone.utc)
    buckets = {
        "2026-06-21": {"global.anthropic.claude-fable-5": _entry(tokens=10, cost=0.1)},
    }

    resp = build_daily_usage_response(buckets, start, end, days=3)

    assert [d.date for d in resp.daily] == ["2026-06-20", "2026-06-21", "2026-06-22"]
    assert resp.daily[0].models == []
    assert resp.daily[0].total_tokens == 0
    assert len(resp.daily[1].models) == 1
    assert resp.daily[2].models == []
