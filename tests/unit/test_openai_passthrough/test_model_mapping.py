"""Tests for resolve_model_id."""
from unittest.mock import MagicMock

from app.api.openai_passthrough.model_mapping import resolve_model_id


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


def test_falls_back_to_default_model_mapping():
    """A short name present only in the default mapping must still resolve.

    This is the layer /v1/messages and /v1/models already consult; without it a
    model added to the model-mappings repo is reachable everywhere except the
    /openai/v1/* routes.
    """
    from app.core.config import settings

    manager = MagicMock()
    manager.get_mapping.return_value = None

    original = settings.default_model_mapping
    try:
        settings.default_model_mapping = {"gpt-6-astra": "global.openai.gpt-6-astra"}
        out = resolve_model_id("gpt-6-astra", manager)
    finally:
        settings.default_model_mapping = original

    assert out == "global.openai.gpt-6-astra"


def test_dynamodb_mapping_wins_over_default_mapping():
    """Per-deployment DynamoDB overrides keep priority over the defaults."""
    from app.core.config import settings

    manager = MagicMock()
    manager.get_mapping.return_value = "openai.override-target"

    original = settings.default_model_mapping
    try:
        settings.default_model_mapping = {"gpt-6-astra": "global.openai.gpt-6-astra"}
        out = resolve_model_id("gpt-6-astra", manager)
    finally:
        settings.default_model_mapping = original

    assert out == "openai.override-target"


def test_default_mapping_used_when_dynamodb_lookup_fails():
    """A transient DynamoDB failure must not bypass the default mapping.

    ModelMappingManager.get_mapping documents that callers are expected to fall
    back to the default mapping when the lookup fails.
    """
    from app.core.config import settings

    manager = MagicMock()
    manager.get_mapping.side_effect = RuntimeError("ddb down")

    original = settings.default_model_mapping
    try:
        settings.default_model_mapping = {"gpt-6-astra": "global.openai.gpt-6-astra"}
        out = resolve_model_id("gpt-6-astra", manager)
    finally:
        settings.default_model_mapping = original

    assert out == "global.openai.gpt-6-astra"
