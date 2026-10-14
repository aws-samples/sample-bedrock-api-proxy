"""Tests for admin portal model-mapping override semantics.

Default mappings (from config.py) are editable via the admin portal:
editing one writes a DynamoDB override row that shadows the default at
resolution time; deleting the override restores the default.
"""
import pytest
from fastapi import HTTPException
from moto import mock_aws

from app.core.config import settings


DEFAULT_ID = "claude-fable-5"
DEFAULT_TARGET = settings.default_model_mapping[DEFAULT_ID]
OVERRIDE_TARGET = "us.anthropic.claude-fable-5"


@pytest.fixture
def mock_dynamodb():
    with mock_aws():
        import boto3

        # Must match settings.aws_region: DynamoDBClient builds its resource from
        # that, so a hardcoded region makes every table lookup here a 404.
        dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
        dynamodb.create_table(
            TableName=settings.dynamodb_model_mapping_table,
            KeySchema=[{"AttributeName": "anthropic_model_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "anthropic_model_id", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield dynamodb


@pytest.fixture
def api(mock_dynamodb):
    from admin_portal.backend.api import model_mapping

    return model_mapping


@pytest.fixture
def schemas():
    from admin_portal.backend.schemas.model_mapping import (
        ModelMappingCreate,
        ModelMappingUpdate,
    )

    return ModelMappingCreate, ModelMappingUpdate


async def test_put_default_creates_override(api, schemas):
    _, ModelMappingUpdate = schemas
    resp = await api.update_model_mapping(
        DEFAULT_ID, ModelMappingUpdate(bedrock_model_id=OVERRIDE_TARGET)
    )
    assert resp.source == "override"
    assert resp.bedrock_model_id == OVERRIDE_TARGET
    assert resp.default_bedrock_model_id == DEFAULT_TARGET


async def test_list_shows_override_once(api, schemas):
    _, ModelMappingUpdate = schemas
    await api.update_model_mapping(
        DEFAULT_ID, ModelMappingUpdate(bedrock_model_id=OVERRIDE_TARGET)
    )
    listing = await api.list_model_mappings(search=None)
    entries = [i for i in listing.items if i.anthropic_model_id == DEFAULT_ID]
    assert len(entries) == 1
    assert entries[0].source == "override"
    assert entries[0].bedrock_model_id == OVERRIDE_TARGET


async def test_override_used_at_resolution_time(api, schemas):
    _, ModelMappingUpdate = schemas
    await api.update_model_mapping(
        DEFAULT_ID, ModelMappingUpdate(bedrock_model_id=OVERRIDE_TARGET)
    )
    from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter
    from app.db.dynamodb import DynamoDBClient

    converter = AnthropicToBedrockConverter(DynamoDBClient())
    assert converter._convert_model_id(DEFAULT_ID) == OVERRIDE_TARGET


async def test_delete_override_restores_default(api, schemas):
    _, ModelMappingUpdate = schemas
    await api.update_model_mapping(
        DEFAULT_ID, ModelMappingUpdate(bedrock_model_id=OVERRIDE_TARGET)
    )
    result = await api.delete_model_mapping(DEFAULT_ID)
    assert result["restored_default"] is True

    restored = await api.get_model_mapping(DEFAULT_ID)
    assert restored.source == "default"
    assert restored.bedrock_model_id == DEFAULT_TARGET


async def test_delete_pure_default_rejected(api):
    with pytest.raises(HTTPException) as exc_info:
        await api.delete_model_mapping(DEFAULT_ID)
    assert exc_info.value.status_code == 400


async def test_put_unknown_model_404(api, schemas):
    _, ModelMappingUpdate = schemas
    with pytest.raises(HTTPException) as exc_info:
        await api.update_model_mapping(
            "no-such-model", ModelMappingUpdate(bedrock_model_id="x")
        )
    assert exc_info.value.status_code == 404


async def test_custom_mapping_crud_unchanged(api, schemas):
    ModelMappingCreate, ModelMappingUpdate = schemas
    created = await api.create_model_mapping(
        ModelMappingCreate(anthropic_model_id="my-alias", bedrock_model_id="zai.glm-5")
    )
    assert created.source == "custom"
    assert created.default_bedrock_model_id is None

    updated = await api.update_model_mapping(
        "my-alias", ModelMappingUpdate(bedrock_model_id="moonshotai.kimi-k2.5")
    )
    assert updated.source == "custom"

    result = await api.delete_model_mapping("my-alias")
    assert result["restored_default"] is False


async def test_post_over_default_reports_override(api, schemas):
    ModelMappingCreate, _ = schemas
    created = await api.create_model_mapping(
        ModelMappingCreate(
            anthropic_model_id=DEFAULT_ID, bedrock_model_id=OVERRIDE_TARGET
        )
    )
    assert created.source == "override"
    assert created.default_bedrock_model_id == DEFAULT_TARGET


async def test_legacy_iso_timestamp_row_does_not_break_listing(api, mock_dynamodb):
    """Rows written by ad-hoc tooling carry ISO-8601 timestamps, not epoch ints.

    Prod hit this: one such row made GET /api/model-mapping return 500, taking
    the whole Model Mapping page down.
    """
    table = mock_dynamodb.Table(settings.dynamodb_model_mapping_table)
    table.put_item(
        Item={
            "anthropic_model_id": "legacy-alias",
            "bedrock_model_id": "openai.gpt-5.4",
            "updated_at": "2026-07-29T15:57:49.963303+00:00",
            "created_at": "2026-07-29T15:57:49.963303+00:00",
        }
    )

    listing = await api.list_model_mappings(search=None)
    entry = next(i for i in listing.items if i.anthropic_model_id == "legacy-alias")
    assert entry.updated_at == 1785340669

    fetched = await api.get_model_mapping("legacy-alias")
    assert fetched.updated_at == 1785340669


async def test_unparseable_timestamp_reported_as_unset(api, mock_dynamodb):
    table = mock_dynamodb.Table(settings.dynamodb_model_mapping_table)
    table.put_item(
        Item={
            "anthropic_model_id": "junk-ts-alias",
            "bedrock_model_id": "zai.glm-5",
            "updated_at": "not-a-timestamp",
        }
    )

    listing = await api.list_model_mappings(search=None)
    entry = next(i for i in listing.items if i.anthropic_model_id == "junk-ts-alias")
    assert entry.updated_at is None
