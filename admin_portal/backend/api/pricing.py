"""Model Pricing management routes."""
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, Query, status

from app.db.dynamodb import DynamoDBClient, ModelPricingManager
from admin_portal.backend.schemas.pricing import (
    PricingCreate,
    PricingUpdate,
    PricingResponse,
    PricingListResponse,
)

router = APIRouter()


def get_manager():
    """Get ModelPricingManager instance."""
    db_client = DynamoDBClient()
    return ModelPricingManager(db_client)


@router.get("", response_model=PricingListResponse)
async def list_pricing(
    limit: int = Query(default=50, ge=1, le=100),
    provider: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    search: Optional[str] = Query(default=None),
):
    """
    List all model pricing with pagination and filtering.

    Args:
        limit: Maximum number of items to return (1-100)
        provider: Filter by provider name
        status_filter: Filter by status ('active', 'deprecated', 'disabled')
        search: Search term for filtering by model ID
    """
    pricing_manager = get_manager()

    result = pricing_manager.list_all_pricing(
        limit=limit,
        provider_filter=provider,
        status_filter=status_filter,
    )

    items = result.get("items", [])

    # Apply search filter if provided
    if search:
        search_lower = search.lower()
        items = [
            item for item in items
            if search_lower in item.get("model_id", "").lower()
            or search_lower in (item.get("display_name") or "").lower()
        ]

    return PricingListResponse(
        items=[PricingResponse(**item) for item in items],
        count=len(items),
        last_key=result.get("last_key"),
    )


@router.get("/providers")
async def list_providers():
    """
    Get list of unique providers.
    """
    pricing_manager = get_manager()

    result = pricing_manager.list_all_pricing(limit=1000)
    items = result.get("items", [])

    providers = list(set(item.get("provider", "Unknown") for item in items))
    providers.sort()

    return {"providers": providers}


@router.get("/{model_id:path}", response_model=PricingResponse)
async def get_pricing(model_id: str):
    """
    Get pricing for a specific model.

    Args:
        model_id: The Bedrock model ID
    """
    pricing_manager = get_manager()

    item = pricing_manager.get_pricing(model_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model pricing not found",
        )

    return PricingResponse(**item)


@router.post("", response_model=PricingResponse, status_code=status.HTTP_201_CREATED)
async def create_pricing(request: PricingCreate):
    """
    Create new model pricing.

    Args:
        request: Pricing creation data
    """
    pricing_manager = get_manager()

    # Check if already exists
    existing = pricing_manager.get_pricing(request.model_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pricing for this model already exists",
        )

    item = pricing_manager.create_pricing(
        model_id=request.model_id,
        provider=request.provider,
        display_name=request.display_name,
        input_price=request.input_price,
        output_price=request.output_price,
        cache_read_price=request.cache_read_price,
        cache_write_price=request.cache_write_price,
        status=request.status,
    )

    return PricingResponse(**item)


@router.put("/{model_id:path}", response_model=PricingResponse)
async def update_pricing(model_id: str, request: PricingUpdate):
    """
    Update model pricing.

    Args:
        model_id: The Bedrock model ID
        request: Fields to update
    """
    pricing_manager = get_manager()

    # Check if exists
    existing = pricing_manager.get_pricing(model_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model pricing not found",
        )

    # Update pricing
    update_data = request.model_dump(exclude_none=True)
    if update_data:
        success = pricing_manager.update_pricing(model_id, **update_data)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update pricing",
            )

    # Return updated pricing
    item = pricing_manager.get_pricing(model_id)
    return PricingResponse(**item)


@router.delete("/{model_id:path}")
async def delete_pricing(model_id: str):
    """
    Delete model pricing.

    Args:
        model_id: The Bedrock model ID
    """
    pricing_manager = get_manager()

    # Check if exists
    existing = pricing_manager.get_pricing(model_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model pricing not found",
        )

    success = pricing_manager.delete_pricing(model_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete pricing",
        )

    return {"message": "Pricing deleted successfully"}
