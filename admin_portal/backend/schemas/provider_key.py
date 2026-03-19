"""Provider Key schemas."""
from typing import List, Optional
from pydantic import BaseModel, Field


class ProviderKeyCreate(BaseModel):
    """Schema for creating a new provider key."""
    provider: str = Field(..., description="Provider name: bedrock/openai/anthropic/deepseek")
    api_key: str = Field(..., description="Plain-text API key (encrypted before storage)")
    models: List[str] = Field(..., description="Models this key supports")


class ProviderKeyUpdate(BaseModel):
    """Schema for updating a provider key."""
    models: Optional[List[str]] = None
    is_enabled: Optional[bool] = None


class ProviderKeyResponse(BaseModel):
    """Schema for provider key response."""
    key_id: str
    provider: str
    api_key_masked: str
    models: List[str]
    is_enabled: bool
    status: str
    created_at: str
    updated_at: Optional[str] = None
