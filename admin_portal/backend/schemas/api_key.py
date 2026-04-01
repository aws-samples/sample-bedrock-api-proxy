"""API Key schemas."""
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, field_validator


class ApiKeyCreate(BaseModel):
    """Schema for creating a new API key."""

    user_id: str = Field(..., description="User identifier")
    name: str = Field(..., description="Human-readable name for the key")
    owner_name: Optional[str] = Field(None, description="Display name for the owner")
    role: Optional[str] = Field("Full Access", description="Role type")
    monthly_budget: Optional[float] = Field(0, description="Monthly budget limit in USD")
    rate_limit: Optional[int] = Field(None, description="Custom rate limit")
    service_tier: Optional[str] = Field(None, description="Bedrock service tier")
    cache_ttl: Optional[str] = Field(None, description="Cache TTL override ('5m' or '1h')")
    routing_strategy: Optional[str] = Field("off", description="Routing strategy: cost/quality/auto/off")
    compression_strategy: Optional[str] = Field("off", description="Compression strategy: aggressive/moderate/conservative/off")
    provider_id: Optional[str] = Field(None, description="Provider ID for Bedrock routing")


class ApiKeyUpdate(BaseModel):
    """Schema for updating an API key."""

    name: Optional[str] = None
    owner_name: Optional[str] = None
    role: Optional[str] = None
    monthly_budget: Optional[float] = None
    budget_used: Optional[float] = None
    rate_limit: Optional[int] = None
    service_tier: Optional[str] = None
    is_active: Optional[bool] = None
    cache_ttl: Optional[str] = None  # "5m", "1h", or "none" to clear
    routing_strategy: Optional[str] = None
    compression_strategy: Optional[str] = None
    provider_id: Optional[str] = None


class ApiKeyResponse(BaseModel):
    """Schema for API key response."""

    api_key: str
    user_id: str
    name: str
    created_at: Union[int, str]  # Accept both Unix timestamp and ISO string
    is_active: bool = True
    rate_limit: Optional[int] = None  # Optional - may not be set
    service_tier: Optional[str] = "default"  # Optional with default
    owner_name: Optional[str] = None
    role: Optional[str] = None
    monthly_budget: Optional[float] = 0
    budget_used: Optional[float] = 0  # Total cumulative budget (never resets)
    budget_used_mtd: Optional[float] = 0  # Month-to-date budget (resets monthly)
    budget_mtd_month: Optional[str] = None  # Month for MTD tracking (YYYY-MM)
    budget_history: Optional[str] = None  # Monthly budget history as JSON string (e.g., {"2025-11": 32.11})
    tpm_limit: Optional[int] = 100000
    updated_at: Optional[Union[int, str]] = None  # Accept both formats
    deactivated_reason: Optional[str] = None  # Reason for deactivation
    cache_ttl: Optional[str] = None  # Per-key cache TTL override
    routing_strategy: Optional[str] = "off"
    compression_strategy: Optional[str] = "off"
    provider_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    # Usage stats (aggregated from usage_stats table)
    total_input_tokens: Optional[int] = 0
    total_output_tokens: Optional[int] = 0
    total_cached_tokens: Optional[int] = 0       # Cache read tokens
    total_cache_write_tokens: Optional[int] = 0  # Cache write tokens
    total_requests: Optional[int] = 0

    @field_validator('created_at', 'updated_at', mode='before')
    @classmethod
    def parse_timestamp(cls, v):
        """Convert ISO string timestamps to Unix timestamps."""
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                # Try parsing ISO format
                dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
                return int(dt.timestamp())
            except (ValueError, AttributeError):
                # If parsing fails, return 0
                return 0
        return v

    class Config:
        extra = "allow"


class ApiKeyListResponse(BaseModel):
    """Schema for paginated API key list response."""

    items: List[ApiKeyResponse]
    count: int
    last_key: Optional[Dict[str, Any]] = None
