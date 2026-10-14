"""Tests for ModelPricingManager provider/status filtering.

Regression guard: provider filtering must work via scan+FilterExpression and must
NOT require a `provider-index` GSI on the pricing table. The table is created here
WITHOUT that GSI; if the code falls back to querying the index, these tests fail.
"""
import boto3
import pytest
from decimal import Decimal
from moto import mock_aws

from app.core.config import settings
from app.db.dynamodb import DynamoDBClient, ModelPricingManager


@pytest.fixture
def manager():
    with mock_aws():
        # Pre-create the pricing table WITHOUT a provider-index GSI (mirrors prod).
        boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName=settings.dynamodb_model_pricing_table,
            KeySchema=[{"AttributeName": "model_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "model_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        db = DynamoDBClient()  # other tables auto-created; pricing already exists (no GSI)
        mgr = ModelPricingManager(db)
        items = [
            {"model_id": "global.anthropic.claude-opus-4-8", "provider": "Anthropic",
             "input_price": Decimal("5"), "output_price": Decimal("25"), "status": "active"},
            {"model_id": "global.anthropic.claude-fable-5", "provider": "Anthropic",
             "input_price": Decimal("10"), "output_price": Decimal("50"), "status": "deprecated"},
            {"model_id": "minimax.minimax-m2", "provider": "MiniMax",
             "input_price": Decimal("0.15"), "output_price": Decimal("0.6"), "status": "active"},
        ]
        for it in items:
            mgr.table.put_item(Item=it)
        yield mgr


def _ids(result):
    return {i["model_id"] for i in result["items"]}


def test_no_filter_returns_all(manager):
    assert len(manager.list_all_pricing()["items"]) == 3


def test_provider_filter_without_gsi(manager):
    result = manager.list_all_pricing(provider_filter="Anthropic")
    assert _ids(result) == {"global.anthropic.claude-opus-4-8", "global.anthropic.claude-fable-5"}


def test_provider_filter_other(manager):
    result = manager.list_all_pricing(provider_filter="MiniMax")
    assert _ids(result) == {"minimax.minimax-m2"}


def test_status_filter(manager):
    result = manager.list_all_pricing(status_filter="active")
    assert _ids(result) == {"global.anthropic.claude-opus-4-8", "minimax.minimax-m2"}


def test_provider_and_status_combined(manager):
    result = manager.list_all_pricing(provider_filter="Anthropic", status_filter="active")
    assert _ids(result) == {"global.anthropic.claude-opus-4-8"}


def test_get_pricing_by_provider_without_gsi(manager):
    items = manager.get_pricing_by_provider("Anthropic")
    assert {i["model_id"] for i in items} == {
        "global.anthropic.claude-opus-4-8",
        "global.anthropic.claude-fable-5",
    }
