"""Tests for InferenceProfileResolver."""
from unittest.mock import MagicMock, patch

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


APP_PROFILE_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/abcd1234"
)
UNDERLYING_MODEL = (
    "arn:aws:bedrock:us-east-1::foundation-model/"
    "anthropic.claude-sonnet-4-5-20250929-v1:0"
)


def _make_client_with_profile(model_arn: str = UNDERLYING_MODEL):
    client = MagicMock()
    client.get_inference_profile.return_value = {
        "inferenceProfileArn": APP_PROFILE_ARN,
        "inferenceProfileName": "test-profile",
        "models": [{"modelArn": model_arn}],
        "type": "APPLICATION",
    }
    return client


def test_application_profile_calls_bedrock_and_returns_underlying_model():
    client = _make_client_with_profile()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    result = resolver.resolve(APP_PROFILE_ARN)

    assert result == UNDERLYING_MODEL
    client.get_inference_profile.assert_called_once_with(
        inferenceProfileIdentifier=APP_PROFILE_ARN
    )


def test_application_profile_result_is_cached():
    client = _make_client_with_profile()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    resolver.resolve(APP_PROFILE_ARN)
    resolver.resolve(APP_PROFILE_ARN)
    resolver.resolve(APP_PROFILE_ARN)

    assert client.get_inference_profile.call_count == 1


def test_resolution_error_on_client_exception():
    from botocore.exceptions import ClientError

    client = MagicMock()
    client.get_inference_profile.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetInferenceProfile",
    )
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    with pytest.raises(InferenceProfileResolutionError) as excinfo:
        resolver.resolve(APP_PROFILE_ARN)

    assert excinfo.value.arn == APP_PROFILE_ARN
    assert isinstance(excinfo.value.cause, ClientError)


def test_resolution_error_on_empty_models():
    client = MagicMock()
    client.get_inference_profile.return_value = {"models": []}
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    with pytest.raises(InferenceProfileResolutionError):
        resolver.resolve(APP_PROFILE_ARN)


def test_ttl_expiry_triggers_refetch():
    client = _make_client_with_profile()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    with patch("app.services.inference_profile_resolver.time") as mock_time:
        mock_time.time.return_value = 1000.0
        resolver.resolve(APP_PROFILE_ARN)
        mock_time.time.return_value = 1000.0 + 120  # past TTL
        resolver.resolve(APP_PROFILE_ARN)

    assert client.get_inference_profile.call_count == 2


def test_get_inference_profile_resolver_returns_singleton(monkeypatch):
    # Ensure fresh module state.
    import app.services.inference_profile_resolver as mod
    monkeypatch.setattr(mod, "_resolver_instance", None)

    fake_client = MagicMock()
    monkeypatch.setattr(mod, "_build_bedrock_client", lambda: fake_client)

    a = mod.get_inference_profile_resolver()
    b = mod.get_inference_profile_resolver()

    assert a is b
    assert a._client is fake_client
