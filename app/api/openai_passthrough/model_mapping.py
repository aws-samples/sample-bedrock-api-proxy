"""Model ID resolution for the OpenAI passthrough endpoints.

Looks up deployment overrides first, then synchronized default mappings.
Unknown IDs pass through so callers can use Bedrock-native IDs directly.
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def resolve_model_id(model: str, model_mapping_manager) -> str:
    """Resolve a client-supplied model ID via the mapping table, with fallback.

    Args:
        model: The ``model`` field from the client request.
        model_mapping_manager: An app.db.dynamodb.ModelMappingManager instance.

    Returns:
        The resolved Bedrock model ID, or the original string if neither the
        deployment override nor the synchronized defaults contain a mapping.
    """
    if not model:
        return model
    try:
        mapped = model_mapping_manager.get_mapping(model)
    except Exception as exc:
        logger.warning("[OPENAI-PASSTHROUGH] model mapping lookup failed for %r: %s", model, exc)
        mapped = None
    return mapped or settings.default_model_mapping.get(model, model)
