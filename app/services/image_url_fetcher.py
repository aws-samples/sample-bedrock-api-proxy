"""
Image URL fetcher.

Resolves UrlImageSource blocks on inbound /v1/messages requests by downloading
the URL and replacing the source with a Base64ImageSource so the rest of the
pipeline (converter -> Bedrock) operates on bytes as it does today.

No SSRF allowlist or private-IP blocking by design - operators are expected to
restrict outbound network reachability via firewall/VPC policy. We enforce
scheme, wall-clock timeout, size cap, and supported content type only.
"""

import asyncio
import base64
import logging
from typing import Literal, cast
from urllib.parse import urlparse, urlunparse

import httpx

from app.core.config import settings
from app.schemas.anthropic import (
    Base64ImageSource,
    ImageContent,
    Message,
    ToolResultContent,
    UrlImageSource,
)

logger = logging.getLogger(__name__)

SUPPORTED_MEDIA_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)

_MAGIC_BYTES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class ImageUrlFetchError(ValueError):
    """Raised when an image URL cannot be fetched or is unacceptable."""


def _sniff_media_type(data: bytes) -> str | None:
    for prefix, mt in _MAGIC_BYTES:
        if data.startswith(prefix):
            return mt
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _safe_url(url: str) -> str:
    """Return URL with query/fragment/userinfo stripped — safe for client error messages.

    Full URL is logged server-side; this redacted form is what surfaces in 4xx
    response bodies so presigned tokens / SAS / credentials don't leak to clients.
    """
    try:
        p = urlparse(url)
        host = p.hostname or ""
        if p.port:
            host = f"{host}:{p.port}"
        return urlunparse((p.scheme, host, p.path, "", "", ""))
    except Exception:
        return "<image url>"


async def _stream_to_buffer(
    response: httpx.Response, max_bytes: int, safe_url: str
) -> bytearray:
    buf = bytearray()
    async for chunk in response.aiter_bytes():
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise ImageUrlFetchError(
                f"image URL exceeded {max_bytes} bytes: {safe_url}"
            )
    return buf


async def fetch_image_url(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    explicit_media_type: str | None = None,
) -> tuple[bytes, str]:
    """Fetch an image URL.

    Returns (raw_bytes, media_type). Raises ImageUrlFetchError on any failure
    (timeout, oversized, bad scheme, unsupported content type, HTTP error).

    `timeout` is total wall-clock budget for the entire fetch (connect + read).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ImageUrlFetchError(
            f"image URL must use http or https scheme: {_safe_url(url)}"
        )

    safe = _safe_url(url)

    async def _do_fetch() -> tuple[bytearray, str]:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise ImageUrlFetchError(
                        f"image URL returned status {response.status_code}: {safe}"
                    )
                buf = await _stream_to_buffer(response, max_bytes, safe)
                return buf, response.headers.get("content-type", "")

    try:
        buf, content_type_header = await asyncio.wait_for(_do_fetch(), timeout=timeout)
    except (TimeoutError, httpx.TimeoutException) as e:
        raise ImageUrlFetchError(f"image URL fetch timed out: {safe}") from e
    except httpx.HTTPError as e:
        raise ImageUrlFetchError(f"image URL fetch failed: {safe}: {e}") from e

    raw = bytes(buf)

    header_media_type = content_type_header.split(";", 1)[0].strip().lower() or None
    media_type = explicit_media_type or header_media_type or _sniff_media_type(raw)

    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise ImageUrlFetchError(
            f"unsupported image media type {media_type!r} for {safe}"
        )

    return raw, cast(str, media_type)


def _collect_url_image_blocks(blocks: list) -> list[ImageContent]:
    """Find all ImageContent blocks with UrlImageSource, including those nested
    inside ToolResultContent.content."""
    out: list[ImageContent] = []
    for block in blocks:
        if isinstance(block, ImageContent) and isinstance(block.source, UrlImageSource):
            out.append(block)
        elif isinstance(block, ToolResultContent):
            inner = block.content
            if isinstance(inner, list):
                out.extend(_collect_url_image_blocks(inner))
    return out


async def resolve_image_urls(messages: list[Message]) -> None:
    """Walk messages (including tool_result.content), fetch every UrlImageSource
    concurrently, replace each block.source with a Base64ImageSource in-place.

    Mutates messages. Raises ImageUrlFetchError if any fetch fails.
    """
    locations: list[ImageContent] = []
    for msg in messages:
        content = msg.content
        if isinstance(content, list):
            locations.extend(_collect_url_image_blocks(content))

    if not locations:
        return

    coros = []
    for block in locations:
        # block.source is UrlImageSource — narrow for type checker.
        assert isinstance(block.source, UrlImageSource)
        coros.append(
            fetch_image_url(
                block.source.url,
                timeout=settings.image_url_fetch_timeout_s,
                max_bytes=settings.image_url_fetch_max_bytes,
                explicit_media_type=block.source.media_type,
            )
        )

    logger.info("[image_url_fetcher] resolving %d image URL(s)", len(coros))
    results = await asyncio.gather(*coros, return_exceptions=True)

    for block, result in zip(locations, results, strict=True):
        if isinstance(result, BaseException):
            raise result
        raw_bytes, media_type = result
        block.source = Base64ImageSource(
            type="base64",
            media_type=cast(
                Literal["image/jpeg", "image/png", "image/gif", "image/webp"],
                media_type,
            ),
            data=base64.b64encode(raw_bytes).decode("ascii"),
        )
