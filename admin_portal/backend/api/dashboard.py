"""Dashboard API routes."""
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Union

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter

from app.db.dynamodb import DynamoDBClient, APIKeyManager, ModelPricingManager, UsageTracker, ModelMappingManager, UsageStatsManager
from app.core.config import settings
from admin_portal.backend.schemas.dashboard import DashboardStats

router = APIRouter()


def _parse_timestamp(value: Union[int, str, None]) -> int:
    """
    Parse a timestamp value that can be either an integer (Unix timestamp)
    or an ISO format string.

    Args:
        value: Unix timestamp (int) or ISO string (e.g., '2026-01-03T13:02:42Z')

    Returns:
        Unix timestamp as integer, or 0 if parsing fails
    """
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return int(dt.timestamp())
        except (ValueError, AttributeError):
            return 0
    return 0


def _resolve_model_id(
    model_id: str,
    model_mapping_cache: dict[str, str],
) -> str:
    """
    Resolve an Anthropic model ID to a Bedrock model ID.

    Args:
        model_id: The model ID (could be Anthropic or Bedrock format)
        model_mapping_cache: Cache of custom model mappings from DynamoDB

    Returns:
        The resolved Bedrock model ID
    """
    if not model_id:
        return model_id

    # Check custom DynamoDB mappings first
    if model_id in model_mapping_cache:
        return model_mapping_cache[model_id]

    # Check default config mapping
    bedrock_id = settings.default_model_mapping.get(model_id)
    if bedrock_id:
        return bedrock_id

    # If no mapping found, assume it's already a Bedrock model ID
    return model_id


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats():
    """
    Get dashboard statistics.

    Returns overview stats including total budget, active keys,
    and system status.
    """
    # Initialize DynamoDB clients
    db_client = DynamoDBClient()
    api_key_manager = APIKeyManager(db_client)
    pricing_manager = ModelPricingManager(db_client)
    usage_tracker = UsageTracker(db_client)

    # Get all API keys
    all_keys_result = api_key_manager.list_all_api_keys(limit=1000)
    all_keys = all_keys_result.get("items", [])

    # Calculate stats
    total_api_keys = len(all_keys)
    active_api_keys = sum(1 for k in all_keys if k.get("is_active", False))
    revoked_api_keys = total_api_keys - active_api_keys

    # Calculate budget stats
    total_budget = sum(float(k.get("monthly_budget", 0) or 0) for k in all_keys)
    total_budget_used = sum(float(k.get("budget_used", 0) or 0) for k in all_keys)

    # Count new keys this week
    week_ago = int((datetime.now() - timedelta(days=7)).timestamp())
    new_keys_this_week = sum(1 for k in all_keys if _parse_timestamp(k.get("created_at")) > week_ago)

    # Get model pricing stats
    pricing_result = pricing_manager.list_all_pricing(limit=1000)
    all_pricing = pricing_result.get("items", [])
    total_models = len(all_pricing)
    active_models = sum(1 for p in all_pricing if p.get("status") == "active")

    # Calculate total token usage across all API keys
    usage_stats_manager = UsageStatsManager(db_client)
    total_input_tokens = 0
    total_output_tokens = 0
    total_cached_tokens = 0
    total_cache_write_tokens = 0
    total_requests = 0

    for key in all_keys:
        api_key = key.get("api_key")
        if api_key:
            stats = usage_stats_manager.get_stats(api_key)
            if stats:
                total_input_tokens += int(stats.get("total_input_tokens", 0) or 0)
                total_output_tokens += int(stats.get("total_output_tokens", 0) or 0)
                total_cached_tokens += int(stats.get("total_cached_tokens", 0) or 0)
                total_cache_write_tokens += int(stats.get("total_cache_write_tokens", 0) or 0)
                total_requests += int(stats.get("total_requests", 0) or 0)

    # Get set of models that have pricing configured (Bedrock model IDs)
    priced_models = {p.get("model_id") for p in all_pricing if p.get("model_id")}

    # Build model mapping cache from DynamoDB custom mappings
    model_mapping_cache: dict[str, str] = {}
    try:
        model_mapping_manager = ModelMappingManager(db_client)
        custom_mappings = model_mapping_manager.list_mappings()
        for mapping in custom_mappings:
            anthropic_id = mapping.get("anthropic_model_id", "")
            bedrock_id = mapping.get("bedrock_model_id", "")
            if anthropic_id and bedrock_id:
                model_mapping_cache[anthropic_id] = bedrock_id
    except Exception as e:
        print(f"[Dashboard] Error loading model mappings: {e}")

    # Get distinct models from usage table and find those without pricing
    models_without_pricing = []
    try:
        used_models = set()
        # Scan usage table to get distinct models (with pagination)
        usage_table = usage_tracker.table
        last_key = None
        while True:
            scan_kwargs = {"ProjectionExpression": "model", "Limit": 1000}
            if last_key:
                scan_kwargs["ExclusiveStartKey"] = last_key
            response = usage_table.scan(**scan_kwargs)
            for item in response.get("Items", []):
                model = item.get("model")
                if model:
                    used_models.add(model)
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break

        # Find models that have usage but no pricing (resolve to Bedrock ID for comparison)
        for model in used_models:
            bedrock_model_id = _resolve_model_id(model, model_mapping_cache)
            if bedrock_model_id not in priced_models:
                models_without_pricing.append(model)
        models_without_pricing = sorted(models_without_pricing)
    except Exception as e:
        print(f"[Dashboard] Error getting models without pricing: {e}")

    return DashboardStats(
        total_api_keys=total_api_keys,
        active_api_keys=active_api_keys,
        revoked_api_keys=revoked_api_keys,
        total_budget=total_budget,
        total_budget_used=total_budget_used,
        total_models=total_models,
        active_models=active_models,
        system_status="operational",
        new_keys_this_week=new_keys_this_week,
        models_without_pricing=models_without_pricing,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cached_tokens=total_cached_tokens,
        total_cache_write_tokens=total_cache_write_tokens,
        total_requests=total_requests,
    )
