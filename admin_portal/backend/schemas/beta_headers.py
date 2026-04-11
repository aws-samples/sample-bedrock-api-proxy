"""Beta Headers management schemas."""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class BetaHeaderCreate(BaseModel):
    header_name: str = Field(..., description="Beta header name")
    header_type: Literal["mapping", "blocklist"] = Field(..., description="Header rule type")
    mapped_to: List[str] = Field(default=[], description="Bedrock headers (mapping type only)")
    description: str = Field(default="", description="Optional admin note")


class BetaHeaderUpdate(BaseModel):
    header_type: Optional[Literal["mapping", "blocklist"]] = None
    mapped_to: Optional[List[str]] = None
    description: Optional[str] = None


class BetaHeaderResponse(BaseModel):
    header_name: str
    header_type: str
    mapped_to: List[str] = []
    description: str = ""
    created_at: str = ""
    updated_at: str = ""


class BetaHeaderListResponse(BaseModel):
    items: List[BetaHeaderResponse]
    count: int
