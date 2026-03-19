"""
Session ID extraction and OTEL context propagation for threaded execution.
"""
import hashlib
from typing import Any, Optional

from opentelemetry import context as otel_context


def get_session_id(request: Any = None, request_data: Any = None) -> Optional[str]:
    """
    Extract session ID from request sources.

    Priority:
      1. x-session-id header (explicit)
      2. metadata.session_id (explicit)
      3. PTC container ID
      4. Auto-derived from first user message + model (for agent loop grouping)
    """
    # Try x-session-id header
    if request is not None:
        session_id = None
        if hasattr(request, "headers"):
            session_id = request.headers.get("x-session-id")
        if session_id:
            return session_id

    # Try metadata.session_id from request body
    if request_data is not None:
        metadata = getattr(request_data, "metadata", None)
        if metadata is not None:
            session_id = None
            if isinstance(metadata, dict):
                session_id = metadata.get("session_id")
            elif hasattr(metadata, "session_id"):
                session_id = getattr(metadata, "session_id", None)
            if session_id:
                return session_id

        # Try PTC container field
        container = getattr(request_data, "container", None)
        if container:
            if isinstance(container, str):
                return container
            elif hasattr(container, "id"):
                return container.id

        # Auto-derive session ID from first user message + model
        # This groups agent loop requests (same first user msg) into one session
        return _derive_session_id(request_data)

    return None


def _derive_session_id(request_data: Any) -> Optional[str]:
    """Derive a stable session ID by hashing the first user message + model."""
    try:
        messages = getattr(request_data, "messages", None)
        model = getattr(request_data, "model", "") or ""
        if not messages:
            return None

        # Find the first user message
        first_user_text = None
        for msg in messages:
            role = getattr(msg, "role", None) if hasattr(msg, "role") else msg.get("role") if isinstance(msg, dict) else None
            if role != "user":
                continue
            content = getattr(msg, "content", "") if hasattr(msg, "content") else msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(content, str) and content:
                first_user_text = content
                break
            elif isinstance(content, list):
                for block in content:
                    btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                    if btype == "text":
                        text = getattr(block, "text", "") if hasattr(block, "text") else block.get("text", "")
                        if text:
                            first_user_text = text
                            break
                if first_user_text:
                    break

        if not first_user_text:
            return None

        # Hash first user message + model for a stable session ID
        key = f"{model}:{first_user_text}"
        return f"auto-{hashlib.sha256(key.encode()).hexdigest()[:16]}"
    except Exception:
        return None


def propagate_context_to_thread():
    """
    Capture current OTEL context for propagation to a worker thread.

    Call this in the async context BEFORE submitting work to ThreadPoolExecutor.
    Returns a context token that should be passed to attach_context_in_thread().
    """
    return otel_context.get_current()


def attach_context_in_thread(parent_context):
    """
    Attach OTEL context in a worker thread.

    Call this at the START of the thread worker function.
    Returns a token that must be passed to detach_context_in_thread().
    """
    if parent_context is None:
        return None
    return otel_context.attach(parent_context)


def detach_context_in_thread(token):
    """
    Detach OTEL context in a worker thread.

    Call this at the END of the thread worker function (in finally block).
    """
    if token is not None:
        otel_context.detach(token)
