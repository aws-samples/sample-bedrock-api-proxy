"""Model Mapping management routes."""
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, Query, status

from app.db.dynamodb import DynamoDBClient, ModelMappingManager
from app.core.config import settings
from admin_portal.backend.schemas.model_mapping import (
    ModelMappingCreate,
    ModelMappingUpdate,
    ModelMappingResponse,
    ModelMappingListResponse,
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

    Default mappings come from config.py, custom mappings from DynamoDB.
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

    # Add custom mappings
    for mapping in custom_mappings:
        updated_at_val = mapping.get("updated_at")
        items.append(ModelMappingResponse(
            anthropic_model_id=mapping.get("anthropic_model_id", ""),
            bedrock_model_id=mapping.get("bedrock_model_id", ""),
            source="custom",
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

    # Sort by source (default first) then by anthropic_model_id
    items.sort(key=lambda x: (0 if x.source == "default" else 1, x.anthropic_model_id))

    return ModelMappingListResponse(items=items, count=len(items))


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
        # Get full item for updated_at
        mappings = mapping_manager.list_mappings()
        for m in mappings:
            if m.get("anthropic_model_id") == anthropic_model_id:
                updated_at_val = m.get("updated_at")
                return ModelMappingResponse(
                    anthropic_model_id=anthropic_model_id,
                    bedrock_model_id=bedrock_id,
                    source="custom",
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

    # Get the created item
    mappings = mapping_manager.list_mappings()
    for m in mappings:
        if m.get("anthropic_model_id") == request.anthropic_model_id:
            updated_at_val = m.get("updated_at")
            return ModelMappingResponse(
                anthropic_model_id=request.anthropic_model_id,
                bedrock_model_id=request.bedrock_model_id,
                source="custom",
                updated_at=int(updated_at_val) if updated_at_val is not None else None,
            )

    return ModelMappingResponse(
        anthropic_model_id=request.anthropic_model_id,
        bedrock_model_id=request.bedrock_model_id,
        source="custom",
    )


@router.put("/{anthropic_model_id:path}", response_model=ModelMappingResponse)
async def update_model_mapping(anthropic_model_id: str, request: ModelMappingUpdate):
    """
    Update an existing custom model mapping.

    Cannot update default mappings - create a custom override instead.
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
                detail="Cannot update default mapping. Create a custom override with POST instead.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom mapping not found",
        )

    # Update the mapping
    mapping_manager.set_mapping(anthropic_model_id, request.bedrock_model_id)

    # Get updated item
    mappings = mapping_manager.list_mappings()
    for m in mappings:
        if m.get("anthropic_model_id") == anthropic_model_id:
            updated_at_val = m.get("updated_at")
            return ModelMappingResponse(
                anthropic_model_id=anthropic_model_id,
                bedrock_model_id=request.bedrock_model_id,
                source="custom",
                updated_at=int(updated_at_val) if updated_at_val is not None else None,
            )

    return ModelMappingResponse(
        anthropic_model_id=anthropic_model_id,
        bedrock_model_id=request.bedrock_model_id,
        source="custom",
    )


@router.delete("/{anthropic_model_id:path}")
async def delete_model_mapping(anthropic_model_id: str):
    """
    Delete a custom model mapping.

    Cannot delete default mappings.
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
                detail="Cannot delete default mapping",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom mapping not found",
        )

    mapping_manager.delete_mapping(anthropic_model_id)
    return {"message": "Mapping deleted successfully"}
