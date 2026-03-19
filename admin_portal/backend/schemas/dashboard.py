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
