"""Model Mapping schemas."""
from typing import Any, Dict, List, Literal, Optional
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
    source: Literal["default", "custom", "override"]
    default_bedrock_model_id: Optional[str] = None
    updated_at: Optional[int] = None

    class Config:
        extra = "allow"


class ModelMappingListResponse(BaseModel):
    """Schema for model mapping list response."""

    items: List[ModelMappingResponse]
    count: int


class ModelMappingSyncRequest(BaseModel):
    """Schema for triggering a default model mapping refresh from the remote file."""

    url: Optional[str] = Field(
        default=None, description="Override source URL (defaults to MODEL_MAPPING_SYNC_URL)"
    )
    dry_run: bool = Field(
        default=False, description="Report what would change without applying"
    )


class ModelMappingSyncResponse(BaseModel):
    """Schema for model mapping sync result summary."""

    source_url: str
    remote_models: int = Field(..., description="Mappings in the remote file")
    mapping_count: int = Field(..., description="Active default mappings after merge")
    local_overrides: int = Field(
        0, description="DEFAULT_MODEL_MAPPING env entries layered over the remote file"
    )
    added: List[str] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)
    changed: List[str] = Field(default_factory=list)
    dry_run: bool = False


class ModelMappingSyncStatus(BaseModel):
    """Schema for the model mapping sync status."""

    enabled: bool
    source_url: str
    source: Literal["remote", "bundled", "env"] = Field(
        ..., description="Where the active default mapping came from"
    )
    mapping_count: int
    local_override_count: int = 0
    last_attempt_at: Optional[float] = None
    last_success_at: Optional[float] = None
    last_error: Optional[str] = None


class SpeedTestRequest(BaseModel):
    """Schema for running one model speed test through the proxy."""

    bedrock_model_id: str = Field(
        ..., min_length=1, description="Bedrock model ID sent as `model` (pass-through)"
    )


class SpeedTestRecord(BaseModel):
    """One persisted speed-test run (DynamoDB item == API JSON)."""

    bedrock_model_id: str
    tested_at: int = Field(..., description="Epoch milliseconds at request send")
    status: Literal["ok", "error"]
    ttft_ms: Optional[float] = Field(None, description="Request send -> first content_block_delta")
    total_ms: Optional[float] = Field(None, description="Request send -> message_stop / stream end")
    output_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = Field(
        None,
        description="usage.reasoning_tokens from message_delta (hidden reasoning counted in output_tokens)",
    )
    otps: Optional[float] = Field(
        None,
        description="streamed tokens / ((total_ms - ttft_ms) / 1000); hidden reasoning_tokens excluded",
    )
    has_reasoning: bool = False
    error: Optional[str] = None
    proxy_base_url: str
    expires_at: int = Field(..., description="Epoch seconds (table TTL attribute)")

    class Config:
        extra = "allow"


class SpeedTestHistoryResponse(BaseModel):
    """Latest N runs for one Bedrock model ID, newest first."""

    items: List[SpeedTestRecord]
    count: int


class SpeedTestLatestResponse(BaseModel):
    """Most recent run per Bedrock model ID."""

    items: Dict[str, SpeedTestRecord]
