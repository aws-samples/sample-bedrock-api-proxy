"""
LLM Provider abstraction layer.

Defines the base interface that all LLM providers must implement,
enabling a pluggable multi-provider architecture.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

from app.schemas.anthropic import MessageRequest, MessageResponse


@dataclass
class ProviderResponse:
    """Unified response wrapper from any provider."""
    response: MessageResponse
    provider_name: str
    model_used: str
    latency_ms: float


@dataclass
class ProviderStreamChunk:
    """A single chunk from a streaming provider response."""
    data: str
    provider_name: str


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier, e.g. 'bedrock', 'openai'."""
        ...

    @abstractmethod
    async def invoke(
        self,
        request: MessageRequest,
        model_id: str,
        api_key_info: Dict[str, Any],
        **kwargs,
    ) -> ProviderResponse:
        """Invoke model (non-streaming)."""
        ...

    @abstractmethod
    async def invoke_stream(
        self,
        request: MessageRequest,
        model_id: str,
        api_key_info: Dict[str, Any],
        **kwargs,
    ) -> AsyncIterator[str]:
        """Invoke model (streaming), yields SSE event strings."""
        ...

    @abstractmethod
    def supports_model(self, model_id: str) -> bool:
        """Check if this provider supports the given model."""
        ...

    @abstractmethod
    def get_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD for the given token counts."""
        ...

    @abstractmethod
    def list_models(self) -> List[Dict[str, Any]]:
        """Return list of available models from this provider."""
        ...
