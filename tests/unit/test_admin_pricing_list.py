"""Tests for the admin portal pricing list: filters must cover every DynamoDB page.

Regression: list_pricing scanned one page (Limit=50) and filtered it in memory,
so after a LiteLLM sync (~300 rows) search could not find synced models while
POST still returned 409 for them.
"""

import pytest
from fastapi import HTTPException
from moto import mock_aws

TARGET = "global.anthropic.claude-opus-5-5"
PAGE_SIZE = 7  # force many DynamoDB pages with a small table


@pytest.fixture
def pricing_api(monkeypatch):
    with mock_aws():
        from admin_portal.backend.api import pricing
        from app.db.dynamodb import DynamoDBClient, ModelPricingManager

        client = DynamoDBClient()
        client._create_model_pricing_table()
        manager = ModelPricingManager(client)

        for i in range(60):
            manager.create_pricing(
                model_id=f"model-{i:03d}",
                provider="Anthropic" if i % 2 else "OpenAI",
                input_price=1,
                output_price=2,
            )
        manager.create_pricing(
            model_id=TARGET, provider="Anthropic", input_price=4, output_price=20
        )

        # Cap every page so the route has to follow LastEvaluatedKey.
        real_list = manager.list_all_pricing
        calls = []

        def paged_list(limit=100, **kwargs):
            calls.append(kwargs.get("last_key"))
            return real_list(limit=min(limit, PAGE_SIZE), **kwargs)

        monkeypatch.setattr(manager, "list_all_pricing", paged_list)
        monkeypatch.setattr(pricing, "get_manager", lambda: manager)
        yield pricing, calls


async def test_list_returns_every_row_sorted(pricing_api):
    pricing, calls = pricing_api
    result = await pricing.list_pricing(
        limit=None, provider=None, status_filter=None, search=None
    )
    ids = [item.model_id for item in result.items]
    assert result.count == 61
    assert ids == sorted(ids)
    assert TARGET in ids
    assert len(calls) > 1
    assert result.last_key is None


async def test_search_finds_row_beyond_first_page(pricing_api):
    pricing, _ = pricing_api
    result = await pricing.list_pricing(
        limit=None, provider=None, status_filter=None, search="opus-5-5"
    )
    assert [item.model_id for item in result.items] == [TARGET]


async def test_provider_filter_follows_gsi_pages(pricing_api):
    pricing, calls = pricing_api
    result = await pricing.list_pricing(
        limit=None, provider="Anthropic", status_filter=None, search=None
    )
    assert result.count == 31  # 30 odd-numbered rows + TARGET
    assert {item.provider for item in result.items} == {"Anthropic"}
    assert len(calls) > 1


async def test_limit_truncates_sorted_matches(pricing_api):
    pricing, _ = pricing_api
    result = await pricing.list_pricing(
        limit=5, provider=None, status_filter=None, search="model-"
    )
    assert [item.model_id for item in result.items] == [
        f"model-{i:03d}" for i in range(5)
    ]


async def test_providers_cover_every_page(pricing_api):
    pricing, calls = pricing_api
    result = await pricing.list_providers()
    assert result == {"providers": ["Anthropic", "OpenAI"]}
    assert len(calls) > 1


async def test_create_existing_row_conflicts(pricing_api):
    pricing, _ = pricing_api
    from admin_portal.backend.schemas.pricing import PricingCreate

    with pytest.raises(HTTPException) as exc:
        await pricing.create_pricing(
            PricingCreate(
                model_id=TARGET, provider="Anthropic", input_price=5, output_price=25
            )
        )
    assert exc.value.status_code == 409
