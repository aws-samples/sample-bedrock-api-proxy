# Image URL Sources for `/v1/messages`

**Date**: 2026-05-28
**Status**: Approved, in implementation

## Problem

Clients sometimes have an image as a URL — typically a pre-signed object on internal storage (e.g. `https://kdsa1-uat.wilmar.cn/.../order-photo-...png`) — and want to pass it to a Claude model through the proxy without first downloading and base64-encoding it. Today the proxy's `ImageContent` schema only accepts `source.type: "base64"`, so URL-based payloads (whether Anthropic-native `source.type: "url"` or OpenAI-style `image_url`) are rejected at validation time.

## Goal

Add native URL support for image content blocks on `/v1/messages`, mirroring Anthropic's native API shape. The proxy fetches the URL, resolves the bytes, and forwards to Bedrock as it does today for base64 images. Documents (PDFs) are out of scope — this change is images only.

## Non-goals

- OpenAI-style `image_url` content blocks on `/v1/messages` (clients are switching to Anthropic-native shape).
- Document/PDF URL sources.
- Proxy-side image resizing/recompression.
- SSRF allowlists or private-IP blocking — relying on network/firewall policy for that.
- Caching fetched images across requests.

## Design

### Request shape (Anthropic-native)

```json
{
  "type": "image",
  "source": {
    "type": "url",
    "url": "https://kdsa1-uat.wilmar.cn/.../order-photo.png"
  }
}
```

`media_type` is optional on the URL source; the fetcher resolves it from the `Content-Type` header, falling back to magic-byte sniffing.

### Schema (`app/schemas/anthropic.py`)

`ImageSource` becomes a discriminated union over `type`:

```python
class Base64ImageSource(BaseModel):
    type: Literal["base64"] = "base64"
    media_type: Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
    data: str  # base64

class UrlImageSource(BaseModel):
    type: Literal["url"] = "url"
    url: str
    media_type: Optional[Literal["image/jpeg", "image/png", "image/gif", "image/webp"]] = None

ImageSource = Annotated[
    Union[Base64ImageSource, UrlImageSource],
    Field(discriminator="type"),
]
```

`ImageContent.source` references this union. The existing `ImageSource` symbol stays as the union alias for back-compat with imports.

### Fetcher service (`app/services/image_url_fetcher.py`, new)

```python
class ImageUrlFetchError(ValueError): ...

async def fetch_image_url(
    url: str, *, timeout: float, max_bytes: int,
) -> tuple[bytes, str]:
    """Returns (raw_bytes, media_type). Raises ImageUrlFetchError."""

async def resolve_image_urls(messages: list[Message]) -> None:
    """Walk messages, fetch every UrlImageSource concurrently via asyncio.gather,
    replace block.source with Base64ImageSource in-place. Mutates messages."""
```

Behavior:
- `httpx.AsyncClient` with the configured timeout.
- Reject non-`http(s)` schemes.
- Stream the body; abort if total exceeds `max_bytes`.
- Resolve media_type by priority: explicit `media_type` field → `Content-Type` header → magic-byte sniff (PNG/JPEG/GIF/WEBP).
- Validate the resolved media_type is in Anthropic's supported set.
- No allowlist, no private-IP blocking.

`resolve_image_urls()` collects all URL sources across all messages, fetches them concurrently with `asyncio.gather`, and mutates each block's `.source` to a `Base64ImageSource`. The existing converter at `app/converters/anthropic_to_bedrock.py:420-431` is untouched — it sees only base64 sources after pre-resolution.

### API integration (`app/api/messages.py`)

Add early in `create_message()` (after auth/rate limiting, before the converter):

```python
try:
    await resolve_image_urls(request.messages)
except ImageUrlFetchError as e:
    raise HTTPException(status_code=400, detail={
        "type": "error",
        "error": {"type": "invalid_request_error", "message": str(e)},
    })
```

Single call site covers both streaming and non-streaming branches.

### Configuration (`app/core/config.py`)

```python
image_url_fetch_timeout_s: float = Field(default=30.0, env="IMAGE_URL_FETCH_TIMEOUT_S")
image_url_fetch_max_bytes: int = Field(default=20 * 1024 * 1024, env="IMAGE_URL_FETCH_MAX_BYTES")
```

Both added to `.env.example` with comments. No feature flag — URL support is additive and base64 clients are unaffected.

### Size policy

The proxy caps fetches at 20 MB by default to prevent abuse. Bedrock itself enforces stricter limits per model (~5 MB after base64 for Claude); oversized images surface as Bedrock-side validation errors. We don't downscale — clean separation of concerns and avoids pulling Pillow into deps.

## Tests

- `tests/unit/test_image_url_fetcher.py` (new): happy paths for PNG/JPEG/GIF/WEBP, media_type priority, size cap mid-stream, timeout, non-http(s) rejection, unsupported content type, HTTP 4xx/5xx, concurrency assertion.
- `tests/unit/test_converters.py`: `UrlImageSource` after `resolve_image_urls` becomes a Bedrock `image` block with the right bytes.
- `tests/integration/test_messages_api.py`: end-to-end POST with URL image source, mocked URL response and mocked Bedrock.

## Rollout

No flag. Ship the feature; existing base64 clients are unaffected. Operators can tune `IMAGE_URL_FETCH_TIMEOUT_S` and `IMAGE_URL_FETCH_MAX_BYTES` per environment.

## Alternatives considered

- **Client-side base64 only**: simplest but pushes per-client work and bytes onto the wire twice (URL → client → base64 → proxy).
- **OpenAI-style `image_url` on `/v1/messages`**: rejected — clients are committing to native Anthropic shape.
- **OpenAI passthrough endpoint**: works for non-Claude models via bedrock-mantle, but the user's model is Claude.
- **Sync httpx in the converter**: rejected — converters stay pure of I/O; pre-resolution in the handler keeps concurrency and async clean.
