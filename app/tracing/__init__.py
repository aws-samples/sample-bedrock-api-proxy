"""
OpenTelemetry tracing for LLM observability.

When tracing is disabled (enable_tracing=False), all functions are no-ops with zero overhead.
"""
from app.core.config import settings

if settings.enable_tracing:
    from app.tracing.provider import init_tracing, shutdown_tracing, get_tracer
    from app.tracing.context import get_session_id, propagate_context_to_thread, attach_context_in_thread, detach_context_in_thread
else:
    # No-op implementations for zero overhead when tracing is disabled
    def init_tracing():
        pass

    def shutdown_tracing():
        pass

    def get_tracer(name: str = ""):
        return None

    def get_session_id(request=None, request_data=None):
        return None

    def propagate_context_to_thread():
        return None

    def attach_context_in_thread(token=None):
        return None

    def detach_context_in_thread(token=None):
        pass
