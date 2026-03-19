"""
Provider Registry — manages all registered LLM provider instances.
"""
from typing import Any, Dict, List, Optional

from app.services.provider_base import LLMProvider


class ProviderRegistry:
    """Manages registered providers and resolves models to providers."""

    def __init__(self):
        self._providers: Dict[str, LLMProvider] = {}

    def register(self, provider: LLMProvider) -> None:
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def get_provider(self, name: str) -> Optional[LLMProvider]:
        return self._providers.get(name)

    def get_providers_for_model(self, model_id: str) -> List[LLMProvider]:
        return [p for p in self._providers.values() if p.supports_model(model_id)]

    def list_all_models(self) -> List[Dict[str, Any]]:
        models: List[Dict[str, Any]] = []
        for provider in self._providers.values():
            for model in provider.list_models():
                model["provider"] = provider.name
                models.append(model)
        return models

    def all_providers(self) -> List[LLMProvider]:
        return list(self._providers.values())
