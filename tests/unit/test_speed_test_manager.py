"""SpeedTestManager + speed-tests table creation (moto)."""

from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from app.core.config import settings


def _record(model_id: str, tested_at: int, **overrides):
    rec = {
        "bedrock_model_id": model_id,
        "tested_at": tested_at,
        "status": "ok",
        "ttft_ms": 412.5,
        "total_ms": 3200.0,
        "output_tokens": 180,
        "otps": 64.57,
        "has_reasoning": False,
        "error": None,
        "proxy_base_url": "http://localhost:8000",
        "expires_at": tested_at // 1000 + 90 * 86400,
    }
    rec.update(overrides)
    return rec


@pytest.fixture
def db():
    with mock_aws():
        from app.db.dynamodb import DynamoDBClient

        client = DynamoDBClient()
        client._create_speed_tests_table()
        yield client


@pytest.fixture
def manager(db):
    from app.db.dynamodb import SpeedTestManager

    return SpeedTestManager(db)


def test_table_created_with_ttl_on_expires_at(db):
    ddb = boto3.client("dynamodb", region_name=settings.aws_region)
    desc = ddb.describe_table(TableName=settings.dynamodb_speed_tests_table)["Table"]
    keys = {k["AttributeName"]: k["KeyType"] for k in desc["KeySchema"]}
    assert keys == {"bedrock_model_id": "HASH", "tested_at": "RANGE"}
    ttl = ddb.describe_time_to_live(TableName=settings.dynamodb_speed_tests_table)
    assert ttl["TimeToLiveDescription"]["AttributeName"] == "expires_at"
    assert ttl["TimeToLiveDescription"]["TimeToLiveStatus"] in ("ENABLED", "ENABLING")


def test_create_tables_is_idempotent(db):
    # second call must swallow ResourceInUseException
    db._create_speed_tests_table()
    assert db.speed_tests_table_name == settings.dynamodb_speed_tests_table


def test_put_and_history_newest_first_with_limit(manager):
    for ts in (1000, 3000, 2000):
        manager.put_result(_record("m1", ts, ttft_ms=float(ts)))
    manager.put_result(_record("other", 9000))

    history = manager.get_history("m1", limit=10)
    assert [r["tested_at"] for r in history] == [3000, 2000, 1000]
    # Decimal -> native types
    assert not isinstance(history[0]["ttft_ms"], Decimal)
    assert history[0]["ttft_ms"] == 3000.0
    assert history[0]["otps"] == 64.57 and isinstance(history[0]["otps"], float)
    assert isinstance(history[0]["output_tokens"], int)
    assert isinstance(history[0]["tested_at"], int)
    assert history[0]["has_reasoning"] is False
    assert history[0]["error"] is None
    assert history[0]["expires_at"] == 3000 // 1000 + 90 * 86400

    assert [r["tested_at"] for r in manager.get_history("m1", limit=2)] == [3000, 2000]


def test_get_latest_one(manager):
    assert manager.get_latest_one("m1") is None
    manager.put_result(_record("m1", 1000))
    manager.put_result(
        _record("m1", 5000, status="error", error="boom", ttft_ms=None, otps=None)
    )
    latest = manager.get_latest_one("m1")
    assert latest["tested_at"] == 5000
    assert latest["status"] == "error"
    assert latest["error"] == "boom"
    assert latest["ttft_ms"] is None
