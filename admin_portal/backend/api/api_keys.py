"""API Keys management routes."""
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, Query, status

from app.db.dynamodb import DynamoDBClient, APIKeyManager, UsageTracker, UsageStatsManager
from admin_portal.backend.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyUpdate,
    ApiKeyResponse,
    ApiKeyListResponse,
)

router = APIRouter()


def get_managers():
    """Get DynamoDB managers."""
    db_client = DynamoDBClient()
    return APIKeyManager(db_client), UsageTracker(db_client), UsageStatsManager(db_client)


@router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(
    limit: int = Query(default=20, ge=1, le=100),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    search: Optional[str] = Query(default=None),
):
    """
    List all API keys with pagination and filtering.

    Args:
        limit: Maximum number of items to return (1-100)
        status_filter: Filter by status ('active', 'revoked', or None for all)
        search: Search term for filtering by name or key prefix
    """
    api_key_manager, _, usage_stats_manager = get_managers()

    result = api_key_manager.list_all_api_keys(
        limit=limit,
        status_filter=status_filter,
    )

    items = result.get("items", [])

    # Apply search filter if provided
    if search:
        search_lower = search.lower()
        items = [
            item for item in items
            if search_lower in item.get("name", "").lower()
            or search_lower in item.get("api_key", "").lower()
            or search_lower in item.get("owner_name", "").lower()
            or search_lower in item.get("user_id", "").lower()
        ]

    # Add usage stats to each item
    for item in items:
        stats = usage_stats_manager.get_stats(item.get("api_key", ""))
        if stats:
            item["total_input_tokens"] = int(stats.get("total_input_tokens", 0))
            item["total_output_tokens"] = int(stats.get("total_output_tokens", 0))
            item["total_cached_tokens"] = int(stats.get("total_cached_tokens", 0))
            item["total_cache_write_tokens"] = int(stats.get("total_cache_write_tokens", 0))
            item["total_requests"] = int(stats.get("total_requests", 0))
        else:
            item["total_input_tokens"] = 0
            item["total_output_tokens"] = 0
            item["total_cached_tokens"] = 0
            item["total_cache_write_tokens"] = 0
            item["total_requests"] = 0

    return ApiKeyListResponse(
        items=[ApiKeyResponse(**item) for item in items],
        count=len(items),
        last_key=result.get("last_key"),
    )


@router.get("/{api_key}", response_model=ApiKeyResponse)
async def get_api_key(api_key: str):
    """
    Get details of a specific API key.

    Args:
        api_key: The API key to retrieve
    """
    api_key_manager, _, _ = get_managers()

    item = api_key_manager.get_api_key(api_key)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    return ApiKeyResponse(**item)


@router.post("", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(request: ApiKeyCreate):
    """
    Create a new API key.

    Args:
        request: API key creation data
    """
    api_key_manager, _, _ = get_managers()

    new_key = api_key_manager.create_api_key(
        user_id=request.user_id,
        name=request.name,
        owner_name=request.owner_name,
        role=request.role,
        monthly_budget=request.monthly_budget,
        rate_limit=request.rate_limit,
        service_tier=request.service_tier,
        cache_ttl=request.cache_ttl,
    )

    # Get the created key details
    item = api_key_manager.get_api_key(new_key)
    return ApiKeyResponse(**item)


@router.put("/{api_key}", response_model=ApiKeyResponse)
async def update_api_key(api_key: str, request: ApiKeyUpdate):
    """
    Update an existing API key.

    Args:
        api_key: The API key to update
        request: Fields to update
    """
    api_key_manager, _, _ = get_managers()

    # Check if key exists
    existing = api_key_manager.get_api_key(api_key)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    # Update the key
    update_data = request.model_dump(exclude_none=True)
    if update_data:
        success = api_key_manager.update_api_key(api_key, **update_data)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update API key",
            )

    # Get updated key
    item = api_key_manager.get_api_key(api_key)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve updated API key",
        )

    # Check if budget was lowered and MTD now exceeds it
    # This handles the case where admin lowers the budget below current MTD usage
    if request.monthly_budget is not None and item.get("is_active", False):
        new_budget = float(request.monthly_budget)
        current_mtd = float(item.get("budget_used_mtd", 0) or 0)

        if new_budget > 0 and current_mtd >= new_budget:
            # Deactivate the key for budget exceeded
            api_key_manager.deactivate_for_budget_exceeded(api_key)
            # Refresh the item to get updated status
            item = api_key_manager.get_api_key(api_key)
            if not item:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to retrieve API key after deactivation",
                )

    return ApiKeyResponse(**item)


@router.delete("/{api_key}")
async def deactivate_api_key(api_key: str):
    """
    Deactivate (revoke) an API key.

    Args:
        api_key: The API key to deactivate
    """
    api_key_manager, _, _ = get_managers()

    # Check if key exists
    existing = api_key_manager.get_api_key(api_key)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    api_key_manager.deactivate_api_key(api_key)
    return {"message": "API key deactivated successfully"}


@router.post("/{api_key}/reactivate")
async def reactivate_api_key(api_key: str):
    """
    Reactivate a revoked API key.

    Args:
        api_key: The API key to reactivate
    """
    api_key_manager, _, _ = get_managers()

    # Check if key exists
    existing = api_key_manager.get_api_key(api_key)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    success = api_key_manager.reactivate_api_key(api_key)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reactivate API key",
        )

    return {"message": "API key reactivated successfully"}


@router.delete("/{api_key}/permanent")
async def delete_api_key_permanently(api_key: str):
    """
    Permanently delete an API key.

    This action cannot be undone.

    Args:
        api_key: The API key to delete
    """
    api_key_manager, _, _ = get_managers()

    # Check if key exists
    existing = api_key_manager.get_api_key(api_key)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    success = api_key_manager.delete_api_key(api_key)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete API key",
        )

    return {"message": "API key deleted permanently"}


@router.get("/{api_key}/usage")
async def get_api_key_usage(api_key: str):
    """
    Get usage statistics for an API key.

    Args:
        api_key: The API key to get usage for
    """
    api_key_manager, usage_tracker, usage_stats_manager = get_managers()

    # Check if key exists
    existing = api_key_manager.get_api_key(api_key)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    # Get aggregated stats from usage_stats table (persisted data)
    aggregated_stats = usage_stats_manager.get_stats(api_key)

    # Get recent stats from usage table (last 30 days, may have TTL expiry)
    recent_stats = usage_tracker.get_usage_stats(api_key)

    return {
        "aggregated": aggregated_stats or {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cached_tokens": 0,
            "total_cache_write_tokens": 0,
            "total_requests": 0,
        },
        "recent": recent_stats,
    }
