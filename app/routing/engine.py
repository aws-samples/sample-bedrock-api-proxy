"""
Routing Engine — orchestrates rule, cost, quality, and smart routing.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from app.core.exceptions import NoProviderAvailableError
from app.routing.rules import RuleEngine

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    provider: str
    model: str
    reason: str


class RoutingEngine:
    """Decides which provider/model to route a request to."""

    def __init__(self, rule_engine: RuleEngine, smart_router=None,
                 provider_registry=None, pricing_manager=None,
                 cache_aware_routing: bool = True):
        self._rules = rule_engine
        self._smart = smart_router
        self._registry = provider_registry
        self._pricing = pricing_manager
        self._cache_aware = cache_aware_routing

    def route(self, request_model: str, user_message: str,
              api_key_info: dict, is_cache_active: bool = False) -> RoutingDecision:
        strategy = api_key_info.get("routing_strategy", "off") if api_key_info else "off"

        if strategy == "off":
            return RoutingDecision("bedrock", request_model, "routing_off")

        # 0. Cache affinity — if prompt cache is active, stick with the requested model
        if self._cache_aware and is_cache_active:
            logger.info("Cache affinity: keeping model %s (cache-active session)", request_model)
            return RoutingDecision("bedrock", request_model, "cache_affinity")

        # 1. Rule engine first
        rule_match = self._rules.match(user_message, request_model)
        if rule_match:
            logger.info("Rule matched: %s -> %s", rule_match.rule_name, rule_match.target_model)
            return RoutingDecision(
                rule_match.target_provider or "bedrock",
                rule_match.target_model,
                f"rule:{rule_match.rule_name}",
            )

        # 2. Budget degradation
        if strategy != "off" and self._should_degrade(api_key_info):
            weak = self._smart.weak_model if self._smart else request_model
            return RoutingDecision("bedrock", weak, "budget_degradation")

        # 3. Strategy-based routing
        if strategy == "cost":
            return self._route_by_cost()
        elif strategy == "quality":
            return self._route_by_quality()
        elif strategy == "auto":
            return self._route_by_smart(user_message)

        return RoutingDecision("bedrock", request_model, "fallback")

    def _should_degrade(self, api_key_info: dict) -> bool:
        budget = float(api_key_info.get("monthly_budget", 0) or 0)
        used = float(api_key_info.get("budget_used_mtd", 0) or 0)
        if budget <= 0:
            return False
        return (used / budget) >= 0.8

    def _route_by_cost(self) -> RoutingDecision:
        if not self._pricing:
            raise NoProviderAvailableError("No pricing data for cost routing")
        items = self._pricing.list_all_pricing().get("items", [])
        scored = []
        for p in items:
            if p.get("status") == "deprecated":
                continue
            cost = (1000 * float(p.get("input_price", 0)) +
                    500 * float(p.get("output_price", 0))) / 1_000_000
            scored.append((cost, p.get("model_id", ""), p.get("provider", "bedrock")))
        scored.sort(key=lambda x: x[0])
        for cost, model, provider in scored:
            if self._registry and self._registry.get_providers_for_model(model):
                return RoutingDecision(provider, model, f"cost:{cost:.6f}")
        raise NoProviderAvailableError("No available model for cost routing")

    def _route_by_quality(self) -> RoutingDecision:
        if not self._pricing:
            raise NoProviderAvailableError("No pricing data for quality routing")
        items = self._pricing.list_all_pricing().get("items", [])
        scored = []
        for p in items:
            if p.get("status") == "deprecated":
                continue
            cost = (1000 * float(p.get("input_price", 0)) +
                    500 * float(p.get("output_price", 0))) / 1_000_000
            scored.append((cost, p.get("model_id", ""), p.get("provider", "bedrock")))
        scored.sort(key=lambda x: x[0], reverse=True)
        for cost, model, provider in scored:
            if self._registry and self._registry.get_providers_for_model(model):
                return RoutingDecision(provider, model, f"quality:{model}")
        raise NoProviderAvailableError("No available model for quality routing")

    def _route_by_smart(self, user_message: str) -> RoutingDecision:
        if not self._smart:
            return RoutingDecision("bedrock", "claude-sonnet-4-5-20250929", "smart:no_router")
        complexity = self._smart.classify(user_message)
        if complexity == "high":
            return RoutingDecision("bedrock", self._smart.strong_model, "smart:high")
        return RoutingDecision("bedrock", self._smart.weak_model, "smart:low")
