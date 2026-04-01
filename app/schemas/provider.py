"""Provider schemas for multi-Bedrock-account support."""
from typing import Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderCreate(BaseModel):
    """Schema for creating a new provider."""

    name: str = Field(..., description="Display name, e.g. 'Production Account US'")
    aws_region: str = Field(..., description="AWS region for Bedrock, e.g. 'us-east-1'")
    auth_type: Literal["bearer_token", "ak_sk"] = Field(
        ..., description="Authentication method"
    )
    credentials: Dict[str, str] = Field(
        ..., description="Credentials dict (bearer_token or access_key_id/secret_access_key/session_token)"
    )
    endpoint_url: Optional[str] = Field(
        None, description="Custom Bedrock endpoint URL"
    )

    @model_validator(mode="after")
    def validate_credentials(self) -> "ProviderCreate":
        if self.auth_type == "bearer_token":
            if "bearer_token" not in self.credentials:
                raise ValueError("credentials must contain 'bearer_token' for auth_type='bearer_token'")
        elif self.auth_type == "ak_sk":
            if "access_key_id" not in self.credentials:
                raise ValueError("credentials must contain 'access_key_id' for auth_type='ak_sk'")
            if "secret_access_key" not in self.credentials:
                raise ValueError("credentials must contain 'secret_access_key' for auth_type='ak_sk'")
        return self


class ProviderUpdate(BaseModel):
    """Schema for updating a provider (all fields optional)."""

    name: Optional[str] = None
    aws_region: Optional[str] = None
    auth_type: Optional[Literal["bearer_token", "ak_sk"]] = None
    credentials: Optional[Dict[str, str]] = None
    endpoint_url: Optional[str] = None
    is_active: Optional[bool] = None


class ProviderResponse(BaseModel):
    """Schema for provider API responses (credentials masked)."""

    provider_id: str
    name: str
    aws_region: str
    auth_type: str
    masked_credentials: str = Field(
        ..., description="Masked credentials for display"
    )
    endpoint_url: Optional[str] = None
    is_active: bool = True
    created_at: str
    updated_at: str
    api_key_count: int = 0

    model_config = ConfigDict(extra="allow")


class ProviderListResponse(BaseModel):
    """Paginated list of providers."""

    items: list[ProviderResponse]
    count: int
