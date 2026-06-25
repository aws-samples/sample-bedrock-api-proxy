"""Dashboard schemas."""
from typing import List, Optional
from pydantic import BaseModel


class DashboardStats(BaseModel):
    """Dashboard statistics response."""

    total_api_keys: int
    active_api_keys: int
    revoked_api_keys: int
    total_budget: float
    total_budget_used: float
    total_models: int
    active_models: int
    system_status: str = "operational"
    new_keys_this_week: Optional[int] = 0
    # Models that have usage but no pricing configured
    models_without_pricing: List[str] = []
    # Total token usage across all API keys
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    total_cache_write_tokens: int = 0
    total_requests: int = 0


class DailyModelUsage(BaseModel):
    """Per-model usage within a single day."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    tokens: int = 0
    cost: float = 0.0
    requests: int = 0


class DailyUsage(BaseModel):
    """Aggregated usage for a single calendar day (UTC), broken down by model."""

    date: str  # YYYY-MM-DD (UTC)
    total_tokens: int = 0
    total_cost: float = 0.0
    models: List[DailyModelUsage] = []


class DailyUsageResponse(BaseModel):
    """Response for the daily token/cost dashboard panel."""

    days: int
    start_date: str
    end_date: str
    daily: List[DailyUsage] = []
