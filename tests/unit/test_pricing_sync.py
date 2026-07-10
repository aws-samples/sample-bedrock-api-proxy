"""Unit tests for the LiteLLM model-pricing sync service."""

from decimal import Decimal

from app.services.pricing_sync_service import (
    extract_bedrock_pricing,
    sync_pricing,
)

SAMPLE_LITELLM_DATA = {
    "sample_spec": {
        "input_cost_per_token": 0.0,
        "litellm_provider": "bedrock",
        "mode": "chat",
    },
    "anthropic.claude-sonnet-4-5-20250929-v1:0": {
        "litellm_provider": "bedrock_converse",
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "cache_read_input_token_cost": 3e-07,
        "cache_creation_input_token_cost": 3.75e-06,
    },
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": {
        "litellm_provider": "bedrock_converse",
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
    },
    "amazon.nova-pro-v1:0": {
        "litellm_provider": "bedrock_converse",
        "mode": "chat",
        "input_cost_per_token": 8e-07,
        "output_cost_per_token": 3.2e-06,
    },
    # OpenAI-format model reachable via /openai/v1: mantle prefix must be stripped
    "bedrock_mantle/openai.gpt-5.5": {
        "litellm_provider": "bedrock_mantle",
        "mode": "responses",
        "input_cost_per_token": 5.5e-06,
        "output_cost_per_token": 3.3e-05,
    },
    # Region-scoped LiteLLM alias, not a Bedrock model ID: skipped
    "bedrock/us-east-1/anthropic.claude-v2": {
        "litellm_provider": "bedrock",
        "mode": "chat",
        "input_cost_per_token": 8e-06,
        "output_cost_per_token": 2.4e-05,
    },
    # GCP-style alias: skipped
    "anthropic.claude-haiku-4-5@20251001": {
        "litellm_provider": "bedrock_converse",
        "mode": "chat",
        "input_cost_per_token": 1e-06,
        "output_cost_per_token": 5e-06,
    },
    # No vendor prefix: skipped
    "claude-sonnet-4-5-20250929-v1:0": {
        "litellm_provider": "bedrock",
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
    },
    # Wrong mode: skipped
    "amazon.titan-embed-text-v2:0": {
        "litellm_provider": "bedrock",
        "mode": "embedding",
        "input_cost_per_token": 2e-08,
        "output_cost_per_token": 0.0,
    },
    # Wrong provider: skipped
    "gpt-4o": {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 2.5e-06,
        "output_cost_per_token": 1e-05,
    },
    # Missing output cost: skipped
    "meta.llama3-8b-instruct-v1:0": {
        "litellm_provider": "bedrock",
        "mode": "chat",
        "input_cost_per_token": 3e-07,
    },
}

PROVIDERS = ["bedrock", "bedrock_converse", "bedrock_mantle"]


class FakePricingManager:
    """In-memory stand-in for ModelPricingManager."""

    def __init__(self, items=None):
        self.items = {item["model_id"]: dict(item) for item in (items or [])}
        self.created = []
        self.updated = []

    def create_pricing(
        self,
        model_id,
        provider,
        input_price,
        output_price,
        cache_read_price=None,
        cache_write_price=None,
        display_name=None,
        status="active",
        pricing_source=None,
    ):
        item = {
            "model_id": model_id,
            "provider": provider,
            "display_name": display_name or model_id,
            "input_price": input_price,
            "output_price": output_price,
            "cache_read_price": cache_read_price,
            "cache_write_price": cache_write_price,
            "status": status,
        }
        if pricing_source:
            item["pricing_source"] = pricing_source
        self.items[model_id] = item
        self.created.append(model_id)
        return item

    def update_pricing(self, model_id, **kwargs):
        self.items[model_id].update({k: v for k, v in kwargs.items() if v is not None})
        self.updated.append(model_id)
        return True

    def list_all_pricing(
        self, limit=100, last_key=None, provider_filter=None, status_filter=None
    ):
        return {
            "items": list(self.items.values()),
            "last_key": None,
            "count": len(self.items),
        }


def litellm_row(model_id, input_price, output_price, **extra):
    row = {
        "model_id": model_id,
        "provider": "Anthropic",
        "input_price": Decimal(str(input_price)),
        "output_price": Decimal(str(output_price)),
        "pricing_source": "litellm",
    }
    row.update(extra)
    return row


class TestExtractBedrockPricing:
    def test_extracts_and_converts_to_per_million(self):
        prices = extract_bedrock_pricing(SAMPLE_LITELLM_DATA, PROVIDERS)

        sonnet = prices["anthropic.claude-sonnet-4-5-20250929-v1:0"]
        assert sonnet["input_price"] == Decimal("3")
        assert sonnet["output_price"] == Decimal("15")
        assert sonnet["cache_read_price"] == Decimal("0.3")
        assert sonnet["cache_write_price"] == Decimal("3.75")

        # Cache prices absent in source stay None
        assert prices["amazon.nova-pro-v1:0"]["cache_read_price"] is None

    def test_keeps_region_prefixed_variants(self):
        prices = extract_bedrock_pricing(SAMPLE_LITELLM_DATA, PROVIDERS)
        assert "us.anthropic.claude-sonnet-4-5-20250929-v1:0" in prices

    def test_strips_mantle_prefix(self):
        prices = extract_bedrock_pricing(SAMPLE_LITELLM_DATA, PROVIDERS)
        assert prices["openai.gpt-5.5"]["input_price"] == Decimal("5.5")

    def test_skips_non_bedrock_aliases_and_modes(self):
        prices = extract_bedrock_pricing(SAMPLE_LITELLM_DATA, PROVIDERS)
        assert "bedrock/us-east-1/anthropic.claude-v2" not in prices
        assert "anthropic.claude-v2" not in prices  # slash alias must not leak through
        assert "anthropic.claude-haiku-4-5@20251001" not in prices
        assert "claude-sonnet-4-5-20250929-v1:0" not in prices
        assert "amazon.titan-embed-text-v2:0" not in prices
        assert "gpt-4o" not in prices
        assert "meta.llama3-8b-instruct-v1:0" not in prices

    def test_provider_filter(self):
        prices = extract_bedrock_pricing(SAMPLE_LITELLM_DATA, ["bedrock_mantle"])
        assert list(prices) == ["openai.gpt-5.5"]


class TestSyncPricing:
    def setup_method(self):
        self.prices = extract_bedrock_pricing(SAMPLE_LITELLM_DATA, PROVIDERS)

    def test_creates_missing_models(self):
        manager = FakePricingManager()
        summary = sync_pricing(manager, self.prices)

        assert set(summary["created"]) == set(self.prices)
        item = manager.items["anthropic.claude-sonnet-4-5-20250929-v1:0"]
        assert item["provider"] == "Anthropic"
        assert item["pricing_source"] == "litellm"
        assert manager.items["openai.gpt-5.5"]["provider"] == "OpenAI"

    def test_create_missing_disabled(self):
        manager = FakePricingManager()
        summary = sync_pricing(manager, self.prices, create_missing=False)
        assert summary["created"] == []
        assert manager.items == {}

    def test_updates_synced_row_on_price_change(self):
        manager = FakePricingManager(
            [
                litellm_row("anthropic.claude-sonnet-4-5-20250929-v1:0", "2.5", "12.5"),
            ]
        )
        summary = sync_pricing(manager, self.prices, create_missing=False)

        assert summary["updated"] == ["anthropic.claude-sonnet-4-5-20250929-v1:0"]
        item = manager.items["anthropic.claude-sonnet-4-5-20250929-v1:0"]
        assert item["input_price"] == Decimal("3")
        assert item["cache_write_price"] == Decimal("3.75")

    def test_unchanged_row_not_rewritten(self):
        manager = FakePricingManager(
            [
                litellm_row(
                    "amazon.nova-pro-v1:0",
                    "0.8",
                    "3.2",
                    provider="Amazon",
                ),
            ]
        )
        summary = sync_pricing(manager, self.prices, create_missing=False)
        assert summary["updated"] == []
        assert summary["unchanged"] == 1
        assert manager.updated == []

    def test_manual_row_skipped_unless_overwrite(self):
        manual = {
            "model_id": "amazon.nova-pro-v1:0",
            "provider": "Amazon",
            "input_price": Decimal("99"),
            "output_price": Decimal("99"),
        }
        manager = FakePricingManager([manual])
        summary = sync_pricing(manager, self.prices, create_missing=False)
        assert summary["skipped_manual"] == ["amazon.nova-pro-v1:0"]
        assert manager.items["amazon.nova-pro-v1:0"]["input_price"] == Decimal("99")

        summary = sync_pricing(
            manager, self.prices, create_missing=False, overwrite_manual=True
        )
        assert summary["updated"] == ["amazon.nova-pro-v1:0"]
        assert manager.items["amazon.nova-pro-v1:0"]["input_price"] == Decimal("0.8")
        # Forced overwrite must not convert the row to sync-managed
        assert "pricing_source" not in manager.items["amazon.nova-pro-v1:0"]

    def test_region_prefixed_existing_row_matched_via_fallback(self):
        # "global." variant isn't keyed in the source; matched via stripped lookup
        manager = FakePricingManager(
            [
                litellm_row(
                    "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "2", "10"
                ),
            ]
        )
        summary = sync_pricing(manager, self.prices, create_missing=False)
        assert summary["updated"] == [
            "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
        ]
        item = manager.items["global.anthropic.claude-sonnet-4-5-20250929-v1:0"]
        assert item["input_price"] == Decimal("3")

    def test_extra_model_ids_created_via_fallback(self):
        manager = FakePricingManager()
        summary = sync_pricing(
            manager,
            self.prices,
            create_missing=False,
            extra_model_ids=[
                "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "eu.amazon.nova-pro-v1:0",
                "anthropic.claude-nonexistent-v1:0",
                "arn:aws:bedrock:us-east-1:123456789012:inference-profile/whatever",
            ],
        )
        assert set(summary["created"]) == {
            "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "eu.amazon.nova-pro-v1:0",
        }
        assert summary["not_found"] == ["anthropic.claude-nonexistent-v1:0"]

    def test_dry_run_writes_nothing(self):
        manager = FakePricingManager(
            [
                litellm_row("anthropic.claude-sonnet-4-5-20250929-v1:0", "2.5", "12.5"),
            ]
        )
        summary = sync_pricing(manager, self.prices, dry_run=True)

        assert summary["dry_run"] is True
        assert len(summary["created"]) == len(self.prices) - 1
        assert summary["updated"] == ["anthropic.claude-sonnet-4-5-20250929-v1:0"]
        assert manager.created == []
        assert manager.updated == []
        assert manager.items["anthropic.claude-sonnet-4-5-20250929-v1:0"][
            "input_price"
        ] == Decimal("2.5")
