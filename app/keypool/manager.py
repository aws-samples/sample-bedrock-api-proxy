"""
Key Pool Manager — round-robin rotation with cooldown tracking.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.keypool.encryption import KeyEncryption

logger = logging.getLogger(__name__)


@dataclass
class KeyState:
    """Runtime state for a single provider API key."""
    provider_key_id: str
    provider: str
    encrypted_key: str
    models: List[str]
    is_enabled: bool = True
    cooldown_until: float = 0.0
    request_count: int = 0


class KeyPoolManager:
    """Manages provider API keys with round-robin rotation and cooldown."""

    def __init__(self, encryption: Optional[KeyEncryption] = None):
        self._encryption = encryption
        self._keys: Dict[str, List[KeyState]] = {}  # provider -> [KeyState]
        self._rr_index: Dict[str, int] = {}

    def load_keys_from_items(self, items: List[dict]) -> None:
        """Load keys from DynamoDB items into memory."""
        self._keys.clear()
        for item in items:
            provider = item.get("provider", "")
            state = KeyState(
                provider_key_id=item.get("key_id", ""),
                provider=provider,
                encrypted_key=item.get("encrypted_api_key", ""),
                models=item.get("models", []),
                is_enabled=item.get("is_enabled", True),
            )
            self._keys.setdefault(provider, []).append(state)

    def get_available_key(self, provider: str, model_id: str) -> Optional[Tuple[str, str]]:
        """
        Round-robin select an available key.
        Returns (decrypted_key, key_id) or None.
        """
        keys = self._keys.get(provider, [])
        now = time.time()
        available = [
            k for k in keys
            if k.is_enabled and model_id in k.models and k.cooldown_until < now
        ]
        if not available:
            return None

        idx = self._rr_index.get(provider, 0) % len(available)
        self._rr_index[provider] = idx + 1
        chosen = available[idx]
        chosen.request_count += 1

        if not self._encryption:
            return (chosen.encrypted_key, chosen.provider_key_id)
        try:
            decrypted = self._encryption.decrypt(chosen.encrypted_key)
            return (decrypted, chosen.provider_key_id)
        except Exception:
            logger.error("Failed to decrypt key %s", chosen.provider_key_id)
            chosen.is_enabled = False
            return self.get_available_key(provider, model_id)

    def mark_rate_limited(self, provider: str, key_id: str,
                          retry_after: Optional[int] = None) -> None:
        """Mark a key as rate-limited with cooldown."""
        cooldown = retry_after if retry_after is not None else 60
        for key in self._keys.get(provider, []):
            if key.provider_key_id == key_id:
                key.cooldown_until = time.time() + cooldown
                logger.info("Key %s cooldown for %ds", key_id, cooldown)
                break

    def mark_preemptive_cooldown(self, provider: str, key_id: str) -> None:
        """Preemptive cooldown when x-ratelimit-remaining=0."""
        self.mark_rate_limited(provider, key_id, retry_after=60)

    def has_available_keys(self, provider: str, model_id: str) -> bool:
        now = time.time()
        keys = self._keys.get(provider, [])
        return any(
            k.is_enabled and model_id in k.models and k.cooldown_until < now
            for k in keys
        )
