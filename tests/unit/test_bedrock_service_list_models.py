"""Verify list_available_models merges DEFAULT_MODEL_MAPPING with the DDB ModelMappingTable."""
from unittest.mock import MagicMock

import pytest

from app.services.bedrock_service import BedrockService, _derive_provider


@pytest.fixture
def service():
    """Build a BedrockService without running its real __init__."""
    return BedrockService.__new__(BedrockService)


@pytest.fixture
def stub_default_mapping(monkeypatch):
    """Replace settings.default_model_mapping with a deterministic fixture."""
    from app.core import config as config_mod

    monkeypatch.setattr(
        config_mod.settings,
        "default_model_mapping",
        {
            "claude-opus-4-7": "global.anthropic.claude-opus-4-7",
            "minimax.minimax-m2.5": "minimax.minimax-m2.5",
        },
    )


def _install_fake_ddb(monkeypatch, mappings):
    """Patch DynamoDBClient so list_mappings() returns the given rows."""
    fake_manager = MagicMock()
    fake_manager.list_mappings.return_value = mappings

    fake_client = MagicMock()
    fake_client.model_mapping_manager = fake_manager

    from app.db import dynamodb as ddb_mod

    monkeypatch.setattr(ddb_mod, "DynamoDBClient", lambda: fake_client)
    return fake_manager


def test_empty_ddb_returns_defaults(service, stub_default_mapping, monkeypatch):
    _install_fake_ddb(monkeypatch, [])

    models = service.list_available_models()

    ids = {m["id"] for m in models}
    assert ids == {"claude-opus-4-7", "minimax.minimax-m2.5"}
    for m in models:
        assert m["streaming_supported"] is True
        assert m["bedrock_model_id"]
        assert m["provider"]


def test_ddb_overrides_default_bedrock_id(service, stub_default_mapping, monkeypatch):
    _install_fake_ddb(
        monkeypatch,
        [
            {
                "anthropic_model_id": "claude-opus-4-7",
                "bedrock_model_id": "us.anthropic.claude-opus-4-7-custom",
            }
        ],
    )

    models = service.list_available_models()
    by_id = {m["id"]: m for m in models}

    assert by_id["claude-opus-4-7"]["bedrock_model_id"] == "us.anthropic.claude-opus-4-7-custom"
    # Untouched default still present
    assert by_id["minimax.minimax-m2.5"]["bedrock_model_id"] == "minimax.minimax-m2.5"


def test_ddb_adds_new_key(service, stub_default_mapping, monkeypatch):
    _install_fake_ddb(
        monkeypatch,
        [
            {
                "anthropic_model_id": "custom-model",
                "bedrock_model_id": "global.foo.custom-v1",
            }
        ],
    )

    models = service.list_available_models()
    ids = {m["id"] for m in models}
    assert "custom-model" in ids
    assert "claude-opus-4-7" in ids


def test_ddb_row_missing_fields_is_skipped(service, stub_default_mapping, monkeypatch):
    _install_fake_ddb(
        monkeypatch,
        [
            {"anthropic_model_id": "only-key"},
            {"bedrock_model_id": "only-value"},
            {},
            {
                "anthropic_model_id": "good",
                "bedrock_model_id": "global.anthropic.good",
            },
        ],
    )

    models = service.list_available_models()
    ids = {m["id"] for m in models}
    assert "good" in ids
    assert "only-key" not in ids
    assert "only-value" not in ids


def test_ddb_failure_falls_back_to_defaults(service, stub_default_mapping, monkeypatch):
    from app.db import dynamodb as ddb_mod

    def boom():
        raise RuntimeError("DDB unreachable")

    monkeypatch.setattr(ddb_mod, "DynamoDBClient", boom)

    models = service.list_available_models()
    ids = {m["id"] for m in models}
    assert ids == {"claude-opus-4-7", "minimax.minimax-m2.5"}


@pytest.mark.parametrize(
    "bedrock_id, expected",
    [
        ("global.anthropic.claude-opus-4-7", "anthropic"),
        ("us.anthropic.claude-3-5-haiku-20241022-v1:0", "anthropic"),
        ("eu.anthropic.claude-sonnet", "anthropic"),
        ("apac.anthropic.claude-sonnet", "anthropic"),
        ("minimax.minimax-m2.5", "minimax"),
        ("zai.glm-5", "zai"),
        ("moonshotai.kimi-k2.5", "moonshotai"),
        (
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/"
            "global.anthropic.claude-opus-4-7",
            "anthropic",
        ),
        ("amazon.nova-pro-v1:0", "amazon"),
        ("", ""),
    ],
)
def test_derive_provider(bedrock_id, expected):
    assert _derive_provider(bedrock_id) == expected
