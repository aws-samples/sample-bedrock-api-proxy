"""Coercion of OpenAI-style image_url content blocks to Anthropic-native shape."""
import base64

import pytest

from app.schemas.anthropic import (
    Base64ImageSource,
    ImageContent,
    Message,
    TextContent,
    UrlImageSource,
)


def test_http_url_image_url_becomes_url_image_source():
    msg = Message.model_validate(
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.test/a.png"},
                },
                {"type": "text", "text": "describe this"},
            ],
        }
    )
    assert isinstance(msg.content, list)
    img, txt = msg.content
    assert isinstance(img, ImageContent)
    assert isinstance(img.source, UrlImageSource)
    assert img.source.url == "https://example.test/a.png"
    assert isinstance(txt, TextContent)


def test_https_url_image_url_becomes_url_image_source():
    msg = Message.model_validate(
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "http://x.test/a.jpg"}}
            ],
        }
    )
    img = msg.content[0]
    assert isinstance(img, ImageContent)
    assert isinstance(img.source, UrlImageSource)


def test_data_url_image_url_becomes_base64_source():
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    data_url = f"data:image/png;base64,{base64.b64encode(raw).decode()}"
    msg = Message.model_validate(
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": data_url}}],
        }
    )
    img = msg.content[0]
    assert isinstance(img, ImageContent)
    assert isinstance(img.source, Base64ImageSource)
    assert img.source.media_type == "image/png"
    assert base64.b64decode(img.source.data) == raw


def test_data_url_without_base64_marker_reencodes():
    """data:image/svg+xml,<urlencoded> — though SVG isn't a supported media_type
    so Bedrock will reject downstream; just confirms the coercion path runs."""
    # Use a JPEG-typed data URL to stay within supported types.
    raw = b"\xff\xd8\xff\xe0" + b"\x00" * 16
    # Plain (non-base64) data URL with urlencoded body.
    plain = "".join(f"%{b:02x}" for b in raw)
    data_url = f"data:image/jpeg,{plain}"
    msg = Message.model_validate(
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": data_url}}],
        }
    )
    img = msg.content[0]
    assert isinstance(img.source, Base64ImageSource)
    assert img.source.media_type == "image/jpeg"
    assert base64.b64decode(img.source.data) == raw


def test_native_anthropic_image_block_is_untouched():
    """Mixing the two shapes in one request must work."""
    msg = Message.model_validate(
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "url", "url": "https://x.test/native.png"},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "https://x.test/openai.png"},
                },
            ],
        }
    )
    a, b = msg.content
    assert isinstance(a, ImageContent) and isinstance(a.source, UrlImageSource)
    assert a.source.url == "https://x.test/native.png"
    assert isinstance(b, ImageContent) and isinstance(b.source, UrlImageSource)
    assert b.source.url == "https://x.test/openai.png"


def test_image_url_without_url_falls_through_to_validation_error():
    """Malformed shape (no url) should produce a Pydantic ValidationError, not
    silently coerce to something nonsensical."""
    with pytest.raises(Exception):  # ValidationError
        Message.model_validate(
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {}}],
            }
        )


def test_string_content_still_works():
    """Regression: existing string-content path is unaffected."""
    msg = Message.model_validate({"role": "user", "content": "hello"})
    assert isinstance(msg.content, list)
    assert isinstance(msg.content[0], TextContent)
    assert msg.content[0].text == "hello"
