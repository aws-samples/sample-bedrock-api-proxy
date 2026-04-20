"""Converter behavior for application inference profile ARNs."""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def converter():
    from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter

    conv = AnthropicToBedrockConverter.__new__(AnthropicToBedrockConverter)
    conv._resolved_model_id = None
    return conv


def test_is_claude_model_true_for_claude_backed_arn(converter, monkeypatch):
    from app.converters import anthropic_to_bedrock as mod

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/"
        "anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: fake_resolver)

    converter._resolved_model_id = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    assert converter._is_claude_model() is True


def test_is_claude_model_false_for_nova_backed_arn(converter, monkeypatch):
    from app.converters import anthropic_to_bedrock as mod

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
    )
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: fake_resolver)

    converter._resolved_model_id = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    assert converter._is_claude_model() is False


def test_supports_beta_header_mapping_for_claude_backed_arn(converter, monkeypatch):
    from app.converters import anthropic_to_bedrock as mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "beta_header_supported_models", ["claude"])

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/"
        "anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: fake_resolver)

    arn = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    converter._resolved_model_id = arn
    assert converter._supports_beta_header_mapping(arn) is True


def test_supports_beta_header_mapping_false_for_nova_backed_arn(converter, monkeypatch):
    from app.converters import anthropic_to_bedrock as mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "beta_header_supported_models", ["claude"])

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
    )
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: fake_resolver)

    arn = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    converter._resolved_model_id = arn
    assert converter._supports_beta_header_mapping(arn) is False
