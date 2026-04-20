"""Tests for InferenceProfileResolver."""
from unittest.mock import MagicMock

import pytest

from app.services.inference_profile_resolver import (
    InferenceProfileResolver,
    InferenceProfileResolutionError,
)


def test_non_arn_passes_through():
    client = MagicMock()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    result = resolver.resolve("claude-sonnet-4-5-20250929")

    assert result == "claude-sonnet-4-5-20250929"
    client.get_inference_profile.assert_not_called()


def test_system_defined_profile_passes_through():
    client = MagicMock()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    result = resolver.resolve("us.anthropic.claude-sonnet-4-5-20250929-v1:0")

    assert result == "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    client.get_inference_profile.assert_not_called()


def test_bedrock_foundation_model_id_passes_through():
    client = MagicMock()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    result = resolver.resolve("global.anthropic.claude-opus-4-7-v1")

    assert result == "global.anthropic.claude-opus-4-7-v1"
    client.get_inference_profile.assert_not_called()
