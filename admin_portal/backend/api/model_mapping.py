"""Model Mapping management routes."""
import asyncio
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

import httpx
from fastapi import APIRouter, HTTPException, Query, status

from app.db.dynamodb import DynamoDBClient, ModelMappingManager
from app.core.config import settings
from app.services.model_mapping_sync_service import get_sync_status, run_sync
from admin_portal.backend.schemas.model_mapping import (
    ModelMappingCreate,
    ModelMappingUpdate,
    ModelMappingResponse,
    ModelMappingListResponse,
    ModelMappingSyncRequest,
    ModelMappingSyncResponse,
    ModelMappingSyncStatus,
)

router = APIRouter()


def get_manager():
    """Get ModelMappingManager instance."""
    db_client = DynamoDBClient()
    return ModelMappingManager(db_client)


@router.get("", response_model=ModelMappingListResponse)
async def list_model_mappings(
    search: Optional[str] = Query(default=None),
):
    """
    List all model mappings (default + custom).

    Default mappings come from the remote model_mappings.json (synced
    in-process, see MODEL_MAPPING_SYNC_URL); custom mappings from DynamoDB.
    If same anthropic_model_id exists in both, custom takes priority.
    """
    mapping_manager = get_manager()

    # Get custom mappings from DynamoDB
    custom_mappings = mapping_manager.list_mappings()
    custom_ids = {m.get("anthropic_model_id") for m in custom_mappings}

    # Build combined list
    items = []

    # Add default mappings (only if not overridden by custom)
    for anthropic_id, bedrock_id in settings.default_model_mapping.items():
        if anthropic_id not in custom_ids:
            items.append(ModelMappingResponse(
                anthropic_model_id=anthropic_id,
                bedrock_model_id=bedrock_id,
                source="default",
            ))

    # Add custom mappings; ones that shadow a default are overrides
    for mapping in custom_mappings:
        anthropic_id = mapping.get("anthropic_model_id", "")
        default_bedrock_id = settings.default_model_mapping.get(anthropic_id)
        updated_at_val = mapping.get("updated_at")
        items.append(ModelMappingResponse(
            anthropic_model_id=anthropic_id,
            bedrock_model_id=mapping.get("bedrock_model_id", ""),
            source="override" if default_bedrock_id is not None else "custom",
            default_bedrock_model_id=default_bedrock_id,
            updated_at=int(updated_at_val) if updated_at_val is not None else None,
        ))

    # Apply search filter if provided
    if search:
        search_lower = search.lower()
        items = [
            item for item in items
            if search_lower in item.anthropic_model_id.lower()
            or search_lower in item.bedrock_model_id.lower()
        ]

    # Sort by source (default/override first) then by anthropic_model_id
    items.sort(key=lambda x: (1 if x.source == "custom" else 0, x.anthropic_model_id))

    return ModelMappingListResponse(items=items, count=len(items))


@router.get("/sync/status", response_model=ModelMappingSyncStatus)
async def model_mapping_sync_status():
    """Where the active default mapping came from and how the last refresh went."""
    return ModelMappingSyncStatus(**get_sync_status())


@router.post("/sync", response_model=ModelMappingSyncResponse)
async def sync_model_mappings(request: Optional[ModelMappingSyncRequest] = None):
    """
    Refresh default model mappings from the remote file (manual trigger).

    Replaces the in-process default mapping of *this* admin portal process;
    proxy workers refresh on their own schedule (MODEL_MAPPING_SYNC_INTERVAL_SECONDS).
    Use dry_run to preview changes.
    """
    request = request or ModelMappingSyncRequest()
    try:
        summary = await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_sync(url=request.url, dry_run=request.dry_run)
        )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch model mapping source: {e}",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    return ModelMappingSyncResponse(**summary)


@router.get("/{anthropic_model_id:path}", response_model=ModelMappingResponse)
async def get_model_mapping(anthropic_model_id: str):
    """
    Get a specific model mapping.
    """
    anthropic_model_id = unquote(anthropic_model_id)
    mapping_manager = get_manager()

    # Check custom mapping first
    bedrock_id = mapping_manager.get_mapping(anthropic_model_id)
    if bedrock_id:
        default_bedrock_id = settings.default_model_mapping.get(anthropic_model_id)
        # Get full item for updated_at
        mappings = mapping_manager.list_mappings()
        for m in mappings:
            if m.get("anthropic_model_id") == anthropic_model_id:
                updated_at_val = m.get("updated_at")
                return ModelMappingResponse(
                    anthropic_model_id=anthropic_model_id,
                    bedrock_model_id=bedrock_id,
                    source="override" if default_bedrock_id is not None else "custom",
                    default_bedrock_model_id=default_bedrock_id,
                    updated_at=int(updated_at_val) if updated_at_val is not None else None,
                )

    # Check default mapping
    if anthropic_model_id in settings.default_model_mapping:
        return ModelMappingResponse(
            anthropic_model_id=anthropic_model_id,
            bedrock_model_id=settings.default_model_mapping[anthropic_model_id],
            source="default",
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Model mapping not found",
    )


@router.post("", response_model=ModelMappingResponse, status_code=status.HTTP_201_CREATED)
async def create_model_mapping(request: ModelMappingCreate):
    """
    Create a new custom model mapping.

    Can override a default mapping by using the same anthropic_model_id.
    """
    mapping_manager = get_manager()

    # Check if custom mapping already exists
    existing = mapping_manager.get_mapping(request.anthropic_model_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Custom mapping for this model already exists. Use PUT to update.",
        )

    # Create the mapping
    mapping_manager.set_mapping(request.anthropic_model_id, request.bedrock_model_id)

    default_bedrock_id = settings.default_model_mapping.get(request.anthropic_model_id)
    source = "override" if default_bedrock_id is not None else "custom"

    # Get the created item
    mappings = mapping_manager.list_mappings()
    for m in mappings:
        if m.get("anthropic_model_id") == request.anthropic_model_id:
            updated_at_val = m.get("updated_at")
            return ModelMappingResponse(
                anthropic_model_id=request.anthropic_model_id,
                bedrock_model_id=request.bedrock_model_id,
                source=source,
                default_bedrock_model_id=default_bedrock_id,
                updated_at=int(updated_at_val) if updated_at_val is not None else None,
            )

    return ModelMappingResponse(
        anthropic_model_id=request.anthropic_model_id,
        bedrock_model_id=request.bedrock_model_id,
        source=source,
        default_bedrock_model_id=default_bedrock_id,
    )


@router.put("/{anthropic_model_id:path}", response_model=ModelMappingResponse)
async def update_model_mapping(anthropic_model_id: str, request: ModelMappingUpdate):
    """
    Update a model mapping.

    Updating a custom mapping changes it in place. Updating a default
    mapping writes a DynamoDB override (the default itself comes from the
    remote model_mappings.json and stays intact; delete the override to
    restore it).
    """
    anthropic_model_id = unquote(anthropic_model_id)
    mapping_manager = get_manager()

    existing = mapping_manager.get_mapping(anthropic_model_id)
    default_bedrock_id = settings.default_model_mapping.get(anthropic_model_id)
    if not existing and default_bedrock_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model mapping not found",
        )

    # Update custom mapping in place, or write an override shadowing the default
    mapping_manager.set_mapping(anthropic_model_id, request.bedrock_model_id)

    source = "override" if default_bedrock_id is not None else "custom"

    # Get updated item
    mappings = mapping_manager.list_mappings()
    for m in mappings:
        if m.get("anthropic_model_id") == anthropic_model_id:
            updated_at_val = m.get("updated_at")
            return ModelMappingResponse(
                anthropic_model_id=anthropic_model_id,
                bedrock_model_id=request.bedrock_model_id,
                source=source,
                default_bedrock_model_id=default_bedrock_id,
                updated_at=int(updated_at_val) if updated_at_val is not None else None,
            )

    return ModelMappingResponse(
        anthropic_model_id=anthropic_model_id,
        bedrock_model_id=request.bedrock_model_id,
        source=source,
        default_bedrock_model_id=default_bedrock_id,
    )


@router.delete("/{anthropic_model_id:path}")
async def delete_model_mapping(anthropic_model_id: str):
    """
    Delete a custom model mapping or override.

    Deleting an override restores the remote-defined default. Defaults
    themselves cannot be deleted here (edit the model-mappings repo instead).
    """
    anthropic_model_id = unquote(anthropic_model_id)
    mapping_manager = get_manager()

    # Check if custom mapping exists
    existing = mapping_manager.get_mapping(anthropic_model_id)
    if not existing:
        # Check if it's a default mapping
        if anthropic_model_id in settings.default_model_mapping:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete default mapping (defined in the remote "
                       "model_mappings.json). You can override it with PUT instead.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom mapping not found",
        )

    mapping_manager.delete_mapping(anthropic_model_id)
    restored = anthropic_model_id in settings.default_model_mapping
    return {
        "message": "Default mapping restored" if restored else "Mapping deleted successfully",
        "restored_default": restored,
    }
