"""
Fetch provider interface and implementations.

Supports:
- HttpxFetchProvider: Direct HTTP fetch using httpx (default, no API key needed)
- TavilyFetchProvider: Tavily Extract API (requires paid Tavily plan)

Security: SSRF protection with DNS rebinding prevention.
DNS resolution is pinned at the transport level so that the validated IP
is the same IP actually connected to, closing the TOCTOU gap between
hostname validation and the outbound TCP connection.
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

import httpcore
import httpx

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


def _resolve_and_validate(hostname: str) -> str:
    """
    Resolve hostname to IP addresses, validate ALL resolved IPs against
    SSRF rules, and return the first valid (public) IP for DNS pinning.

    This ensures the IP we later connect to has been explicitly validated,
    preventing DNS rebinding attacks where a hostname resolves to a private
    IP between the validation check and the actual connection.

    Args:
        hostname: The hostname to resolve.

    Returns:
        The first resolved public IP address as a string.

    Raises:
        FetchError: If the hostname cannot be resolved or any resolved IP
            is private/reserved.
    """
    try:
        addrinfos = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except socket.gaierror:
        raise FetchError("url_not_accessible", f"Cannot resolve hostname: {hostname}")

    if not addrinfos:
        raise FetchError("url_not_accessible", f"No addresses found for hostname: {hostname}")

    # Validate ALL resolved IPs — if any is private, block the request.
    # An attacker-controlled DNS server could return both public and private IPs;
    # we must reject the entire resolution if any IP is suspicious.
    for addrinfo in addrinfos:
        ip_str = str(addrinfo[4][0])
        if _is_private_ip(ip_str):
            raise FetchError(
                "ssrf_blocked",
                f"Access to private/internal IP address is not allowed: {hostname} -> {ip_str}",
            )

    # Return first validated IP for DNS pinning
    return str(addrinfos[0][4][0])


class _PinnedDNSNetworkBackend(httpcore.AsyncNetworkBackend):
    """
    httpcore network backend that pins DNS resolution for a specific hostname
    to a pre-validated IP address.

    This prevents DNS rebinding attacks by ensuring the TCP connection goes to
    the exact IP that was validated during the SSRF check, rather than doing a
    fresh DNS resolution that could return a different (malicious) IP.

    For any hostname not in the pinning map, falls back to normal resolution
    via the default AnyIO backend.
    """

    def __init__(self, pinned_hosts: dict[str, str]):
        """
        Args:
            pinned_hosts: Mapping of hostname -> validated IP address.
        """
        self._pinned_hosts = pinned_hosts
        self._default_backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Connect to the pinned IP for mapped hosts, or fall back to default."""
        actual_host = self._pinned_hosts.get(host, host)
        if actual_host != host:
            logger.debug(
                f"[SSRF/DNS-Pin] Connecting to pinned IP {actual_host} "
                f"for hostname {host}"
            )
        return await self._default_backend.connect_tcp(
            actual_host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Delegate unix socket connections to default backend."""
        return await self._default_backend.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        """Delegate sleep to default backend."""
        await self._default_backend.sleep(seconds)


def _create_pinned_transport(
    hostname: str, pinned_ip: str, **kwargs
) -> httpx.AsyncHTTPTransport:
    """
    Create an httpx transport that pins DNS for *hostname* to *pinned_ip*.

    The transport uses a custom httpcore network backend so that when httpcore
    opens a TCP connection to *hostname*, it connects to *pinned_ip* instead
    of performing a fresh DNS lookup. TLS SNI and certificate validation still
    use the original hostname (handled by httpcore's TLS layer), so HTTPS
    works correctly.

    Args:
        hostname: The hostname to pin.
        pinned_ip: The validated IP to connect to.
        **kwargs: Additional keyword arguments forwarded to AsyncHTTPTransport.

    Returns:
        An AsyncHTTPTransport with pinned DNS resolution.
    """
    backend = _PinnedDNSNetworkBackend({hostname: pinned_ip})
    transport = httpx.AsyncHTTPTransport(**kwargs)
    # Replace the connection pool's network backend with our pinned backend.
    # AsyncConnectionPool stores the backend in _network_backend and passes
    # it to new connections. We set it directly on the pool.
    transport._pool._network_backend = backend
    return transport


async def _pre_request_ssrf_check(request: httpx.Request) -> None:
    """
    Event hook that re-validates SSRF just before each outbound request.

    This is a defense-in-depth measure that catches DNS rebinding during
    redirect chains. Even though we pin DNS for the initial request, redirects
    may target new hostnames that need fresh validation.
    """
    hostname = request.url.host
    if hostname:
        host_str = hostname.decode() if isinstance(hostname, bytes) else hostname
        try:
            addrinfos = socket.getaddrinfo(
                host_str, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
        except socket.gaierror:
            raise FetchError(
                "url_not_accessible", f"Cannot resolve hostname: {host_str}"
            )
        for addrinfo in addrinfos:
            ip_str = str(addrinfo[4][0])
            if _is_private_ip(ip_str):
                raise FetchError(
                    "ssrf_blocked",
                    f"DNS rebinding detected: {host_str} resolved to private IP {ip_str}",
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

    Security: Each request uses a per-request httpx client with DNS pinned
    to the pre-validated IP, preventing DNS rebinding TOCTOU attacks.
    Redirect targets are also validated via event hooks.
    """

    # Supported text content types
    _TEXT_TYPES = {"text/html", "text/plain", "text/xml", "application/xml",
                   "application/xhtml+xml", "application/json", "text/csv",
                   "text/markdown"}

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; AnthropicProxy/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    async def _create_client_for_url(self, url: str) -> httpx.AsyncClient:
        """
        Create an httpx client with DNS pinned to the pre-validated IP.

        The client pins the initial hostname's DNS resolution so the TCP
        connection goes to the exact IP validated during SSRF checks.
        Redirect targets are validated via both request and response hooks.
        """
        parsed = urlparse(url)
        hostname = parsed.hostname

        if not hostname:
            raise FetchError("invalid_input", f"Cannot extract hostname from URL: {url}")

        # Resolve hostname and validate ALL IPs; get pinned IP
        pinned_ip = _resolve_and_validate(hostname)
        logger.debug(
            f"[WebFetch/Httpx] DNS pinned: {hostname} -> {pinned_ip}"
        )

        # Build transport with pinned DNS for the target hostname
        transport = _create_pinned_transport(hostname, pinned_ip)

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

        client = httpx.AsyncClient(
            transport=transport,
            timeout=30.0,
            follow_redirects=True,
            event_hooks={
                "request": [_pre_request_ssrf_check],
                "response": [_validate_redirect],
            },
            headers=self._HEADERS,
        )
        return client

    async def fetch(
        self,
        url: str,
        max_content_tokens: Optional[int] = None,
    ) -> FetchResult:
        """Fetch content via direct HTTP request with DNS-pinned transport."""
        _validate_url(url)

        logger.info(f"[WebFetch/Httpx] Fetching: {url}")

        # Create a per-request client with DNS pinned to the validated IP.
        # This is intentionally not a long-lived client — each fetch gets
        # its own client so that DNS is pinned fresh for each URL.
        client = await self._create_client_for_url(url)
        try:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except FetchError:
                raise  # re-raise SSRF/validation errors as-is
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
        finally:
            await client.aclose()


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
