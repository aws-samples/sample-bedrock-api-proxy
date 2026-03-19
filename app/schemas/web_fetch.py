"""
Pydantic models for Web Fetch tool support.

These models represent the web fetch tool configuration, fetch results,
and error types used in the Anthropic Messages API.

Web fetch tool types:
- web_fetch_20250910: Base web fetch (beta: web-fetch-2025-09-10)
- web_fetch_20260209: Dynamic filtering with bash code execution (beta: web-fetch-2026-02-09)
"""
from datetime import datetime, timezone
from typing import List, Literal, Optional, Union

from pydantic import BaseModel


# Web Fetch tool type identifiers
WEB_FETCH_TOOL_TYPES = {"web_fetch_20250910", "web_fetch_20260209"}

# Beta headers for web fetch
WEB_FETCH_BETA_HEADERS = {"web-fetch-2025-09-10", "web-fetch-2026-02-09"}


class WebFetchCitationConfig(BaseModel):
    """Citation configuration for web fetch."""
    enabled: bool = False


class WebFetchToolDefinition(BaseModel):
    """Web fetch tool definition from client request."""
    type: str  # "web_fetch_20250910" or "web_fetch_20260209"
    name: str = "web_fetch"
    max_uses: Optional[int] = None
    allowed_domains: Optional[List[str]] = None
    blocked_domains: Optional[List[str]] = None
    citations: Optional[WebFetchCitationConfig] = None
    max_content_tokens: Optional[int] = None


# ==================== Fetch Result Content Types ====================

class WebFetchDocumentSource(BaseModel):
    """Source data for a fetched document."""
    type: str  # "text" or "base64"
    media_type: str  # "text/plain", "application/pdf", etc.
    data: str  # Text content or base64-encoded data


class WebFetchDocument(BaseModel):
    """Document block within a web fetch result."""
    type: Literal["document"] = "document"
    source: WebFetchDocumentSource
    title: Optional[str] = None
    citations: Optional[WebFetchCitationConfig] = None


class WebFetchResult(BaseModel):
    """Individual web fetch result."""
    type: Literal["web_fetch_result"] = "web_fetch_result"
    url: str
    content: WebFetchDocument
    retrieved_at: str  # ISO 8601 timestamp


class WebFetchToolResultError(BaseModel):
    """Error result for web fetch tool."""
    type: Literal["web_fetch_tool_error"] = "web_fetch_tool_error"
    error_code: str
    # Error codes: invalid_input, url_too_long, url_not_allowed,
    # url_not_accessible, too_many_requests, unsupported_content_type,
    # max_uses_exceeded, unavailable


class WebFetchToolResultContent(BaseModel):
    """Web fetch tool result content block."""
    type: Literal["web_fetch_tool_result"] = "web_fetch_tool_result"
    tool_use_id: str
    content: Union[WebFetchResult, WebFetchToolResultError]


# ==================== Helpers ====================

def now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
