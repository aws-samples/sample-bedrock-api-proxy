"""Routing configuration schemas."""
from typing import List, Optional
from pydantic import BaseModel, Field


class RoutingRuleCreate(BaseModel):
    """Schema for creating a routing rule."""
    rule_name: str
    rule_type: str = Field(..., description="keyword/regex/model")
    pattern: str
    target_model: str
    target_provider: str = "bedrock"


class RoutingRuleUpdate(BaseModel):
    """Schema for updating a routing rule."""
    rule_name: Optional[str] = None
    rule_type: Optional[str] = None
    pattern: Optional[str] = None
    target_model: Optional[str] = None
    target_provider: Optional[str] = None
    is_enabled: Optional[bool] = None


class RoutingRuleResponse(BaseModel):
    """Schema for routing rule response."""
    rule_id: str
    rule_name: str
    rule_type: str
    pattern: str
    target_model: str
    target_provider: str
    priority: int
    is_enabled: bool
    created_at: str
    updated_at: Optional[str] = None


class RuleReorderRequest(BaseModel):
    """Schema for reordering rules."""
    rule_ids: List[str] = Field(..., description="Rule IDs in new priority order")


class SmartRoutingConfig(BaseModel):
    """Schema for smart routing global config."""
    strong_model: str
    weak_model: str
    threshold: float = 0.5
