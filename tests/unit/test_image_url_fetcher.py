"""Tests for app.services.image_url_fetcher."""

import asyncio
import base64
import socket
import time

import httpx
import pytest
import respx

from app.schemas.anthropic import (
    Base64ImageSource,
    ImageContent,
    Message,
    TextContent,
    ToolResultContent,
    UrlImageSource,
)
from app.services.image_url_fetcher import (
    ImageUrlFetchError,
    _safe_url,
    fetch_image_url,
    resolve_image_urls,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF_BYTES = b"GIF89a" + b"\x00" * 16
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x00" * 8

PUBLIC_TEST_IP = "93.184.216.34"
_real_getaddrinfo = socket.getaddrinfo


def _fake_getaddrinfo(host, *args, **kwargs):
    """Resolve *.test fixture hosts to a public IP; everything else for real.

    Literal IPs (e.g. 10.0.0.5, 169.254.169.254) resolve to themselves without
    network access, so SSRF tests stay offline.
    """
    if isinstance(host, bytes):
        host = host.decode()
    if host.endswith(".test"):
        if host.startswith("internal."):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_TEST_IP, 0))]
    return _real_getaddrinfo(host, *args, **kwargs)


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


# --- fetch_image_url --------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_png_via_content_type_header():
    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/a.png").mock(
            return_value=httpx.Response(
                200, content=PNG_BYTES, headers={"content-type": "image/png"}
            )
        )
        data, mt = await fetch_image_url(
            "https://example.test/a.png", timeout=5.0, max_bytes=1_000_000
        )
    assert data == PNG_BYTES
    assert mt == "image/png"


@pytest.mark.asyncio
async def test_fetch_explicit_media_type_wins_over_header():
    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/x").mock(
            return_value=httpx.Response(
                200,
                content=PNG_BYTES,
                headers={"content-type": "application/octet-stream"},
            )
        )
        data, mt = await fetch_image_url(
            "https://example.test/x",
            timeout=5.0,
            max_bytes=1_000_000,
            explicit_media_type="image/png",
        )
    assert data == PNG_BYTES
    assert mt == "image/png"


@pytest.mark.asyncio
async def test_fetch_falls_back_to_magic_byte_sniff():
    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/blob").mock(
            return_value=httpx.Response(
                200, content=JPEG_BYTES, headers={"content-type": ""}
            )
        )
        data, mt = await fetch_image_url(
            "https://example.test/blob", timeout=5.0, max_bytes=1_000_000
        )
    assert data == JPEG_BYTES
    assert mt == "image/jpeg"


@pytest.mark.asyncio
async def test_fetch_webp_sniff():
    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/w").mock(
            return_value=httpx.Response(200, content=WEBP_BYTES)
        )
        data, mt = await fetch_image_url(
            "https://example.test/w", timeout=5.0, max_bytes=1_000_000
        )
    assert data == WEBP_BYTES
    assert mt == "image/webp"


@pytest.mark.asyncio
async def test_fetch_gif_sniff():
    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/g").mock(
            return_value=httpx.Response(200, content=GIF_BYTES)
        )
        _, mt = await fetch_image_url(
            "https://example.test/g", timeout=5.0, max_bytes=1_000_000
        )
    assert mt == "image/gif"


@pytest.mark.asyncio
async def test_fetch_rejects_non_http_scheme():
    with pytest.raises(ImageUrlFetchError, match="http or https"):
        await fetch_image_url("file:///etc/passwd", timeout=5.0, max_bytes=1_000_000)


@pytest.mark.asyncio
async def test_fetch_rejects_oversize():
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * 5000
    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/big").mock(
            return_value=httpx.Response(
                200, content=big, headers={"content-type": "image/png"}
            )
        )
        with pytest.raises(ImageUrlFetchError, match="exceeded"):
            await fetch_image_url(
                "https://example.test/big", timeout=5.0, max_bytes=100
            )


@pytest.mark.asyncio
async def test_fetch_rejects_unsupported_content_type():
    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/svg").mock(
            return_value=httpx.Response(
                200, content=b"<svg/>", headers={"content-type": "image/svg+xml"}
            )
        )
        with pytest.raises(ImageUrlFetchError, match="unsupported"):
            await fetch_image_url(
                "https://example.test/svg", timeout=5.0, max_bytes=1_000_000
            )


@pytest.mark.asyncio
async def test_fetch_surfaces_http_status():
    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/missing").mock(return_value=httpx.Response(404))
        with pytest.raises(ImageUrlFetchError, match="status 404"):
            await fetch_image_url(
                "https://example.test/missing", timeout=5.0, max_bytes=1_000_000
            )


def test_safe_url_strips_query_and_userinfo():
    assert (
        _safe_url("https://user:pass@host.test:8443/p/o.png?sig=abc&token=xyz#f")
        == "https://host.test:8443/p/o.png"
    )
    assert _safe_url("https://example.test/a.png") == "https://example.test/a.png"


@pytest.mark.asyncio
async def test_fetch_error_message_redacts_query_string():
    secret = "https://example.test/photo.png?Signature=SECRET123&Expires=999"
    with respx.mock(assert_all_called=True) as r:
        r.get(secret).mock(return_value=httpx.Response(404))
        with pytest.raises(ImageUrlFetchError) as exc:
            await fetch_image_url(secret, timeout=5.0, max_bytes=1_000_000)
    msg = str(exc.value)
    assert "SECRET123" not in msg
    assert "Signature" not in msg
    assert "https://example.test/photo.png" in msg


@pytest.mark.asyncio
async def test_fetch_surfaces_timeout():
    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/slow").mock(
            side_effect=httpx.TimeoutException("too slow")
        )
        with pytest.raises(ImageUrlFetchError, match="timed out"):
            await fetch_image_url(
                "https://example.test/slow", timeout=0.01, max_bytes=1_000_000
            )


# --- resolve_image_urls -----------------------------------------------------


def test_image_source_without_type_field_defaults_to_base64():
    """Backward compat: clients sending the pre-union shape (no source.type)
    must still parse as Base64ImageSource."""
    ic = ImageContent.model_validate(
        {
            "type": "image",
            "source": {"media_type": "image/png", "data": "iVBORw0KGgo="},
        }
    )
    assert isinstance(ic.source, Base64ImageSource)
    assert ic.source.type == "base64"


def test_converter_guards_against_unresolved_url_source():
    """Defense in depth: if a UrlImageSource somehow reaches the converter,
    it raises a clear ValueError, not AttributeError."""
    from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter

    block = ImageContent(
        type="image",
        source=UrlImageSource(type="url", url="https://example.test/x.png"),
    )
    converter = AnthropicToBedrockConverter()
    with pytest.raises(ValueError, match="resolve_image_urls"):
        converter._convert_content_blocks([block])


@pytest.mark.asyncio
async def test_resolve_replaces_url_source_with_base64(monkeypatch):
    monkeypatch.setattr(
        "app.services.image_url_fetcher.settings.image_url_fetch_timeout_s", 5.0
    )
    monkeypatch.setattr(
        "app.services.image_url_fetcher.settings.image_url_fetch_max_bytes", 1_000_000
    )

    msg = Message(
        role="user",
        content=[
            TextContent(type="text", text="hi"),
            ImageContent(
                type="image",
                source=UrlImageSource(type="url", url="https://example.test/a.png"),
            ),
        ],
    )

    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/a.png").mock(
            return_value=httpx.Response(
                200, content=PNG_BYTES, headers={"content-type": "image/png"}
            )
        )
        await resolve_image_urls([msg])

    block = msg.content[1]
    assert isinstance(block, ImageContent)
    assert isinstance(block.source, Base64ImageSource)
    assert block.source.media_type == "image/png"
    assert base64.b64decode(block.source.data) == PNG_BYTES


@pytest.mark.asyncio
async def test_resolve_no_op_when_no_urls():
    msg = Message(role="user", content="hello")
    await resolve_image_urls([msg])  # should not raise


@pytest.mark.asyncio
async def test_resolve_runs_fetches_concurrently(monkeypatch):
    """Three slow URLs should resolve in roughly max(latency), not sum(latency)."""
    monkeypatch.setattr(
        "app.services.image_url_fetcher.settings.image_url_fetch_timeout_s", 5.0
    )
    monkeypatch.setattr(
        "app.services.image_url_fetcher.settings.image_url_fetch_max_bytes", 1_000_000
    )

    delay_s = 0.2

    async def slow_response(request):
        await asyncio.sleep(delay_s)
        return httpx.Response(
            200, content=PNG_BYTES, headers={"content-type": "image/png"}
        )

    msg = Message(
        role="user",
        content=[
            ImageContent(
                type="image",
                source=UrlImageSource(type="url", url=f"https://example.test/{i}.png"),
            )
            for i in range(3)
        ],
    )

    with respx.mock(assert_all_called=True) as r:
        for i in range(3):
            r.get(f"https://example.test/{i}.png").mock(side_effect=slow_response)

        start = time.perf_counter()
        await resolve_image_urls([msg])
        elapsed = time.perf_counter() - start

    # Concurrent: should be close to delay_s, definitely well under 3*delay_s.
    assert elapsed < delay_s * 2.5, f"expected concurrent fetch, took {elapsed:.3f}s"

    assert isinstance(msg.content, list)
    for block in msg.content:
        assert isinstance(block, ImageContent)
        assert isinstance(block.source, Base64ImageSource)


@pytest.mark.asyncio
async def test_resolve_walks_into_tool_result_content(monkeypatch):
    """A URL-source ImageContent nested inside ToolResultContent.content
    must be resolved (regression — previously skipped, crashed converter)."""
    monkeypatch.setattr(
        "app.services.image_url_fetcher.settings.image_url_fetch_timeout_s", 5.0
    )
    monkeypatch.setattr(
        "app.services.image_url_fetcher.settings.image_url_fetch_max_bytes", 1_000_000
    )

    nested = ImageContent(
        type="image",
        source=UrlImageSource(type="url", url="https://example.test/nested.png"),
    )
    msg = Message(
        role="user",
        content=[
            ToolResultContent(
                type="tool_result",
                tool_use_id="toolu_abc",
                content=[nested],
            ),
        ],
    )

    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/nested.png").mock(
            return_value=httpx.Response(
                200, content=PNG_BYTES, headers={"content-type": "image/png"}
            )
        )
        await resolve_image_urls([msg])

    assert isinstance(nested.source, Base64ImageSource)
    assert nested.source.media_type == "image/png"


@pytest.mark.asyncio
async def test_resolve_propagates_first_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.image_url_fetcher.settings.image_url_fetch_timeout_s", 5.0
    )
    monkeypatch.setattr(
        "app.services.image_url_fetcher.settings.image_url_fetch_max_bytes", 1_000_000
    )

    msg = Message(
        role="user",
        content=[
            ImageContent(
                type="image",
                source=UrlImageSource(type="url", url="https://example.test/bad.png"),
            ),
        ],
    )

    with respx.mock(assert_all_called=True) as r:
        r.get("https://example.test/bad.png").mock(return_value=httpx.Response(500))
        with pytest.raises(ImageUrlFetchError, match="status 500"):
            await resolve_image_urls([msg])


# --- SSRF protection ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://169.254.170.2/v2/credentials",
        "http://127.0.0.1:8000/admin",
        "http://10.0.0.5/x.png",
        "http://[::1]/x.png",
        "http://localhost/x.png",
        "https://internal.example.test/x.png",  # hostname resolving to 10.0.0.7
    ],
)
async def test_fetch_blocks_private_and_metadata_destinations(url):
    with respx.mock(assert_all_called=False) as r:
        route = r.get(url).mock(return_value=httpx.Response(200, content=PNG_BYTES))
        with pytest.raises(ImageUrlFetchError, match="not allowed"):
            await fetch_image_url(url, timeout=5.0, max_bytes=1_000_000)
        assert not route.called, "no request may be sent to a blocked destination"


@pytest.mark.asyncio
async def test_fetch_blocks_redirect_to_metadata():
    with respx.mock(assert_all_called=False) as r:
        r.get("https://example.test/redirect").mock(
            return_value=httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        )
        metadata = r.get("http://169.254.169.254/latest/meta-data/").mock(
            return_value=httpx.Response(200, content=PNG_BYTES)
        )
        with pytest.raises(ImageUrlFetchError, match="not allowed"):
            await fetch_image_url(
                "https://example.test/redirect", timeout=5.0, max_bytes=1_000_000
            )
        assert not metadata.called


@pytest.mark.asyncio
async def test_blocked_error_does_not_leak_resolved_ip():
    with pytest.raises(ImageUrlFetchError) as exc:
        await fetch_image_url(
            "https://internal.example.test/x.png", timeout=5.0, max_bytes=1_000_000
        )
    assert "10.0.0.7" not in str(exc.value)


@pytest.mark.asyncio
async def test_private_destinations_allowed_when_opted_in(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "image_url_fetch_allow_private_networks", True)
    with respx.mock(assert_all_called=True) as r:
        r.get("http://10.0.0.5/x.png").mock(
            return_value=httpx.Response(
                200, content=PNG_BYTES, headers={"content-type": "image/png"}
            )
        )
        data, media_type = await fetch_image_url(
            "http://10.0.0.5/x.png", timeout=5.0, max_bytes=1_000_000
        )
    assert data == PNG_BYTES
    assert media_type == "image/png"
