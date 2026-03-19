"""Authentication schemas."""
from pydantic import BaseModel


class LoginRequest(BaseModel):
    """Login request with master key."""

    master_key: str


class LoginResponse(BaseModel):
    """Login response."""

    success: bool
    message: str
