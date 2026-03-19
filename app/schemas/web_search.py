"""
Pydantic models for Web Search tool support.

These models represent the web search tool configuration, search results,
and error types used in the Anthropic Messages API.
"""
import base64
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field


# Web Search tool type identifiers
WEB_SEARCH_TOOL_TYPES = {"web_search_20250305", "web_search_20260209"}


class UserLocation(BaseModel):
    """Approximate user location for search localization."""
    type: Literal["approximate"] = "approximate"
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    timezone: Optional[str] = None


class WebSearchToolDefinition(BaseModel):
    """Web search tool definition from client request."""
    type: str  # "web_search_20250305" or "web_search_20260209"
    name: str = "web_search"
    max_uses: Optional[int] = None
    allowed_domains: Optional[List[str]] = None
    blocked_domains: Optional[List[str]] = None
    user_location: Optional[UserLocation] = None


# ==================== Search Result Content Types ====================

class WebSearchResult(BaseModel):
    """Individual web search result within a web_search_tool_result."""
    type: Literal["web_search_result"] = "web_search_result"
    url: str
    title: str
    encrypted_content: str  # base64-encoded page content
    page_age: Optional[str] = None


class WebSearchToolResultError(BaseModel):
    """Error result for web search tool."""
    type: Literal["web_search_tool_result_error"] = "web_search_tool_result_error"
    error_code: str  # too_many_requests, invalid_input, max_uses_exceeded, query_too_long, unavailable


class WebSearchToolResultContent(BaseModel):
    """Web search tool result content block (result of a server_tool_use web_search)."""
    type: Literal["web_search_tool_result"] = "web_search_tool_result"
    tool_use_id: str
    content: Union[List[WebSearchResult], WebSearchToolResultError]


# ==================== Citation Types ====================

class WebSearchResultLocation(BaseModel):
    """Citation location referencing a web search result."""
    type: Literal["web_search_result_location"] = "web_search_result_location"
    url: str
    title: str
    encrypted_index: str  # base64-encoded index reference
    cited_text: str = Field(max_length=150)


# ==================== Helpers ====================

def encode_content(content: str) -> str:
    """Base64-encode content for encrypted_content field."""
    return base64.b64encode(content.encode("utf-8")).decode("utf-8")


def decode_content(encrypted: str) -> str:
    """Decode encrypted_content from base64."""
    return base64.b64decode(encrypted.encode("utf-8")).decode("utf-8")
