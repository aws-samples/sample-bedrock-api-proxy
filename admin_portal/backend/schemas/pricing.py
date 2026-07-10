"""Model Pricing schemas."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class PricingCreate(BaseModel):
    """Schema for creating model pricing."""

    model_id: str = Field(..., description="Bedrock model ID")
    provider: str = Field(..., description="Provider name (e.g., Anthropic, Cohere)")
    display_name: Optional[str] = Field(None, description="Human-readable model name")
    input_price: float = Field(..., description="Input price per 1M tokens in USD")
    output_price: float = Field(..., description="Output price per 1M tokens in USD")
    cache_read_price: Optional[float] = Field(
        None, description="Cache read price per 1M tokens"
    )
    cache_write_price: Optional[float] = Field(
        None, description="Cache write price per 1M tokens"
    )
    status: str = Field(
        "active", description="Model status (active, deprecated, disabled)"
    )


class PricingUpdate(BaseModel):
    """Schema for updating model pricing."""

    display_name: Optional[str] = None
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    cache_read_price: Optional[float] = None
    cache_write_price: Optional[float] = None
    status: Optional[str] = None
    provider: Optional[str] = None


class PricingResponse(BaseModel):
    """Schema for pricing response."""

    model_id: str
    provider: str
    display_name: Optional[str] = None
    input_price: float
    output_price: float
    cache_read_price: Optional[float] = None
    cache_write_price: Optional[float] = None
    status: str
    created_at: Optional[int] = None
    updated_at: Optional[int] = None

    class Config:
        extra = "allow"


class PricingListResponse(BaseModel):
    """Schema for paginated pricing list response."""

    items: List[PricingResponse]
    count: int
    last_key: Optional[Dict[str, Any]] = None


class PricingSyncRequest(BaseModel):
    """Schema for triggering a pricing sync from the LiteLLM price table."""

    url: Optional[str] = Field(
        default=None, description="Override source URL (defaults to PRICING_SYNC_URL)"
    )
    create_missing: Optional[bool] = Field(
        default=None,
        description="Create rows for source models missing from the table (defaults to PRICING_SYNC_CREATE_MISSING)",
    )
    overwrite_manual: Optional[bool] = Field(
        default=None,
        description="Also update manually managed rows (defaults to PRICING_SYNC_OVERWRITE_MANUAL)",
    )
    dry_run: bool = Field(
        default=False, description="Report what would change without writing"
    )


class PricingSyncResponse(BaseModel):
    """Schema for pricing sync result summary."""

    source_url: str
    source_models: int = Field(
        ..., description="Number of usable models in the source table"
    )
    created: List[str] = Field(default_factory=list, description="Model IDs created")
    updated: List[str] = Field(
        default_factory=list, description="Model IDs whose prices changed"
    )
    skipped_manual: List[str] = Field(
        default_factory=list, description="Manually managed rows left untouched"
    )
    unchanged: int = 0
    not_found: List[str] = Field(
        default_factory=list, description="Mapped models with no source pricing"
    )
    dry_run: bool = False
