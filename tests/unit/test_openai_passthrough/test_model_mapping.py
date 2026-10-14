"""Tests for resolve_model_id."""
from unittest.mock import MagicMock

import pytest

from app.api.openai_passthrough.model_mapping import resolve_model_id


@pytest.fixture(autouse=True)
def isolated_defaults(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.default_model_mapping", {})


def test_returns_mapped_id_when_mapping_exists():
    manager = MagicMock()
    manager.get_mapping.return_value = "openai.gpt-oss-120b"

    out = resolve_model_id("gpt-4", manager)
    assert out == "openai.gpt-oss-120b"
    manager.get_mapping.assert_called_once_with("gpt-4")


def test_passes_through_when_no_mapping_exists():
    manager = MagicMock()
    manager.get_mapping.return_value = None

    out = resolve_model_id("openai.gpt-oss-120b", manager)
    assert out == "openai.gpt-oss-120b"


def test_passes_through_empty_string():
    manager = MagicMock()
    manager.get_mapping.return_value = None

    assert resolve_model_id("", manager) == ""


def test_handles_lookup_exception_by_passing_through():
    """If DDB lookup raises, fall back to the original ID rather than crashing the request."""
    manager = MagicMock()
    manager.get_mapping.side_effect = RuntimeError("ddb down")

    out = resolve_model_id("gpt-4", manager)
    assert out == "gpt-4"



def test_falls_back_to_default_model_mapping(monkeypatch):
    """A short name present only in the synchronized defaults must still resolve.

    This is the layer /v1/messages and /v1/models already consult, so without it
    a model added to the model-mappings repo is reachable on every route except
    /openai/v1/*, and nothing in the local configuration explains why.
    """
    monkeypatch.setattr(
        "app.core.config.settings.default_model_mapping",
        {"gpt-6-astra": "global.openai.gpt-6-astra"},
    )
    manager = MagicMock()
    manager.get_mapping.return_value = None

    assert resolve_model_id("gpt-6-astra", manager) == "global.openai.gpt-6-astra"


def test_dynamodb_mapping_wins_over_default_model_mapping(monkeypatch):
    """Per-deployment DynamoDB overrides keep priority over the defaults."""
    monkeypatch.setattr(
        "app.core.config.settings.default_model_mapping",
        {"gpt-6-astra": "global.openai.gpt-6-astra"},
    )
    manager = MagicMock()
    manager.get_mapping.return_value = "us.openai.gpt-6-astra"

    assert resolve_model_id("gpt-6-astra", manager) == "us.openai.gpt-6-astra"


def test_default_model_mapping_used_when_lookup_raises(monkeypatch):
    """A transient DynamoDB failure must not bypass the defaults.

    ModelMappingManager.get_mapping documents that callers are expected to fall
    back to the default mapping when the lookup fails. Returning the raw ID here
    would downgrade a resolvable short name into an unmapped ID sent upstream.
    """
    monkeypatch.setattr(
        "app.core.config.settings.default_model_mapping",
        {"gpt-6-astra": "global.openai.gpt-6-astra"},
    )
    manager = MagicMock()
    manager.get_mapping.side_effect = RuntimeError("ddb down")

    assert resolve_model_id("gpt-6-astra", manager) == "global.openai.gpt-6-astra"
