"""Failover chain schemas."""
from typing import List, Optional
from pydantic import BaseModel


class FailoverTarget(BaseModel):
    """A single failover target."""
    provider: str
    model: str


class FailoverChainCreate(BaseModel):
    """Schema for creating a failover chain."""
    source_model: str
    targets: List[FailoverTarget]


class FailoverChainUpdate(BaseModel):
    """Schema for updating a failover chain."""
    targets: List[FailoverTarget]


class FailoverChainResponse(BaseModel):
    """Schema for failover chain response."""
    source_model: str
    targets: List[FailoverTarget]
    created_at: str
    updated_at: Optional[str] = None
