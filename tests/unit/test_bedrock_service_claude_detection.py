"""Verify _is_claude_model resolves application inference profile ARNs."""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def service(monkeypatch):
    """Build a BedrockService without running its real __init__."""
    from app.services.bedrock_service import BedrockService

    svc = BedrockService.__new__(BedrockService)
    return svc


def test_is_claude_model_plain_id(service):
    assert service._is_claude_model("claude-sonnet-4-5-20250929") is True
    assert service._is_claude_model("amazon.nova-pro-v1:0") is False


def test_is_claude_model_resolves_claude_backed_profile(service, monkeypatch):
    from app.services import bedrock_service as bs_mod

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/"
        "anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    monkeypatch.setattr(
        bs_mod, "get_inference_profile_resolver", lambda: fake_resolver
    )

    arn = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    assert service._is_claude_model(arn) is True
    fake_resolver.resolve.assert_called_once_with(arn)


def test_is_claude_model_resolves_nova_backed_profile(service, monkeypatch):
    from app.services import bedrock_service as bs_mod

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
    )
    monkeypatch.setattr(
        bs_mod, "get_inference_profile_resolver", lambda: fake_resolver
    )

    arn = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    assert service._is_claude_model(arn) is False
