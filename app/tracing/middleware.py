"""
FastAPI middleware for creating root request spans.

Only POST /v1/messages is traced (whitelist approach).
All other paths are passed through without tracing.
"""
import json
import hashlib
import logging
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from opentelemetry import trace as trace_api
from opentelemetry.trace import SpanContext, TraceFlags, NonRecordingSpan

from app.tracing.attributes import (
    SPAN_TRACE_ROOT,
    LANGFUSE_SESSION_ID,
)
from app.tracing.provider import get_tracer
from app.tracing.session_store import get_session_store

logger = logging.getLogger(__name__)


def _derive_session_from_body(body: bytes) -> Optional[str]:
    """Try to derive a session ID from the request body JSON."""
    try:
        data = json.loads(body)
        model = data.get("model", "")

        # Check explicit metadata.session_id
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and metadata.get("session_id"):
            return metadata["session_id"]

        # Auto-derive from first user message + model
        messages = data.get("messages", [])
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                key = f"{model}:{content}"
                return f"auto-{hashlib.sha256(key.encode()).hexdigest()[:16]}"
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        key = f"{model}:{block['text']}"
                        return f"auto-{hashlib.sha256(key.encode()).hexdigest()[:16]}"
            break  # Only check first user message
    except Exception:
        pass
    return None


class TracingMiddleware(BaseHTTPMiddleware):
    """Middleware that creates root request spans for API calls."""

    async def dispatch(self, request: Request, call_next):
        # Whitelist: only trace POST /v1/messages (chat completions)
        if request.method == "POST" and request.url.path == "/v1/messages":
            tracer = get_tracer("app.middleware.tracing")
            return await self._dispatch_messages(request, call_next, tracer)

        # Everything else passes through without tracing
        return await call_next(request)

    async def _dispatch_messages(self, request: Request, call_next, tracer):
        """Handle /v1/messages with turn-based trace structure."""
        store = get_session_store()

        # Determine session ID
        session_id = request.headers.get("x-session-id") if hasattr(request, "headers") else None
        if not session_id:
            try:
                body = await request.body()
                if body:
                    session_id = _derive_session_from_body(body)
            except Exception:
                pass

        if not session_id:
            # No session ID — still trace but as a standalone turn
            # Create a one-off root span so the request still appears in Langfuse
            root_span = tracer.start_span(SPAN_TRACE_ROOT)
            parent_ctx = trace_api.set_span_in_context(root_span)

            request.state.trace_session_id = None
            request.state.trace_parent_ctx = parent_ctx
            request.state.trace_turn_num = 1
            request.state.trace_is_first_turn = True
            request.state.trace_root_span = root_span

            try:
                response = await call_next(request)
                return response
            except Exception:
                raise
            finally:
                root_span.end()

        # Look up or create trace context for this session
        existing = store.get(session_id)
        is_first_turn = existing is None

        if is_first_turn:
            # First request: create a root span to anchor the trace
            root_span = tracer.start_span(SPAN_TRACE_ROOT)
            span_ctx = root_span.get_span_context()

            # Set session ID on root span
            root_span.set_attribute(LANGFUSE_SESSION_ID, session_id)

            store.put(session_id, span_ctx.trace_id, span_ctx.span_id, root_span)

            # Build parent context from the root span
            parent_ctx = trace_api.set_span_in_context(root_span)
        else:
            trace_id, span_id, turn_count, root_span = existing
            # Build parent context from stored trace/span IDs
            parent_span_context = SpanContext(
                trace_id=trace_id,
                span_id=span_id,
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
            parent_span = NonRecordingSpan(parent_span_context)
            parent_ctx = trace_api.set_span_in_context(parent_span)

        # Get next turn number
        turn_num = store.next_turn(session_id)

        # Store turn-based tracing info in request.state for messages.py to use
        request.state.trace_session_id = session_id
        request.state.trace_parent_ctx = parent_ctx
        request.state.trace_turn_num = turn_num
        request.state.trace_is_first_turn = is_first_turn
        request.state.trace_root_span = root_span

        # Set API key hash and user info on request state (set by auth middleware later)
        # These will be used by messages.py to set on spans

        try:
            response = await call_next(request)
            return response
        except Exception:
            raise

