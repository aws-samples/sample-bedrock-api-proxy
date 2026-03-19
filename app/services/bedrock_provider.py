"""
Bedrock Provider — wraps existing BedrockService as an LLMProvider.

Zero-modification wrapper: delegates all calls to BedrockService,
preserving streaming, prompt caching, extended thinking, service tiers, etc.
"""
import time
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from app.core.config import settings
from app.services.provider_base import LLMProvider, ProviderResponse
from app.schemas.anthropic import MessageRequest, MessageResponse

logger = logging.getLogger(__name__)


class BedrockProvider(LLMProvider):
    """Wraps BedrockService behind the LLMProvider interface."""

    def __init__(self, bedrock_service, pricing_manager=None):
        self._service = bedrock_service
        self._pricing = pricing_manager

    @property
    def name(self) -> str:
        return "bedrock"

    async def invoke(
        self,
        request: MessageRequest,
        model_id: str,
        api_key_info: Dict[str, Any],
        **kwargs,
    ) -> ProviderResponse:
        service_tier = api_key_info.get("service_tier", "default") if api_key_info else "default"
        anthropic_beta = kwargs.get("anthropic_beta")
        cache_ttl = kwargs.get("cache_ttl")
        request_id = kwargs.get("request_id")

        start = time.monotonic()
        response = await self._service.invoke_model(
            request,
            request_id=request_id,
            service_tier=service_tier,
            anthropic_beta=anthropic_beta,
            cache_ttl=cache_ttl,
        )
        latency = (time.monotonic() - start) * 1000

        return ProviderResponse(
            response=response,
            provider_name="bedrock",
            model_used=model_id,
            latency_ms=latency,
        )

    async def invoke_stream(
        self,
        request: MessageRequest,
        model_id: str,
        api_key_info: Dict[str, Any],
        **kwargs,
    ) -> AsyncIterator[str]:
        service_tier = api_key_info.get("service_tier", "default") if api_key_info else "default"
        anthropic_beta = kwargs.get("anthropic_beta")
        cache_ttl = kwargs.get("cache_ttl")
        request_id = kwargs.get("request_id")

        async for chunk in self._service.invoke_model_stream(
            request,
            request_id=request_id,
            service_tier=service_tier,
            anthropic_beta=anthropic_beta,
            cache_ttl=cache_ttl,
        ):
            yield chunk

    def supports_model(self, model_id: str) -> bool:
        # Check default mapping
        if model_id in settings.default_model_mapping:
            return True
        # Check if it looks like a Bedrock model ID (pass-through)
        if "." in model_id:
            return True
        return False

    def get_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        if not self._pricing:
            return 0.0
        try:
            pricing = self._pricing.get_pricing(model_id)
            if not pricing:
                return 0.0
            input_price = float(pricing.get("input_price", 0))
            output_price = float(pricing.get("output_price", 0))
            return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
        except Exception:
            return 0.0

    def list_models(self) -> List[Dict[str, Any]]:
        try:
            return self._service.list_available_models()
        except Exception as e:
            logger.warning(f"Failed to list Bedrock models: {e}")
            return []
