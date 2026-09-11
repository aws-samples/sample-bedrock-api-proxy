"""Model ID resolution for the OpenAI passthrough endpoints.

Resolves the client-supplied model through the same layers the Anthropic
surface uses, in the same order:

1. the ``model_mapping`` DynamoDB table (per-deployment overrides), then
2. ``settings.default_model_mapping`` (the defaults fetched from the
   model-mappings repo by ``model_mapping_sync_service``, layered with any
   ``DEFAULT_MODEL_MAPPING`` env entries), then
3. pass through unchanged, so callers can use Bedrock-native IDs
   (e.g. ``openai.gpt-oss-120b``) directly without registering them.

Layer 2 used to be missing here, which made the passthrough endpoints
inconsistent with ``/v1/messages`` and ``/v1/models``: a short name that existed
only in the default mapping resolved fine on those routes but was forwarded
verbatim from here, and the upstream rejected it. Any model added to the
model-mappings repo was therefore reachable by short name everywhere except the
``/openai/v1/*`` routes.
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def resolve_model_id(model: str, model_mapping_manager) -> str:
    """Resolve a client-supplied model ID via the mapping layers, with fallback.

    Args:
        model: The ``model`` field from the client request.
        model_mapping_manager: An app.db.dynamodb.ModelMappingManager instance.

    Returns:
        The resolved Bedrock model ID, or the original string if no mapping
        exists in any layer.
    """
    if not model:
        return model

    mapped = None
    try:
        mapped = model_mapping_manager.get_mapping(model)
    except Exception as exc:
        # Deliberately fall through to the default mapping rather than
        # returning early: ModelMappingManager.get_mapping documents that a
        # transient DynamoDB failure is expected to "fall back to default
        # mapping upstream", and returning the raw ID here would send an
        # unmapped short name to the provider.
        logger.warning("[OPENAI-PASSTHROUGH] model mapping lookup failed for %r: %s", model, exc)

    if mapped:
        return mapped

    default = settings.default_model_mapping.get(model)
    if default:
        return default

    return model
