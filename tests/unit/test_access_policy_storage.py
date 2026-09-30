"""Policy persistence/admin roundtrips and existing auth-cache invariants."""

import asyncio
import copy
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from moto import mock_aws
from pydantic import ValidationError

from admin_portal.backend.api import api_keys
from admin_portal.backend.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyResponse,
    ApiKeyUpdate,
)
from app.core import ttl_cache
from app.core.access_policy import (
    AccessPolicyDenied,
    policy_from_key_info,
    require_ip,
    require_model,
)
from app.core.config import settings
from app.db.dynamodb import APIKeyManager, DynamoDBClient
from app.middleware.auth import AuthMiddleware


def payload():
    return {
        "version": 1,
        "ip": {"enabled": True, "allow": ["203.0.113.8"]},
        "model": {"enabled": True, "allow": ["us.vendor.model-v1:0"]},
    }


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(settings, "dynamodb_endpoint_url", None)
    with mock_aws():
        client = DynamoDBClient()
        client._create_api_keys_table()
        yield APIKeyManager(client)


@pytest.fixture
def admin_client(manager, monkeypatch):
    stats = MagicMock()
    stats.get_stats.return_value = None
    monkeypatch.setattr(api_keys, "get_managers", lambda: (manager, None, stats))
    app = FastAPI()
    app.include_router(api_keys.router, prefix="/api/keys")
    with TestClient(app) as client:
        yield client


def test_manager_legacy_and_policy_roundtrip(manager):
    legacy = manager.create_api_key("user", "legacy")
    key = manager.create_api_key("user", "restricted", access_policy=payload())
    assert "access_policy" not in manager.get_api_key(legacy)
    stored = manager.table.get_item(Key={"api_key": key})["Item"]
    assert isinstance(stored["access_policy"]["version"], Decimal)
    for item in (
        manager.get_api_key(key),
        manager.validate_api_key(key),
        *manager.list_api_keys_for_user("user"),
        *manager.list_all_api_keys()["items"],
    ):
        if item["api_key"] != key:
            continue
        assert type(item["access_policy"]["version"]) is int
        assert item["access_policy"]["ip"]["allow"] == ["203.0.113.8/32"]
        require_model(policy_from_key_info(item), "us.vendor.model-v1:0")
    assert manager.update_api_key(key, name="renamed", rate_limit=321)
    assert (
        manager.get_api_key(key)["access_policy"]
        == manager.validate_api_key(key)["access_policy"]
    )
    assert manager.get_api_key(key)["rate_limit"] == 321


@pytest.mark.parametrize("bad", [None, {}, {**payload(), "version": "1"}])
def test_invalid_manager_writes_have_no_side_effect(manager, bad):
    key = manager.create_api_key("user", "restricted", access_policy=payload())
    old = manager.get_api_key(key)
    with pytest.raises(ValidationError):
        manager.create_api_key("user", "invalid", access_policy=bad)
    with pytest.raises(ValidationError):
        manager.update_api_key(key, name="must-not-change", access_policy=bad)
    assert manager.get_api_key(key) == old
    assert manager.list_all_api_keys()["count"] == 1


def test_policy_replacement_is_one_atomic_attribute_update(manager, monkeypatch):
    key = manager.create_api_key("user", "restricted", access_policy=payload())
    replacement = payload()
    replacement["ip"] = {"enabled": False, "allow": []}
    replacement["model"]["allow"] = ["new-target"]
    update = MagicMock(wraps=manager.table.update_item)
    monkeypatch.setattr(manager.table, "update_item", update)
    assert manager.update_api_key(key, access_policy=replacement)
    update.assert_called_once()
    kwargs = update.call_args.kwargs
    assert "access_policy = :access_policy" in kwargs["UpdateExpression"]
    assert "access_policy." not in kwargs["UpdateExpression"]
    assert kwargs["ExpressionAttributeValues"][":access_policy"] == replacement
    assert manager.get_api_key(key)["access_policy"] == replacement
    require_ip(policy_from_key_info(manager.get_api_key(key)), None)


@pytest.mark.parametrize("bad", [None, {}, {**payload(), "version": Decimal(2)}])
def test_corrupt_stored_policy_survives_reads_for_fail_closed_evaluation(manager, bad):
    key = manager.create_api_key("user", "restricted", access_policy=payload())
    manager.table.update_item(
        Key={"api_key": key},
        UpdateExpression="SET access_policy = :p",
        ExpressionAttributeValues={":p": bad},
    )
    item = manager.validate_api_key(key)
    assert item["access_policy"] == bad
    with pytest.raises(AccessPolicyDenied) as exc:
        policy_from_key_info(item)
    assert exc.value.reason == "invalid_policy"


def test_validation_reads_are_strongly_consistent(manager, monkeypatch):
    key = manager.create_api_key("user", "key", access_policy=payload())
    read = MagicMock(wraps=manager.table.get_item)
    monkeypatch.setattr(manager.table, "get_item", read)
    manager.validate_api_key(key)
    read.assert_called_once_with(Key={"api_key": key}, ConsistentRead=True)
    read.reset_mock()
    manager.get_api_key(key)
    read.assert_called_once_with(Key={"api_key": key}, ConsistentRead=True)
    read.reset_mock()
    manager.table.update_item(
        Key={"api_key": key},
        UpdateExpression="SET is_active = :f, deactivated_reason = :r, budget_mtd_month = :m",
        ExpressionAttributeValues={
            ":f": False,
            ":r": "budget_exceeded",
            ":m": "2000-01",
        },
    )
    result = manager.validate_api_key(key)
    assert result["is_active"] is True
    assert read.call_args_list[0].kwargs["ConsistentRead"] is True
    assert read.call_args_list[-1].kwargs["ConsistentRead"] is True
    require_model(policy_from_key_info(result), "us.vendor.model-v1:0")


def test_inactive_and_budget_exceeded_key_behavior_unchanged(manager):
    key = manager.create_api_key("user", "key", access_policy=payload())
    manager.deactivate_api_key(key)
    assert manager.validate_api_key(key) is None
    manager.deactivate_for_budget_exceeded(key)
    assert manager.validate_api_key(key) is None
    assert manager.validate_api_key("missing") is None


async def test_admin_route_functions_roundtrip(manager, monkeypatch):
    monkeypatch.setattr(api_keys, "get_managers", lambda: (manager, None, None))
    created = await api_keys.create_api_key(
        ApiKeyCreate(user_id="user", name="restricted", access_policy=payload())
    )
    updated = await api_keys.update_api_key(
        created.api_key, ApiKeyUpdate(name="renamed")
    )
    assert updated.access_policy == created.access_policy
    assert updated.name == "renamed"


def test_admin_http_create_edit_reload_two_keys(admin_client, manager):
    first = admin_client.post(
        "/api/keys",
        json={"user_id": "user", "name": "first", "access_policy": payload()},
    )
    assert first.status_code == 201
    second_policy = payload()
    second_policy["ip"] = {"enabled": False, "allow": []}
    second_policy["model"]["allow"] = ["target-b"]
    second = admin_client.post(
        "/api/keys",
        json={"user_id": "user", "name": "second", "access_policy": second_policy},
    )
    assert second.status_code == 201
    key = first.json()["api_key"]
    original = first.json()["access_policy"]
    assert (
        admin_client.put(f"/api/keys/{key}", json={"name": "rename"}).json()[
            "access_policy"
        ]
        == original
    )
    assert admin_client.get(f"/api/keys/{key}").json()["access_policy"] == original
    policies = {
        row["api_key"]: row["access_policy"]
        for row in admin_client.get("/api/keys").json()["items"]
    }
    assert policies[key] == original
    assert policies[second.json()["api_key"]] == second_policy
    disabled = {
        "version": 1,
        "ip": {"enabled": False, "allow": []},
        "model": {"enabled": False, "allow": []},
    }
    result = admin_client.put(f"/api/keys/{key}", json={"access_policy": disabled})
    assert result.status_code == 200
    assert result.json()["access_policy"] == disabled
    assert manager.get_api_key(key)["access_policy"] == disabled


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {**payload(), "version": 2},
        {**payload(), "ip": {"enabled": True, "allow": []}},
    ],
)
def test_admin_http_rejects_invalid_create_and_update_without_writes(
    admin_client, manager, bad
):
    key = manager.create_api_key("user", "original", access_policy=payload())
    old = manager.get_api_key(key)
    created = admin_client.post(
        "/api/keys", json={"user_id": "user", "name": "bad", "access_policy": bad}
    )
    assert created.status_code == 422
    updated = admin_client.put(
        f"/api/keys/{key}", json={"name": "bad", "access_policy": bad}
    )
    assert updated.status_code == 422
    assert manager.get_api_key(key) == old
    assert manager.list_all_api_keys()["count"] == 1


def test_legacy_admin_client_still_works(admin_client, manager):
    response = admin_client.post(
        "/api/keys", json={"user_id": "user", "name": "legacy"}
    )
    assert response.status_code == 201
    key = response.json()["api_key"]
    assert "access_policy" not in response.json()
    assert "access_policy" not in manager.get_api_key(key)
    assert (
        admin_client.put(f"/api/keys/{key}", json={"name": "rename"}).status_code == 200
    )
    assert "access_policy" not in manager.get_api_key(key)


async def test_policy_cache_singleflight_isolation_and_per_request_ip(manager):
    key = manager.create_api_key("user", "restricted", access_policy=payload())
    worker = AuthMiddleware(MagicMock(), MagicMock(), cache_ttl_seconds=60)
    worker.api_key_manager = manager
    lookup = MagicMock(wraps=manager.validate_api_key)
    manager.validate_api_key = lookup
    one, two = await asyncio.gather(
        worker._validate_api_key(key), worker._validate_api_key(key)
    )
    lookup.assert_called_once_with(key)
    admitted = policy_from_key_info(one)
    one["access_policy"]["model"]["allow"].append("evil")
    assert not policy_from_key_info(two).allows_model("evil")
    three = await worker._validate_api_key(key)
    assert not policy_from_key_info(three).allows_model("evil")
    require_ip(admitted, "203.0.113.8")
    with pytest.raises(AccessPolicyDenied):
        require_ip(policy_from_key_info(three), "203.0.113.9")
    lookup.assert_called_once()


async def test_workers_converge_after_expiry_without_stale_fallback(
    manager, monkeypatch
):
    now = [1000.0]
    monkeypatch.setattr(ttl_cache.time, "monotonic", lambda: now[0])
    key = manager.create_api_key("user", "restricted", access_policy=payload())
    workers = [
        AuthMiddleware(MagicMock(), MagicMock(), cache_ttl_seconds=60) for _ in range(2)
    ]
    for worker in workers:
        worker.api_key_manager = manager
    admitted = [
        policy_from_key_info(await worker._validate_api_key(key)) for worker in workers
    ]
    replacement = copy.deepcopy(payload())
    replacement["model"]["allow"] = ["target-b"]
    manager.update_api_key(key, access_policy=replacement)
    now[0] += 59
    for worker in workers:
        assert policy_from_key_info(await worker._validate_api_key(key)).allows_model(
            "us.vendor.model-v1:0"
        )
    now[0] += 2
    for worker in workers:
        current = policy_from_key_info(await worker._validate_api_key(key))
        require_model(current, "target-b")
        assert not current.allows_model("us.vendor.model-v1:0")
    # Already-admitted work retains its immutable snapshot.
    assert all(policy.allows_model("us.vendor.model-v1:0") for policy in admitted)
    now[0] += 61
    original_read = manager.table.get_item
    broken_read = MagicMock(
        side_effect=ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "unavailable"}},
            "GetItem",
        )
    )
    monkeypatch.setattr(manager.table, "get_item", broken_read)
    for worker in workers:
        assert await worker._validate_api_key(key) is None
    monkeypatch.setattr(manager.table, "get_item", original_read)
    for worker in workers:
        # Read failures weren't cached; no stale permissive policy was served.
        require_model(
            policy_from_key_info(await worker._validate_api_key(key)), "target-b"
        )


@pytest.mark.parametrize("bad", [None, {}, {**payload(), "version": 2}])
def test_admin_response_does_not_hide_corrupt_policy_as_legacy(manager, bad):
    key = manager.create_api_key("user", "key")
    item = manager.get_api_key(key)
    item["access_policy"] = bad
    with pytest.raises(ValidationError):
        ApiKeyResponse(**item)


async def test_admin_can_replace_corrupt_policy(manager, monkeypatch):
    key = manager.create_api_key("user", "key")
    manager.table.update_item(
        Key={"api_key": key},
        UpdateExpression="SET access_policy = :p",
        ExpressionAttributeValues={":p": None},
    )
    monkeypatch.setattr(api_keys, "get_managers", lambda: (manager, None, None))
    repaired = await api_keys.update_api_key(key, ApiKeyUpdate(access_policy=payload()))
    assert repaired.access_policy.version == 1
