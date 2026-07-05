"""ModelMappingManager caches mapping lookups across instances."""
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

BEDROCK_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
ANTHROPIC_ID = "claude-sonnet-4-5-20250929"


def make_manager(cache_ttl=300, get_item_response=None):
    from app.db.dynamodb import ModelMappingManager

    m = ModelMappingManager.__new__(ModelMappingManager)
    m.table = MagicMock()
    if get_item_response is not None:
        m.table.get_item.return_value = get_item_response
    m._cache_ttl = cache_ttl
    return m


@pytest.fixture(autouse=True)
def clear_shared_cache():
    from app.db.dynamodb import ModelMappingManager

    cache = getattr(ModelMappingManager, "_cache", None)
    if cache is not None:
        cache.clear()
    yield
    if cache is not None:
        cache.clear()


def test_second_lookup_served_from_cache():
    m = make_manager(get_item_response={"Item": {"bedrock_model_id": BEDROCK_ID}})

    assert m.get_mapping(ANTHROPIC_ID) == BEDROCK_ID
    assert m.get_mapping(ANTHROPIC_ID) == BEDROCK_ID
    assert m.table.get_item.call_count == 1


def test_cache_shared_across_manager_instances():
    """Converters build a fresh manager per request; the cache must outlive them."""
    m1 = make_manager(get_item_response={"Item": {"bedrock_model_id": BEDROCK_ID}})
    m2 = make_manager()

    m1.get_mapping(ANTHROPIC_ID)
    assert m2.get_mapping(ANTHROPIC_ID) == BEDROCK_ID
    m2.table.get_item.assert_not_called()


def test_negative_result_cached():
    """Pass-through model IDs (no mapping) must not hit DynamoDB every request."""
    m = make_manager(get_item_response={})

    assert m.get_mapping("arn:aws:bedrock:us-west-2::foundation-model/x") is None
    assert m.get_mapping("arn:aws:bedrock:us-west-2::foundation-model/x") is None
    assert m.table.get_item.call_count == 1


def test_ttl_expiry_refetches(monkeypatch):
    from app.core import ttl_cache as ttl_mod

    now = 1000.0
    monkeypatch.setattr(ttl_mod.time, "monotonic", lambda: now)
    m = make_manager(
        cache_ttl=300, get_item_response={"Item": {"bedrock_model_id": BEDROCK_ID}}
    )

    m.get_mapping(ANTHROPIC_ID)
    monkeypatch.setattr(ttl_mod.time, "monotonic", lambda: now + 301)
    m.get_mapping(ANTHROPIC_ID)

    assert m.table.get_item.call_count == 2


def test_ttl_zero_disables_cache():
    m = make_manager(
        cache_ttl=0, get_item_response={"Item": {"bedrock_model_id": BEDROCK_ID}}
    )

    m.get_mapping(ANTHROPIC_ID)
    m.get_mapping(ANTHROPIC_ID)

    assert m.table.get_item.call_count == 2


def test_client_error_not_cached():
    """A DynamoDB blip must not pin None for the whole TTL."""
    m = make_manager()
    m.table.get_item.side_effect = [
        ClientError({"Error": {"Code": "Throttling", "Message": "x"}}, "GetItem"),
        {"Item": {"bedrock_model_id": BEDROCK_ID}},
    ]

    assert m.get_mapping(ANTHROPIC_ID) is None
    assert m.get_mapping(ANTHROPIC_ID) == BEDROCK_ID
    assert m.table.get_item.call_count == 2


def test_set_mapping_updates_cache_in_process():
    m = make_manager()

    m.set_mapping(ANTHROPIC_ID, BEDROCK_ID)
    assert m.get_mapping(ANTHROPIC_ID) == BEDROCK_ID
    m.table.get_item.assert_not_called()


def test_delete_mapping_invalidates_cache():
    m = make_manager(get_item_response={"Item": {"bedrock_model_id": BEDROCK_ID}})

    m.get_mapping(ANTHROPIC_ID)  # prime the cache
    m.delete_mapping(ANTHROPIC_ID)
    m.get_mapping(ANTHROPIC_ID)  # must go back to DynamoDB

    assert m.table.get_item.call_count == 2
