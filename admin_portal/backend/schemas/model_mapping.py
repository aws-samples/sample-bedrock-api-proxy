"""Model Mapping schemas."""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class ModelMappingCreate(BaseModel):
    """Schema for creating a model mapping."""

    anthropic_model_id: str = Field(..., description="Anthropic model ID (e.g., 'opus', 'claude-opus-4-5-20251101')")
    bedrock_model_id: str = Field(..., description="Bedrock model ARN (e.g., 'global.anthropic.claude-opus-4-5-20251101-v1:0')")


class ModelMappingUpdate(BaseModel):
    """Schema for updating a model mapping."""

    bedrock_model_id: str = Field(..., description="New Bedrock model ARN")


class ModelMappingResponse(BaseModel):
    """Schema for model mapping response."""

    anthropic_model_id: str
    bedrock_model_id: str
    source: Literal["default", "custom"]
    updated_at: Optional[int] = None

    class Config:
        extra = "allow"


class ModelMappingListResponse(BaseModel):
    """Schema for model mapping list response."""

    items: List[ModelMappingResponse]
    count: int
