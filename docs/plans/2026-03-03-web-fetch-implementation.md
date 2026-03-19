# Web Fetch Tool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement `web_fetch_20250910` and `web_fetch_20260209` server-managed tools that fetch full web page content, following the same agentic loop pattern as web search.

**Architecture:** Extract shared agentic loop logic from `WebSearchService` into a `ServerToolService` base class. Create `WebFetchService` inheriting from the base. Refactor `WebSearchService` to also inherit. The fetch backend uses Tavily Extract API (reusing existing Tavily integration). `web_fetch_20260209` adds dynamic filtering via bash code execution (same as web search).

**Tech Stack:** Python 3.11, FastAPI, Pydantic, Tavily SDK (tavily-python), asyncio

---

### Task 1: Create Web Fetch Schemas

**Files:**
- Create: `app/schemas/web_fetch.py`

**Step 1: Create the schema file**

```python
"""
Pydantic models for Web Fetch tool support.

These models represent the web fetch tool configuration, fetch results,
and error types used in the Anthropic Messages API.
"""
import base64
from datetime import datetime, timezone
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field


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
```

**Step 2: Verify import works**

Run: `cd /home/ubuntu/workspace/anthropic_api_proxy && python -c "from app.schemas.web_fetch import *; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add app/schemas/web_fetch.py
git commit -m "feat: add web fetch schema models"
```

---

### Task 2: Create Web Fetch Provider (Tavily Extract)

**Files:**
- Create: `app/services/web_fetch/__init__.py`
- Create: `app/services/web_fetch/providers.py`

**Step 1: Create the provider module**

`app/services/web_fetch/__init__.py`:
```python
"""Web fetch provider module."""

from app.services.web_fetch.providers import (
    FetchProvider,
    FetchResult,
    TavilyFetchProvider,
    create_fetch_provider,
)
```

`app/services/web_fetch/providers.py`:
```python
"""
Fetch provider interface and implementations.

Uses Tavily Extract API to fetch and parse web page content.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Standardized fetch result."""
    url: str
    title: str
    content: str  # Extracted text/markdown content
    media_type: str  # "text/plain" or "application/pdf"
    is_pdf: bool = False
    raw_content: Optional[str] = None  # Original raw content if available


class FetchProvider(ABC):
    """Abstract fetch provider interface."""

    @abstractmethod
    async def fetch(
        self,
        url: str,
        max_content_tokens: Optional[int] = None,
    ) -> FetchResult:
        """
        Fetch and extract content from a URL.

        Args:
            url: URL to fetch
            max_content_tokens: Optional content length limit in tokens

        Returns:
            FetchResult with extracted content

        Raises:
            FetchError: If fetch fails
        """
        pass


class FetchError(Exception):
    """Error during fetch operation."""

    def __init__(self, error_code: str, message: str = ""):
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


class TavilyFetchProvider(FetchProvider):
    """
    Tavily-based fetch provider.

    Uses Tavily Extract API to fetch and parse web page content.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = None

    @property
    def client(self):
        """Lazy-initialize Tavily client."""
        if self._client is None:
            from tavily import TavilyClient
            self._client = TavilyClient(api_key=self.api_key)
        return self._client

    async def fetch(
        self,
        url: str,
        max_content_tokens: Optional[int] = None,
    ) -> FetchResult:
        """Fetch content via Tavily Extract API."""
        import asyncio

        logger.info(f"[WebFetch/Tavily] Fetching: {url}")

        # Validate URL
        if not url or not url.startswith(("http://", "https://")):
            raise FetchError("invalid_input", f"Invalid URL: {url}")

        if len(url) > 250:
            raise FetchError("url_too_long", f"URL exceeds 250 characters")

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.extract(urls=[url])
            )
        except Exception as e:
            error_str = str(e).lower()
            if "rate limit" in error_str or "429" in error_str:
                raise FetchError("too_many_requests", str(e))
            raise FetchError("url_not_accessible", str(e))

        results = response.get("results", [])
        if not results:
            # Check failed results
            failed = response.get("failed_results", [])
            if failed:
                raise FetchError("url_not_accessible", f"Failed to fetch: {failed}")
            raise FetchError("url_not_accessible", "No content returned")

        result = results[0]
        raw_content = result.get("raw_content", "") or ""
        title = result.get("title", "") or ""

        # Detect PDF (Tavily may indicate this in response)
        is_pdf = url.lower().endswith(".pdf")
        media_type = "application/pdf" if is_pdf else "text/plain"

        # Apply max_content_tokens limit (approximate: 1 token ≈ 4 chars)
        content = raw_content
        if max_content_tokens and content:
            max_chars = max_content_tokens * 4
            if len(content) > max_chars:
                content = content[:max_chars]
                logger.info(
                    f"[WebFetch/Tavily] Content truncated: "
                    f"{len(raw_content)} → {len(content)} chars "
                    f"(max_content_tokens={max_content_tokens})"
                )

        logger.info(
            f"[WebFetch/Tavily] Fetched {len(content)} chars, "
            f"title={title!r}, media_type={media_type}"
        )

        return FetchResult(
            url=url,
            title=title,
            content=content,
            media_type=media_type,
            is_pdf=is_pdf,
            raw_content=raw_content,
        )


def create_fetch_provider(
    api_key: Optional[str] = None,
) -> FetchProvider:
    """
    Create a fetch provider instance.

    Uses Tavily Extract API (same API key as web search).

    Args:
        api_key: Tavily API key. Defaults to settings.web_search_api_key.

    Returns:
        FetchProvider instance
    """
    api_key = api_key or settings.web_search_api_key
    if not api_key:
        raise ValueError(
            "Tavily API key required for web fetch. Set WEB_SEARCH_API_KEY."
        )
    return TavilyFetchProvider(api_key=api_key)
```

**Step 2: Verify import works**

Run: `cd /home/ubuntu/workspace/anthropic_api_proxy && python -c "from app.services.web_fetch import *; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add app/services/web_fetch/
git commit -m "feat: add Tavily-based web fetch provider"
```

---

### Task 3: Create Web Fetch Service

**Files:**
- Create: `app/services/web_fetch_service.py`

This is the main service, following the same agentic loop pattern as `WebSearchService`. Rather than refactoring the existing service right now (risky), we'll copy the pattern and share what's easy to share. The base class extraction can be done in a follow-up refactoring.

**Approach:** Create `WebFetchService` with the same structure as `WebSearchService`, adapted for fetch. Reuse `DomainFilter` from web_search and the same SSE/citation patterns.

The service should:
1. Detect `web_fetch_20250910` / `web_fetch_20260209` tools in request
2. Replace with custom `web_fetch` tool definition for Bedrock
3. Run agentic loop (call Bedrock → if Claude calls web_fetch → execute fetch → continue)
4. For `20260209`: also inject `bash_code_execution` tool
5. Convert `tool_use` → `server_tool_use` in response
6. Build `web_fetch_tool_result` content blocks
7. Support citations when `citations.enabled=true`
8. Support streaming (hybrid approach like web search)

**Key differences from web search:**
- Result format: `web_fetch_tool_result` contains a single `web_fetch_result` (not a list)
- Content is a document block (type, source, title, citations)
- Citation format: `char_location` (character positions in document) vs `web_search_result_location`
- Error codes: different set (url_not_accessible, unsupported_content_type, etc.)
- Custom tool description asks Claude to fetch URLs, not search queries
- `max_content_tokens` parameter on the tool definition
- `server_tool_use` usage tracking field: `web_fetch_requests` (not `web_search_requests`)

**Step 1: Create the service file**

Create `app/services/web_fetch_service.py` with the full implementation. The service follows the same agentic loop pattern as WebSearchService. Key methods:

- `is_web_fetch_request(request)` → static detection
- `extract_web_fetch_config(request)` → extract tool config
- `_get_custom_web_fetch_tool()` → custom tool definition for Bedrock
- `_build_tools_for_request()` → replace web_fetch marker
- `_execute_fetch()` → call Tavily Extract via provider
- `_build_web_fetch_tool_result()` → build result content block
- `_build_web_fetch_error()` → build error content block
- `handle_request()` → non-streaming agentic loop
- `handle_request_streaming()` → streaming agentic loop

For the citation system with web fetch:
- When `citations.enabled=true`, inject a system prompt asking Claude to cite with `[doc:N:start-end]` markers
- Post-process to convert markers into `char_location` citation objects
- Each fetched document gets a 0-based document_index
- Citations reference character positions in the document text

**Step 2: Verify import works**

Run: `cd /home/ubuntu/workspace/anthropic_api_proxy && python -c "from app.services.web_fetch_service import WebFetchService; print('OK')"`

**Step 3: Commit**

```bash
git add app/services/web_fetch_service.py
git commit -m "feat: add WebFetchService with agentic loop"
```

---

### Task 4: Add Config Settings for Web Fetch

**Files:**
- Modify: `app/core/config.py` (add settings after web_search section, ~line 323)

**Step 1: Add web fetch settings**

Add after the `web_search_default_max_uses` field (around line 323):

```python
    # Web Fetch Settings
    enable_web_fetch: bool = Field(
        default=True,
        alias="ENABLE_WEB_FETCH",
        description="Enable web fetch tool support (proxy-side server tool)"
    )
    web_fetch_default_max_uses: int = Field(
        default=20,
        alias="WEB_FETCH_DEFAULT_MAX_USES",
        description="Default maximum number of web fetches per request"
    )
    web_fetch_default_max_content_tokens: int = Field(
        default=100000,
        alias="WEB_FETCH_DEFAULT_MAX_CONTENT_TOKENS",
        description="Default maximum content tokens per fetch"
    )
```

**Step 2: Verify config loads**

Run: `cd /home/ubuntu/workspace/anthropic_api_proxy && python -c "from app.core.config import settings; print(f'fetch={settings.enable_web_fetch}, max_uses={settings.web_fetch_default_max_uses}')"`
Expected: `fetch=True, max_uses=20`

**Step 3: Commit**

```bash
git add app/core/config.py
git commit -m "feat: add web fetch config settings"
```

---

### Task 5: Integrate Web Fetch into API Router

**Files:**
- Modify: `app/api/messages.py`

**Step 1: Add imports**

Add alongside the web_search imports (around line 33-35):

```python
from app.services.web_fetch_service import (
    WebFetchService,
    get_web_fetch_service,
)
```

**Step 2: Add dependency injection**

Add a dependency function similar to `get_web_search_service_dep()` (around line 223-225):

```python
def get_web_fetch_service_dep() -> WebFetchService:
    """Dependency injection for web fetch service."""
    return get_web_fetch_service()
```

Add to `create_message()` function parameters (around line 251):

```python
    web_fetch_service: WebFetchService = Depends(get_web_fetch_service_dep),
```

**Step 3: Add web fetch routing**

Add AFTER the web search routing block (after line 728), BEFORE the streaming check:

```python
        # Check if this is a web fetch request
        is_web_fetch = WebFetchService.is_web_fetch_request(request_data)
        if is_web_fetch:
            logger.info(f"Request {request_id}: Detected web fetch request")

            if request_data.stream:
                _end_trace_spans()

                async def _web_fetch_stream_with_usage():
                    """Wrap web fetch streaming with usage tracking."""
                    accumulated = {"input": 0, "output": 0}
                    wf_success = True
                    wf_error = None
                    try:
                        async for sse_event in web_fetch_service.handle_request_streaming(
                            request=request_data,
                            bedrock_service=bedrock_service,
                            request_id=request_id,
                            service_tier=service_tier,
                            anthropic_beta=anthropic_beta,
                        ):
                            if "data:" in sse_event:
                                try:
                                    data_line = [l for l in sse_event.split("\n") if l.startswith("data:")]
                                    if data_line:
                                        evt = json.loads(data_line[0][5:].strip())
                                        if evt.get("type") == "message_start":
                                            msg = evt.get("message", {})
                                            u = msg.get("usage", {})
                                            accumulated["input"] = u.get("input_tokens", 0)
                                        elif evt.get("type") == "message_delta":
                                            u = evt.get("usage", {})
                                            if "output_tokens" in u:
                                                accumulated["output"] = u["output_tokens"]
                                except (json.JSONDecodeError, IndexError, KeyError):
                                    pass
                                yield sse_event
                    except Exception as e:
                        wf_success = False
                        wf_error = str(e)
                        raise
                    finally:
                        usage_tracker.record_usage(
                            api_key=api_key_info.get("api_key"),
                            request_id=request_id,
                            model=request_data.model,
                            input_tokens=accumulated["input"],
                            output_tokens=accumulated["output"],
                            success=wf_success,
                            error_message=wf_error,
                        )

                return StreamingResponse(
                    _web_fetch_stream_with_usage(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Request-ID": request_id,
                    },
                )

            try:
                response = await web_fetch_service.handle_request(
                    request=request_data,
                    bedrock_service=bedrock_service,
                    request_id=request_id,
                    service_tier=service_tier,
                    anthropic_beta=anthropic_beta,
                )

                usage_tracker.record_usage(
                    api_key=api_key_info.get("api_key"),
                    request_id=request_id,
                    model=request_data.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
                    cache_write_input_tokens=getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
                    success=True,
                    cache_ttl=effective_cache_ttl,
                )

                _end_trace_spans()
                return JSONResponse(content=response.model_dump())

            except ValueError as e:
                logger.error(f"Request {request_id}: Web fetch config error: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"type": "invalid_request_error", "message": str(e)},
                )
            except Exception as e:
                logger.error(f"Request {request_id}: Web fetch error: {e}")
                raise
```

**Step 4: Commit**

```bash
git add app/api/messages.py
git commit -m "feat: integrate web fetch service into API router"
```

---

### Task 6: Handle web_fetch_tool_result in Converters (Multi-Turn)

**Files:**
- Modify: `app/converters/anthropic_to_bedrock.py` (around line 664-693)

When clients send multi-turn conversations that include `web_fetch_tool_result` blocks from previous turns, the converter needs to handle them (convert to Bedrock toolResult format).

**Step 1: Add web_fetch_tool_result handling**

Add a new `elif` block after the existing `web_search_tool_result` handler (after line 693):

```python
                elif block_type == "web_fetch_tool_result":
                    # Handle web fetch results in multi-turn: convert to toolResult
                    wf_content = block.get("content", {})
                    if isinstance(wf_content, dict):
                        wf_type = wf_content.get("type", "")
                        if wf_type == "web_fetch_result":
                            doc = wf_content.get("content", {})
                            source = doc.get("source", {})
                            data = source.get("data", "")
                            title = doc.get("title", "")
                            url = wf_content.get("url", "")
                            result_text = f"Title: {title}\nURL: {url}\nContent: {data}"
                        elif wf_type == "web_fetch_tool_error":
                            result_text = f"Error: {wf_content.get('error_code', 'unknown')}"
                        else:
                            result_text = str(wf_content)
                    else:
                        result_text = str(wf_content)
                    bedrock_content.append(
                        {
                            "toolResult": {
                                "toolUseId": block.get("tool_use_id", ""),
                                "content": [{"text": result_text}],
                                "status": "success",
                            }
                        }
                    )
```

**Step 2: Commit**

```bash
git add app/converters/anthropic_to_bedrock.py
git commit -m "feat: handle web_fetch_tool_result in multi-turn converter"
```

---

### Task 7: Write Tests

**Files:**
- Create: `tests/unit/test_web_fetch_schemas.py`
- Create: `tests/unit/test_web_fetch_provider.py`
- Create: `tests/web_fetch_test.py` (integration-style test)

**Step 1: Schema tests**

```python
"""Tests for web fetch schema models."""
from app.schemas.web_fetch import (
    WEB_FETCH_TOOL_TYPES,
    WebFetchToolDefinition,
    WebFetchResult,
    WebFetchDocument,
    WebFetchDocumentSource,
    WebFetchToolResultError,
    WebFetchToolResultContent,
    now_iso,
)


def test_web_fetch_tool_types():
    assert "web_fetch_20250910" in WEB_FETCH_TOOL_TYPES
    assert "web_fetch_20260209" in WEB_FETCH_TOOL_TYPES


def test_web_fetch_tool_definition_minimal():
    defn = WebFetchToolDefinition(type="web_fetch_20250910")
    assert defn.name == "web_fetch"
    assert defn.max_uses is None
    assert defn.citations is None
    assert defn.max_content_tokens is None


def test_web_fetch_tool_definition_full():
    defn = WebFetchToolDefinition(
        type="web_fetch_20260209",
        name="web_fetch",
        max_uses=5,
        allowed_domains=["example.com"],
        citations={"enabled": True},
        max_content_tokens=50000,
    )
    assert defn.max_uses == 5
    assert defn.allowed_domains == ["example.com"]
    assert defn.citations.enabled is True
    assert defn.max_content_tokens == 50000


def test_web_fetch_result():
    result = WebFetchResult(
        url="https://example.com",
        content=WebFetchDocument(
            source=WebFetchDocumentSource(
                type="text",
                media_type="text/plain",
                data="Hello world",
            ),
            title="Example",
        ),
        retrieved_at="2025-08-25T10:30:00Z",
    )
    assert result.type == "web_fetch_result"
    assert result.content.source.data == "Hello world"


def test_web_fetch_error():
    error = WebFetchToolResultError(error_code="url_not_accessible")
    assert error.type == "web_fetch_tool_error"
    assert error.error_code == "url_not_accessible"


def test_now_iso():
    ts = now_iso()
    assert "T" in ts
    assert ts.endswith("Z")
```

**Step 2: Run schema tests**

Run: `cd /home/ubuntu/workspace/anthropic_api_proxy && uv run pytest tests/unit/test_web_fetch_schemas.py -v`
Expected: All pass

**Step 3: Commit**

```bash
git add tests/unit/test_web_fetch_schemas.py
git commit -m "test: add web fetch schema tests"
```

---

### Task 8: Update CLAUDE.md and .env.example

**Files:**
- Modify: `CLAUDE.md` - Add web fetch section
- Modify: `.env.example` - Add web fetch env vars (if exists)

Add web fetch documentation to CLAUDE.md covering:
- Web fetch tool types and beta headers
- Config settings (ENABLE_WEB_FETCH, WEB_FETCH_DEFAULT_MAX_USES, etc.)
- Response format differences from web search
- How web fetch integrates with web search

**Step 1: Update docs**

**Step 2: Commit**

```bash
git add CLAUDE.md .env.example
git commit -m "docs: add web fetch documentation"
```

---

## Execution Notes

### Testing Strategy
- Unit tests: Schema validation, provider mock tests
- Integration tests: Full request flow with mocked Tavily Extract
- Manual tests: Real Tavily API calls (requires API key)

### Risk Mitigation
- WebSearchService is NOT modified in this plan (no refactoring risk)
- Base class extraction is deferred to a follow-up task
- Web fetch service is a new, independent file
- Converter change is minimal (one new elif block)

### Key Validation Points
1. After Task 3: `WebFetchService.is_web_fetch_request()` correctly detects web fetch tools
2. After Task 5: API router correctly routes web fetch requests
3. After Task 6: Multi-turn conversations with web_fetch_tool_result work
4. After Task 7: All tests pass
