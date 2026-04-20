"""Billing uses resolved underlying model ID for pricing lookup."""
from unittest.mock import MagicMock

import pytest


APP_PROFILE_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/abcd"
)
UNDERLYING_MODEL_ARN = (
    "arn:aws:bedrock:us-east-1::foundation-model/"
    "anthropic.claude-sonnet-4-5-20250929-v1:0"
)


def test_get_cost_uses_resolved_model_for_pricing(monkeypatch):
    from app.services import bedrock_provider as mod
    from app.services.bedrock_provider import BedrockProvider

    pricing = MagicMock()
    pricing.get_pricing.return_value = {
        "input_price": 3.0,
        "output_price": 15.0,
    }

    resolver = MagicMock()
    resolver.resolve.return_value = UNDERLYING_MODEL_ARN
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: resolver)

    provider = BedrockProvider(bedrock_service=MagicMock(), pricing_manager=pricing)

    cost = provider.get_cost(APP_PROFILE_ARN, input_tokens=1000, output_tokens=500)

    resolver.resolve.assert_called_once_with(APP_PROFILE_ARN)
    pricing.get_pricing.assert_called_once_with(UNDERLYING_MODEL_ARN)
    # cost = (1000 * 3 + 500 * 15) / 1_000_000 = 0.0105
    assert cost == pytest.approx(0.0105, rel=1e-6)
