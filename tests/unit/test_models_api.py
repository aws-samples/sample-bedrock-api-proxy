"""Tests for /v1/models API endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.models import (
    _build_model_entry,
    _collect_supported_model_ids,
    _humanize_model_id,
    router,
)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    return TestClient(app)


# ── _humanize_model_id ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("claude-opus-4-7", "Claude Opus 4.7"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-opus-4-5-20251101", "Claude Opus 4.5"),
        ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
        ("claude-3-5-haiku-20241022", "Claude 3.5 Haiku"),
    ],
)
def test_humanize_model_id(model_id, expected):
    assert _humanize_model_id(model_id) == expected


def test_humanize_model_id_without_claude_prefix():
    # Non-Claude short IDs still get a "Claude" prefix so the display name
    # looks coherent alongside Claude models.
    assert _humanize_model_id("opus-4-7") == "Claude Opus 4.7"


# ── _collect_supported_model_ids ──────────────────────────────────────────────


def test_collect_merges_defaults_and_dynamodb():
    with (
        patch("app.api.models.settings") as mock_settings,
        patch("app.api.models.DynamoDBClient"),
        patch("app.api.models.ModelMappingManager") as mock_mgr_cls,
    ):
        mock_settings.default_model_mapping = {
            "claude-opus-4-7": "us.anthropic.claude-opus-4-7",
            "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
        }
        mgr = MagicMock()
        mgr.list_mappings.return_value = [
            {
                "anthropic_model_id": "claude-custom-model",
                "bedrock_model_id": "arn:aws:bedrock:...",
            },
            {"anthropic_model_id": "claude-opus-4-7"},  # duplicate of default
            {"bedrock_model_id": "missing-anthropic-id"},  # skipped
        ]
        mock_mgr_cls.return_value = mgr

        ids = _collect_supported_model_ids()

        assert ids == sorted(
            ["claude-opus-4-7", "claude-sonnet-4-6", "claude-custom-model"]
        )


def test_collect_degrades_when_dynamodb_unavailable():
    with (
        patch("app.api.models.settings") as mock_settings,
        patch("app.api.models.DynamoDBClient", side_effect=RuntimeError("boom")),
    ):
        mock_settings.default_model_mapping = {"claude-opus-4-7": "..."}

        ids = _collect_supported_model_ids()

        assert ids == ["claude-opus-4-7"]


# ── _build_model_entry ────────────────────────────────────────────────────────


def test_build_model_entry_shape():
    entry = _build_model_entry("claude-opus-4-7")
    assert entry == {
        "type": "model",
        "id": "claude-opus-4-7",
        "display_name": "Claude Opus 4.7",
        "created_at": "2024-01-01T00:00:00Z",
    }


# ── GET /v1/models ────────────────────────────────────────────────────────────


def test_list_models_returns_mapping_keys(client):
    with (
        patch("app.api.models.settings") as mock_settings,
        patch(
            "app.api.models._collect_supported_model_ids",
            return_value=["claude-opus-4-7", "claude-sonnet-4-6"],
        ),
    ):
        mock_settings.multi_provider_enabled = False

        resp = client.get("/v1/models")

    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert body["has_more"] is False
    ids = [m["id"] for m in body["data"]]
    assert ids == ["claude-opus-4-7", "claude-sonnet-4-6"]
    # Anthropic-shape entries
    assert body["data"][0]["type"] == "model"
    assert body["data"][0]["display_name"] == "Claude Opus 4.7"


def test_list_models_multi_provider_unchanged(client):
    # When multi-provider is enabled and a registry is present, the
    # aggregation path is preserved unchanged.
    with patch("app.api.models.settings") as mock_settings:
        mock_settings.multi_provider_enabled = True
        registry = MagicMock()
        registry.list_all_models.return_value = [
            {"id": "anthropic.claude-opus-4-7", "provider": "anthropic"},
            {"id": "qwen.qwen3-235b", "provider": "qwen"},
        ]
        client.app.state.provider_registry = registry

        resp = client.get("/v1/models")

    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert ids == ["anthropic.claude-opus-4-7", "qwen.qwen3-235b"]


# ── GET /v1/models/{model_id} ─────────────────────────────────────────────────


def test_get_model_returns_anthropic_shape_for_mapping_hit(client):
    with patch(
        "app.api.models._collect_supported_model_ids",
        return_value=["claude-opus-4-7"],
    ):
        resp = client.get("/v1/models/claude-opus-4-7")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "type": "model",
        "id": "claude-opus-4-7",
        "display_name": "Claude Opus 4.7",
        "created_at": "2024-01-01T00:00:00Z",
    }


def test_get_model_falls_back_to_bedrock_lookup(client):
    bedrock_stub = MagicMock()
    bedrock_stub.get_model_info.return_value = {
        "id": "anthropic.claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
    }
    client.app.dependency_overrides = {}
    from app.api.models import get_bedrock_service

    client.app.dependency_overrides[get_bedrock_service] = lambda: bedrock_stub

    with patch("app.api.models._collect_supported_model_ids", return_value=[]):
        resp = client.get("/v1/models/anthropic.claude-sonnet-4-6")

    client.app.dependency_overrides.pop(get_bedrock_service, None)
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "model"
    assert body["id"] == "anthropic.claude-sonnet-4-6"


def test_get_model_returns_404_when_unknown(client):
    bedrock_stub = MagicMock()
    bedrock_stub.get_model_info.return_value = None
    from app.api.models import get_bedrock_service

    client.app.dependency_overrides[get_bedrock_service] = lambda: bedrock_stub

    with patch("app.api.models._collect_supported_model_ids", return_value=[]):
        resp = client.get("/v1/models/does-not-exist")

    client.app.dependency_overrides.pop(get_bedrock_service, None)
    assert resp.status_code == 404


def test_get_model_returns_404_when_bedrock_lookup_raises(client):
    # bedrock_service.get_model_info raises on invalid IDs (ValidationException);
    # we want the API to surface this as a clean 404, not a 500.
    bedrock_stub = MagicMock()
    bedrock_stub.get_model_info.side_effect = Exception(
        "ValidationException: invalid model identifier"
    )
    from app.api.models import get_bedrock_service

    client.app.dependency_overrides[get_bedrock_service] = lambda: bedrock_stub

    with patch("app.api.models._collect_supported_model_ids", return_value=[]):
        resp = client.get("/v1/models/totally-bogus")

    client.app.dependency_overrides.pop(get_bedrock_service, None)
    assert resp.status_code == 404
