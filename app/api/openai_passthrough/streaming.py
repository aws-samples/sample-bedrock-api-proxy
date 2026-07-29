"""SSE passthrough with usage tee.

The async generator yields raw response bytes line-by-line so the FastAPI
StreamingResponse forwards them unchanged. After upstream stream ends, it
calls the supplied on_complete callback with the captured usage dict so the
caller can record usage to DynamoDB.

Responses API note: bedrock-mantle emits Responses SSE as data-only frames
(``data: {"type": "response.completed", ...}``) without the corresponding
``event: response.completed`` line that the real OpenAI Responses API
includes. Strict SSE clients (e.g. OpenAI Codex CLI) key off the ``event:``
field and reject streams that lack it. For api_surface="responses" we
synthesize ``event: <type>`` lines from the JSON ``type`` field on each frame
to maintain wire compatibility with the real OpenAI server.

Upstream-error contract: ``open_upstream_stream`` returns the (resp, error_body)
tuple BEFORE FastAPI has committed any response headers. If the upstream
returns a non-2xx status, the caller can hand back a JSONResponse with the
real upstream status code instead of a fake 200 streaming response.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from app.api.openai_passthrough.client import get_client, upstream_headers, upstream_url
from app.api.openai_passthrough.usage_extractor import try_extract_usage_from_sse

logger = logging.getLogger(__name__)


def _log_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return repr(value)


def _extract_event_type(raw_line: str) -> str | None:
    """Return the ``type`` field from a ``data:`` JSON frame, or None if not parseable."""
    line = raw_line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    event_type = obj.get("type")
    return event_type if isinstance(event_type, str) else None


class UpstreamConnectionError(Exception):
    """Raised by open_upstream_stream when the upstream is unreachable.

    Carries an HTTP status to return to the client (502 Bad Gateway by
    default) and the underlying httpx exception for logging.
    """

    def __init__(self, status_code: int, message: str, exc_type: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.exc_type = exc_type


async def open_upstream_stream(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    extra_headers: dict[str, str] | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> tuple[httpx.Response, bytes | None]:
    """Open an upstream streaming request and peek at the status code.

    Returns (resp, error_body):
      - error_body is None if upstream returned 2xx — caller streams the body
        and is responsible for closing the response.
      - error_body is the full upstream body bytes if status >= 400 — caller
        should return a JSONResponse with resp.status_code. The response is
        already closed.

    Raises UpstreamConnectionError if the upstream is unreachable
    (timeout, DNS, TLS, connection reset). Caller should turn this into a
    JSON 502/504 with the carried status code.
    """
    client = get_client()
    headers = upstream_headers(extra_headers, api_key=api_key)
    req = client.build_request(
        method,
        upstream_url(
            path,
            base_url=base_url,
            model=body.get("model") if isinstance(body, dict) else None,
        ),
        json=body,
        headers=headers,
    )
    try:
        resp = await client.send(req, stream=True)
    except httpx.TimeoutException as exc:
        logger.error("[OPENAI-PASSTHROUGH] upstream timeout opening stream: %s", exc)
        raise UpstreamConnectionError(
            status_code=504,
            message=f"upstream timeout: {exc}",
            exc_type=type(exc).__name__,
        ) from exc
    except httpx.RequestError as exc:
        logger.error(
            "[OPENAI-PASSTHROUGH] upstream connection error opening stream: %s", exc
        )
        raise UpstreamConnectionError(
            status_code=502,
            message=f"upstream connection failed: {exc}",
            exc_type=type(exc).__name__,
        ) from exc

    if resp.status_code >= 400:
        try:
            error_body = await resp.aread()
        finally:
            await resp.aclose()
        return resp, error_body
    return resp, None


async def stream_passthrough_response(
    resp: httpx.Response,
    api_surface: str,
    on_complete: Callable[[dict[str, Any]], Awaitable[None] | None],
) -> AsyncIterator[bytes]:
    """Stream the body of an already-opened 2xx upstream response.

    Closes the response when done.
    """
    usage: dict[str, Any] = {}
    synthesize_event_lines = api_surface == "responses"

    try:
        async for raw_line in resp.aiter_lines():
            if logger.isEnabledFor(logging.INFO):
                logger.info(
                    "[OPENAI-PASSTHROUGH] upstream stream chunk %s",
                    _log_json(
                        {
                            "api_surface": api_surface,
                            "line": raw_line,
                        }
                    ),
                )
            # For the Responses API, prepend an ``event: <type>`` line whenever
            # we see a data frame whose JSON carries a ``type`` field. This
            # restores the OpenAI-spec SSE format that strict clients expect.
            if synthesize_event_lines:
                event_type = _extract_event_type(raw_line)
                if event_type is not None:
                    yield f"event: {event_type}\n".encode()

            # Upstream gives us SSE lines without trailing newlines; restore the
            # framing byte so the SSE body is well-formed for the downstream client.
            yield (raw_line + "\n").encode("utf-8")
            try_extract_usage_from_sse(raw_line, usage, api_surface)
    except (httpx.RequestError, httpx.TimeoutException) as exc:
        # Upstream connection/timeout failure during streaming. OpenAI SDK clients
        # expect a clean SSE termination, not an abruptly closed stream.
        logger.error("[OPENAI-PASSTHROUGH] upstream stream connection error: %s", exc)
        err = {
            "error": {
                "message": f"upstream connection failed: {type(exc).__name__}",
                "type": "upstream_error",
            }
        }
        yield ("data: " + json.dumps(err) + "\n\n").encode("utf-8")
        yield b"data: [DONE]\n\n"
        return
    except Exception as exc:
        # Unexpected error — re-raise so FastAPI can convert to 500.
        logger.error("[OPENAI-PASSTHROUGH] upstream stream error: %s", exc)
        raise
    finally:
        await resp.aclose()

    if usage:
        result = on_complete(usage)
        # Support both sync and async callbacks
        if hasattr(result, "__await__"):
            await result  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Backwards-compat helper: streams in one call. Useful where the caller doesn't
# need to differentiate streaming-error vs streaming-success at the HTTP-status
# level (legacy code path; new code should use open_upstream_stream +
# stream_passthrough_response so non-2xx errors come back as a real JSONResponse).
# ---------------------------------------------------------------------------


async def stream_passthrough(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    api_surface: str,
    on_complete: Callable[[dict[str, Any]], Awaitable[None] | None],
    extra_headers: dict[str, str] | None = None,
) -> AsyncIterator[bytes]:
    """Open + stream + close in one call. Status-checking variant lives in
    open_upstream_stream/stream_passthrough_response."""
    resp, error_body = await open_upstream_stream(method, path, body, extra_headers)
    if error_body is not None:
        # Legacy callers can't surface a real error status here; emit an SSE
        # error frame and terminate cleanly so the client doesn't hang.
        try:
            err_payload = json.loads(error_body)
        except (ValueError, TypeError):
            err_payload = {
                "error": {
                    "message": error_body.decode("utf-8", "replace"),
                    "type": "upstream_error",
                }
            }
        yield ("data: " + json.dumps(err_payload) + "\n\n").encode("utf-8")
        yield b"data: [DONE]\n\n"
        return

    async for chunk in stream_passthrough_response(resp, api_surface, on_complete):
        yield chunk
