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
