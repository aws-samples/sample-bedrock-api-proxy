"""
Fetch provider interface and implementations.

Supports:
- HttpxFetchProvider: Direct HTTP fetch using httpx (default, no API key needed)
- TavilyFetchProvider: Tavily Extract API (requires paid Tavily plan)
"""
import html as html_module
import ipaddress
import logging
import re
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

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


class FetchError(Exception):
    """Error during fetch operation."""

    def __init__(self, error_code: str, message: str = ""):
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


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


def _is_private_ip(ip_str: str) -> bool:
    """Check if an IP address is private, reserved, loopback, or link-local."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # If we can't parse it, block it to be safe

    return (
        addr.is_private
        or addr.is_reserved
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        # AWS EC2 metadata endpoint
        or ip_str == "169.254.169.254"
        # ECS metadata endpoint
        or ip_str == "169.254.170.2"
    )


def _validate_url_ssrf(url: str) -> None:
    """
    Validate URL against SSRF attacks by resolving the hostname
    and checking if it points to a private/reserved IP address.

    Raises FetchError if the URL targets an internal resource.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname

    if not hostname:
        raise FetchError("invalid_input", f"Cannot extract hostname from URL: {url}")

    # Block obvious internal hostnames
    blocked_hostnames = {
        "localhost",
        "metadata.google.internal",
        "metadata.google",
    }
    if hostname.lower() in blocked_hostnames:
        raise FetchError(
            "ssrf_blocked",
            f"Access to internal host is not allowed: {hostname}",
        )

    # Resolve hostname to IP(s) and check each one
    try:
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError("url_not_accessible", f"Cannot resolve hostname: {hostname}")

    for addrinfo in addrinfos:
        ip_str = str(addrinfo[4][0])
        if _is_private_ip(ip_str):
            raise FetchError(
                "ssrf_blocked",
                f"Access to private/internal IP address is not allowed: {hostname} -> {ip_str}",
            )


def _validate_url(url: str) -> None:
    """Common URL validation. Raises FetchError on invalid URL."""
    if not url or not url.startswith(("http://", "https://")):
        raise FetchError("invalid_input", f"Invalid URL: {url}")
    if len(url) > 250:
        raise FetchError("url_too_long", f"URL exceeds 250 characters")
    _validate_url_ssrf(url)


def _apply_token_limit(content: str, max_content_tokens: Optional[int], label: str) -> str:
    """Truncate content to approximate token limit (1 token ≈ 4 chars)."""
    if not max_content_tokens or not content:
        return content
    max_chars = max_content_tokens * 4
    if len(content) > max_chars:
        original_len = len(content)
        content = content[:max_chars]
        logger.info(
            f"[{label}] Content truncated: {original_len} -> {len(content)} chars "
            f"(max_content_tokens={max_content_tokens})"
        )
    return content


def _html_to_text(html: str) -> str:
    """
    Convert HTML to readable plain text.

    Simple regex-based approach (no external dependencies).
    Strips tags, decodes entities, collapses whitespace.
    """
    # Remove script and style blocks
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    # Convert block elements to newlines
    text = re.sub(r'<(?:br|hr)[^>]*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<(?:/p|/div|/h[1-6]|/li|/tr|/section|/article)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<(?:p|div|h[1-6]|li|tr|section|article)[^>]*>', '\n', text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode common HTML entities
    text = html_module.unescape(text)
    # Collapse whitespace: multiple spaces on a line -> single space
    text = re.sub(r'[^\S\n]+', ' ', text)
    # Collapse multiple blank lines -> double newline
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_title(html: str) -> str:
    """Extract <title> from HTML."""
    match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
    if match:
        return html_module.unescape(match.group(1).strip())
    return ""


# ==================== Providers ====================

class HttpxFetchProvider(FetchProvider):
    """
    Direct HTTP fetch provider using httpx.

    No external API key required. Fetches URL directly and converts
    HTML to text using simple regex-based extraction.
    """

    # Supported text content types
    _TEXT_TYPES = {"text/html", "text/plain", "text/xml", "application/xml",
                   "application/xhtml+xml", "application/json", "text/csv",
                   "text/markdown"}

    def __init__(self):
        self._client = None

    @property
    def client(self):
        """Lazy-initialize httpx async client with SSRF-safe redirect handling."""
        if self._client is None:
            import httpx

            async def _validate_redirect(response: httpx.Response) -> None:
                """Validate each redirect target against SSRF."""
                if response.next_request is not None:
                    redirect_url = str(response.next_request.url)
                    try:
                        _validate_url_ssrf(redirect_url)
                    except FetchError:
                        raise FetchError(
                            "ssrf_blocked",
                            f"Redirect to blocked URL: {redirect_url}",
                        )

            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                event_hooks={"response": [_validate_redirect]},
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AnthropicProxy/1.0)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
        return self._client

    async def fetch(
        self,
        url: str,
        max_content_tokens: Optional[int] = None,
    ) -> FetchResult:
        """Fetch content via direct HTTP request."""
        _validate_url(url)

        logger.info(f"[WebFetch/Httpx] Fetching: {url}")

        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate limit" in error_str:
                raise FetchError("too_many_requests", str(e))
            raise FetchError("url_not_accessible", str(e))

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        is_pdf = content_type == "application/pdf" or url.lower().endswith(".pdf")

        if is_pdf:
            # Return raw bytes as base64 for PDFs — Claude can handle PDF natively
            import base64
            content = base64.b64encode(response.content).decode("utf-8")
            title = url.rsplit("/", 1)[-1] if "/" in url else url
            media_type = "application/pdf"
        elif content_type in self._TEXT_TYPES or content_type.startswith("text/"):
            raw_html = response.text
            title = _extract_title(raw_html) if "html" in content_type else ""
            if "html" in content_type or "xml" in content_type:
                content = _html_to_text(raw_html)
            else:
                content = raw_html  # plain text, json, csv — pass through
            media_type = "text/plain"
        else:
            raise FetchError(
                "unsupported_content_type",
                f"Content type not supported: {content_type}"
            )

        content = _apply_token_limit(content, max_content_tokens, "WebFetch/Httpx")

        logger.info(
            f"[WebFetch/Httpx] Fetched {len(content)} chars, "
            f"title={title!r}, content_type={content_type}"
        )

        return FetchResult(
            url=str(response.url),  # use final URL after redirects
            title=title,
            content=content,
            media_type=media_type,
            is_pdf=is_pdf,
        )


class TavilyFetchProvider(FetchProvider):
    """
    Tavily-based fetch provider.

    Uses Tavily Extract API. Requires a paid Tavily plan.
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

        _validate_url(url)
        logger.info(f"[WebFetch/Tavily] Fetching: {url}")

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
            failed = response.get("failed_results", [])
            if failed:
                raise FetchError("url_not_accessible", f"Failed to fetch: {failed}")
            raise FetchError("url_not_accessible", "No content returned")

        result = results[0]
        raw_content = result.get("raw_content", "") or ""
        title = result.get("title", "") or ""

        is_pdf = url.lower().endswith(".pdf")
        media_type = "application/pdf" if is_pdf else "text/plain"

        content = _apply_token_limit(raw_content, max_content_tokens, "WebFetch/Tavily")

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
        )


# ==================== Factory ====================

def create_fetch_provider(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
) -> FetchProvider:
    """
    Create a fetch provider instance.

    Args:
        provider: Provider name. "httpx" (default) or "tavily".
        api_key: API key (only needed for tavily).

    Returns:
        FetchProvider instance
    """
    provider = provider or getattr(settings, 'web_fetch_provider', 'httpx')

    if provider == "tavily":
        api_key = api_key or settings.web_search_api_key
        if not api_key:
            raise ValueError(
                "Tavily API key required for web fetch. Set WEB_SEARCH_API_KEY."
            )
        return TavilyFetchProvider(api_key=api_key)

    # Default: direct HTTP fetch (no API key needed)
    return HttpxFetchProvider()
