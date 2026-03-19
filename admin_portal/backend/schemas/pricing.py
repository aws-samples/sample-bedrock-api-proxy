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
    cache_read_price: Optional[float] = Field(None, description="Cache read price per 1M tokens")
    cache_write_price: Optional[float] = Field(None, description="Cache write price per 1M tokens")
    status: str = Field("active", description="Model status (active, deprecated, disabled)")


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
