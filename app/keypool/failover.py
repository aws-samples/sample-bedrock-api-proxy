"""
Failover Manager — cross-model failover when all keys are rate-limited.
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.keypool.manager import KeyPoolManager

logger = logging.getLogger(__name__)


@dataclass
class FailoverTarget:
    """A single failover destination."""
    provider: str
    model: str


class FailoverManager:
    """Manages failover chains: when all keys for a model are exhausted,
    try the next model in the configured chain."""

    def __init__(self, key_pool: KeyPoolManager):
        self._key_pool = key_pool
        self._chains: Dict[str, List[FailoverTarget]] = {}  # source_model -> targets

    def load_chains_from_items(self, items: List[dict]) -> None:
        """Load failover chains from DynamoDB items."""
        self._chains.clear()
        for item in items:
            source = item.get("source_model", "")
            targets = [
                FailoverTarget(provider=t.get("provider", "bedrock"), model=t.get("model", ""))
                for t in item.get("targets", [])
            ]
            if source and targets:
                self._chains[source] = targets

    def load_chains_from_dict(self, chains: Dict[str, List[dict]]) -> None:
        """Load from a simple dict (e.g. from env var)."""
        self._chains.clear()
        for source, targets in chains.items():
            self._chains[source] = [
                FailoverTarget(
                    provider=t.get("provider", "bedrock") if isinstance(t, dict) else "bedrock",
                    model=t.get("model", t) if isinstance(t, dict) else t,
                )
                for t in targets
            ]

    def find_failover(self, source_model: str) -> Optional[Tuple[str, str, str, str]]:
        """
        Find first available failover target.
        Returns (decrypted_key, key_id, target_provider, target_model) or None.
        """
        targets = self._chains.get(source_model, [])
        for target in targets:
            key_result = self._key_pool.get_available_key(target.provider, target.model)
            if key_result:
                decrypted_key, key_id = key_result
                logger.info(
                    "Failover: %s -> %s/%s",
                    source_model, target.provider, target.model,
                )
                return (decrypted_key, key_id, target.provider, target.model)
        return None
