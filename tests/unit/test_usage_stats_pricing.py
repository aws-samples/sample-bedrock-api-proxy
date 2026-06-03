"""Usage stats pricing behavior."""

from decimal import Decimal
from unittest.mock import MagicMock

from app.db.dynamodb import UsageStatsManager


def test_aggregate_usage_prices_cached_tokens_as_subset_of_input_tokens():
    manager = UsageStatsManager.__new__(UsageStatsManager)
    manager.usage_table = MagicMock()
    manager.usage_table.query.return_value = {
        "Items": [
            {
                "timestamp": "1000",
                "model": "openai.gpt-oss-120b",
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_tokens": 30,
                "cache_write_input_tokens": 0,
                "metadata": {"input_tokens_include_cached_tokens": True},
            }
        ]
    }

    result = manager.aggregate_usage_for_key(
        "sk-test",
        pricing_cache={
            "openai.gpt-oss-120b": {
                "input_price": Decimal("2.00"),
                "output_price": Decimal("8.00"),
                "cache_read_price": Decimal("0.20"),
                "cache_write_price": Decimal("2.50"),
            }
        },
    )

    expected_cost = ((70 * 2.00) + (50 * 8.00) + (30 * 0.20)) / 1_000_000
    # input_tokens is reported cache-inclusive by OpenAI APIs (flag set), so the
    # displayed total must subtract the cached subset to match the Anthropic
    # convention used elsewhere — otherwise cached is counted twice (once inside
    # input, once as cached). 100 - 30 cached = 70.
    assert result["total_input_tokens"] == 70
    assert result["total_cached_tokens"] == 30
    assert abs(result["total_cost"] - expected_cost) < 1e-12


def test_aggregate_usage_input_tokens_exclusive_when_flag_absent():
    """Native Anthropic records (no flag) keep input_tokens as-is (already cache-exclusive)."""
    manager = UsageStatsManager.__new__(UsageStatsManager)
    manager.usage_table = MagicMock()
    manager.usage_table.query.return_value = {
        "Items": [
            {
                "timestamp": "1000",
                "model": "claude-sonnet-4-5",
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_tokens": 30,
                "cache_write_input_tokens": 0,
                "metadata": {},
            }
        ]
    }

    result = manager.aggregate_usage_for_key("sk-test")

    assert result["total_input_tokens"] == 100
    assert result["total_cached_tokens"] == 30
