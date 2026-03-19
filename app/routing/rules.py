"""
Rule Engine — keyword, regex, and model-name based routing rules.
"""
import re
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RoutingRule:
    rule_id: str
    rule_name: str
    rule_type: str          # "keyword" | "regex" | "model"
    pattern: str            # comma-separated keywords, regex, or model names
    target_model: str
    target_provider: str = "bedrock"
    priority: int = 0
    is_enabled: bool = True


@dataclass
class RuleMatch:
    rule_name: str
    target_model: str
    target_provider: Optional[str] = None


class RuleEngine:
    """Priority-ordered rule matching for routing decisions."""

    def __init__(self):
        self._rules: List[RoutingRule] = []

    def load_rules(self, rules: List[RoutingRule]) -> None:
        self._rules = sorted(
            [r for r in rules if r.is_enabled],
            key=lambda r: r.priority,
        )

    def load_rules_from_items(self, items: List[dict]) -> None:
        rules = [
            RoutingRule(
                rule_id=item.get("rule_id", ""),
                rule_name=item.get("rule_name", ""),
                rule_type=item.get("rule_type", "keyword"),
                pattern=item.get("pattern", ""),
                target_model=item.get("target_model", ""),
                target_provider=item.get("target_provider", "bedrock"),
                priority=int(item.get("priority", 0)),
                is_enabled=item.get("is_enabled", True),
            )
            for item in items
        ]
        self.load_rules(rules)

    def match(self, user_message: str, request_model: str) -> Optional[RuleMatch]:
        """Return first matching rule or None."""
        for rule in self._rules:
            try:
                if rule.rule_type == "keyword":
                    keywords = [k.strip().lower() for k in rule.pattern.split(",") if k.strip()]
                    if any(kw in user_message.lower() for kw in keywords):
                        return RuleMatch(rule.rule_name, rule.target_model, rule.target_provider)

                elif rule.rule_type == "regex":
                    if re.search(rule.pattern, user_message):
                        return RuleMatch(rule.rule_name, rule.target_model, rule.target_provider)

                elif rule.rule_type == "model":
                    source_models = [m.strip() for m in rule.pattern.split(",") if m.strip()]
                    if request_model in source_models:
                        return RuleMatch(rule.rule_name, rule.target_model, rule.target_provider)
            except re.error as e:
                logger.warning("Invalid regex in rule %s: %s", rule.rule_name, e)
                continue
        return None
