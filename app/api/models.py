"""
Models API endpoints.

Implements GET /v1/models for listing models supported by this proxy.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.config import settings
from app.db.dynamodb import DynamoDBClient, ModelMappingManager
from app.services.bedrock_service import BedrockService

logger = logging.getLogger(__name__)

router = APIRouter()


# Stable placeholder created_at for Anthropic-style responses.
# The proxy does not track when each model was first supported, so a
# fixed value keeps responses deterministic.
_DEFAULT_CREATED_AT = "2024-01-01T00:00:00Z"


def get_bedrock_service() -> BedrockService:
    """Get Bedrock service instance."""
    return BedrockService()


def _humanize_model_id(model_id: str) -> str:
    """Convert a short model ID into a human-readable display name.

    Examples:
        claude-opus-4-7 -> Claude Opus 4.7
        claude-sonnet-4-5-20250929 -> Claude Sonnet 4.5
        claude-3-5-haiku-20241022 -> Claude Haiku 3.5
    """
    parts = model_id.split("-")
    # Drop trailing date-like segment (8-digit YYYYMMDD)
    if parts and re.fullmatch(r"\d{8}", parts[-1]):
        parts = parts[:-1]

    words: list[str] = []
    numeric_buffer: list[str] = []

    for part in parts:
        if part.isdigit():
            numeric_buffer.append(part)
        else:
            if numeric_buffer:
                words.append(".".join(numeric_buffer))
                numeric_buffer = []
            words.append(part.capitalize())
    if numeric_buffer:
        words.append(".".join(numeric_buffer))

    display = " ".join(words)
    # Promote "Claude" capitalization in case the id doesn't start with it
    if not display.lower().startswith("claude"):
        display = f"Claude {display}"
    return display


def _collect_supported_model_ids() -> list[str]:
    """Return all Anthropic-format model IDs this proxy supports.

    Sources:
      1. `settings.default_model_mapping` keys (compiled-in defaults).
      2. DynamoDB custom mappings (admin portal overrides).

    If DynamoDB is unavailable the defaults are still returned so the
    endpoint degrades gracefully.
    """
    ids: set[str] = set(settings.default_model_mapping.keys())

    try:
        db_client = DynamoDBClient()
        manager = ModelMappingManager(db_client)
        for item in manager.list_mappings():
            anthropic_id = item.get("anthropic_model_id")
            if anthropic_id:
                ids.add(anthropic_id)
    except Exception as exc:
        logger.warning("Could not load custom model mappings from DynamoDB: %s", exc)

    return sorted(ids)


def _build_model_entry(model_id: str) -> dict[str, str]:
    """Build an Anthropic-style model descriptor."""
    return {
        "type": "model",
        "id": model_id,
        "display_name": _humanize_model_id(model_id),
        "created_at": _DEFAULT_CREATED_AT,
    }


@router.get(
    "/models",
    summary="List supported models",
    description=(
        "List Anthropic-format model IDs this proxy supports, sourced from "
        "the default config mapping and any DynamoDB custom mappings."
    ),
)
async def list_models(request: Request):
    """List supported Anthropic-format model IDs.

    When ``MULTI_PROVIDER_ENABLED=true`` and a provider registry is
    available, that multi-provider aggregation is preserved. Otherwise the
    response reflects only the Anthropic mapping keys this proxy routes.
    """
    try:
        if settings.multi_provider_enabled:
            provider_registry = getattr(request.app.state, "provider_registry", None)
            if provider_registry:
                models = provider_registry.list_all_models()
                return {
                    "object": "list",
                    "data": models,
                    "has_more": False,
                }

        data = [_build_model_entry(mid) for mid in _collect_supported_model_ids()]
        return {
            "object": "list",
            "data": data,
            "has_more": False,
        }

    except Exception as e:
        logger.error(f"Failed to list models: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "internal_error",
                "message": "Failed to list models due to an internal error",
            },
        ) from e


@router.get(
    "/models/{model_id}",
    summary="Get model information",
    description="Return metadata for a supported Anthropic-format model ID.",
)
async def get_model(
    model_id: str,
    bedrock_service: BedrockService = Depends(get_bedrock_service),
):
    """Return metadata for a supported model ID.

    If ``model_id`` matches a supported Anthropic-format ID we return an
    Anthropic-style descriptor. Otherwise we fall back to the Bedrock
    foundation-model lookup for backwards compatibility.
    """
    try:
        if model_id in set(_collect_supported_model_ids()):
            return _build_model_entry(model_id)

        # Fall back to Bedrock foundation-model lookup so that clients that
        # still pass raw Bedrock IDs keep working. Treat lookup failures as
        # "not found" rather than propagating as 500s.
        try:
            model_info = bedrock_service.get_model_info(model_id)
        except Exception as lookup_err:
            logger.debug("Bedrock model lookup failed for %s: %s", model_id, lookup_err)
            model_info = None

        if model_info:
            return {"object": "model", **model_info}

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "type": "not_found_error",
                "message": f"Model {model_id} not found",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get model info for {model_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "internal_error",
                "message": "Failed to get model info due to an internal error",
            },
        ) from e
