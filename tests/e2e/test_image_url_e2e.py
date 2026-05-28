"""End-to-end test for image URL sources against a deployed proxy.

Set PROXY_URL and PROXY_API_KEY env vars to run. Without them, all tests skip.

Example:
    PROXY_URL=https://d1234.cloudfront.net \\
    PROXY_API_KEY=sk-... \\
    uv run pytest tests/e2e/test_image_url_e2e.py -v -s

The model targeted is a Claude model (InvokeModel path), since the original
brainstorming was about getting URL-based image input working with Claude.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx
import pytest

PROXY_URL = os.environ.get("PROXY_URL", "").rstrip("/")
API_KEY = os.environ.get("PROXY_API_KEY", "")
MODEL = os.environ.get("PROXY_TEST_MODEL", "claude-sonnet-4-5-20250929")

# Small public PNG that doesn't UA-filter. Override via PROXY_TEST_IMAGE_URL.
# Default uses Google's static WebP gallery — a small, stable, UA-friendly CDN.
PUBLIC_IMAGE_URL = os.environ.get(
    "PROXY_TEST_IMAGE_URL",
    "https://www.gstatic.com/webp/gallery/1.webp",
)
# A 404-returning URL whose host resolves and returns proper HTTP status.
NOT_FOUND_URL = "https://www.gstatic.com/webp/gallery/this-does-not-exist.webp"

pytestmark = pytest.mark.skipif(
    not (PROXY_URL and API_KEY),
    reason="PROXY_URL and PROXY_API_KEY required for e2e tests",
)


def _post_messages(payload: dict[str, Any], timeout: float = 60.0) -> httpx.Response:
    return httpx.post(
        f"{PROXY_URL}/v1/messages",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )


def _build_request(
    image_block: dict[str, Any],
    prompt: str = "Describe this image in one short sentence.",
) -> dict[str, Any]:
    return {
        "model": MODEL,
        "max_tokens": 256,
        "messages": [
            {
                "role": "user",
                "content": [image_block, {"type": "text", "text": prompt}],
            }
        ],
    }


def _assert_message_shape(body: dict[str, Any]) -> None:
    assert body.get("type") == "message", body
    assert body.get("role") == "assistant", body
    content = body.get("content", [])
    assert isinstance(content, list) and content, body
    text_blocks = [b for b in content if b.get("type") == "text"]
    assert text_blocks, f"no text content in response: {body}"
    assert text_blocks[0]["text"].strip(), f"empty text: {body}"


def _assert_invalid_request_error(body: dict[str, Any]) -> dict[str, Any]:
    """Walk the proxy's error envelope to find the inner invalid_request_error.
    Shape: {"type":"error","error":{"type":"invalid_request_error","message":"..."}}.
    Returns the inner error dict for further assertions on `message`.
    """
    assert body.get("type") == "error", body
    inner = body.get("error", {})
    assert inner.get("type") == "invalid_request_error", body
    assert inner.get("message"), body
    return inner


# --- Happy paths -----------------------------------------------------------


def test_url_image_source_anthropic_native():
    """The headline use case: source.type='url' on a Claude model."""
    payload = _build_request(
        {
            "type": "image",
            "source": {"type": "url", "url": PUBLIC_IMAGE_URL},
        }
    )
    r = _post_messages(payload)
    assert r.status_code == 200, r.text
    _assert_message_shape(r.json())


def test_url_image_source_with_explicit_media_type():
    """media_type on a URL source is optional but should be honored if given."""
    actual_ct = (
        httpx.head(PUBLIC_IMAGE_URL, follow_redirects=True, timeout=15)
        .headers.get("content-type", "image/png")
        .split(";", 1)[0]
        .strip()
        .lower()
    )
    payload = _build_request(
        {
            "type": "image",
            "source": {
                "type": "url",
                "url": PUBLIC_IMAGE_URL,
                "media_type": actual_ct,
            },
        }
    )
    r = _post_messages(payload)
    assert r.status_code == 200, r.text
    _assert_message_shape(r.json())


def test_url_image_source_without_type_field_back_compat():
    """Pre-discriminated-union shape: source has no `type` field. Discriminator
    defaults to 'base64' so this is preserved as a Base64ImageSource. Should
    still validate (will fail inside Bedrock if data isn't real base64, but
    the proxy should accept the shape and surface the Bedrock error, not 422)."""
    tiny_png = base64.b64encode(
        bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108020000009077"
            "53DE0000000C4944415478DA63F8FF1F0000050001005B5DB1F30000000049"
            "454E44AE426082"
        )
    ).decode()
    payload = _build_request(
        {
            "type": "image",
            # Note: no `type` field on source — old shape.
            "source": {"media_type": "image/png", "data": tiny_png},
        }
    )
    r = _post_messages(payload)
    # 200 ideally, or 4xx from Bedrock for tiny image — but NOT 422 from proxy.
    assert r.status_code != 422, f"proxy rejected legacy typeless source: {r.text}"


def test_base64_image_source_regression():
    """Regression: existing base64-source flow must still work after the
    discriminated-union refactor."""
    img_resp = httpx.get(PUBLIC_IMAGE_URL, timeout=30)
    img = img_resp.content
    media_type = (
        img_resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        or "image/png"
    )
    payload = _build_request(
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(img).decode(),
            },
        }
    )
    r = _post_messages(payload)
    assert r.status_code == 200, r.text
    _assert_message_shape(r.json())


# --- Error paths -----------------------------------------------------------


def test_url_image_source_404_returns_400_invalid_request():
    """A 4xx from the upstream image URL becomes a 400 invalid_request_error."""
    payload = _build_request(
        {"type": "image", "source": {"type": "url", "url": NOT_FOUND_URL}}
    )
    r = _post_messages(payload)
    assert r.status_code == 400, r.text
    _assert_invalid_request_error(r.json())


def test_url_image_source_error_message_does_not_leak_query_string():
    """Presigned-token-style query strings must not echo back to the client."""
    secret = "SECRETTOKEN12345"
    bad_url = f"{NOT_FOUND_URL}?Signature={secret}&Expires=999"
    payload = _build_request(
        {"type": "image", "source": {"type": "url", "url": bad_url}}
    )
    r = _post_messages(payload)
    assert r.status_code == 400, r.text
    assert secret not in r.text, "URL secret leaked into 400 response"
    assert "Signature" not in r.text, "URL query key leaked into 400 response"


def test_url_image_source_bad_scheme():
    """file:// must be rejected up-front, not attempted."""
    payload = _build_request(
        {
            "type": "image",
            "source": {"type": "url", "url": "file:///etc/passwd"},
        }
    )
    r = _post_messages(payload)
    assert r.status_code == 400, r.text


def test_url_image_source_unsupported_content_type():
    """A URL that returns text/html (e.g. an HTML page, not an image) is rejected."""
    payload = _build_request(
        {
            "type": "image",
            "source": {"type": "url", "url": "https://example.com/"},
        }
    )
    r = _post_messages(payload)
    assert r.status_code == 400, r.text


# --- count_tokens ----------------------------------------------------------


def test_count_tokens_accepts_url_source_without_fetching():
    """count_tokens should accept the shape without trying to fetch the URL.
    A bogus host that would fail to resolve must NOT cause a 400 here."""
    r = httpx.post(
        f"{PROXY_URL}/v1/messages/count_tokens",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": "https://this-host-should-not-resolve.invalid/x.png",
                            },
                        },
                        {"type": "text", "text": "hi"},
                    ],
                }
            ],
        },
        timeout=30,
    )
    # Either 200 with a token count, or a Bedrock-side 4xx — but the proxy
    # itself must NOT 400 with "image URL fetch failed", since it shouldn't
    # have tried to fetch.
    assert "image URL fetch" not in r.text, r.text
