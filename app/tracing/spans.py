"""
Span creation and attribute-setting helpers for tracing.
"""
import json
import logging
from typing import Any, Optional

from opentelemetry.trace import Span, StatusCode, Tracer

from app.core.config import settings
from app.tracing.attributes import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_SYSTEM,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_MAX_OUTPUT_TOKENS,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_CALL_ID,
    LANGFUSE_SESSION_ID,
    PROXY_REQUEST_ID,
    PROXY_STREAM,
    PROXY_IS_PTC,
    PROXY_USAGE_CACHE_READ_TOKENS,
    PROXY_USAGE_CACHE_WRITE_TOKENS,
    PROXY_PTC_SESSION_ID,
    LANGFUSE_OBSERVATION_USAGE_DETAILS,
    SPAN_GEN_AI_CHAT,
    SPAN_GEN_AI_EXECUTE_TOOL,
    SPAN_PTC_CODE_EXECUTION,
    SPAN_TURN,
)

logger = logging.getLogger(__name__)


def start_turn_span(
    tracer: Optional[Tracer],
    turn_num: int,
    context=None,
) -> Optional[Span]:
    """Start a Turn span as a child of the root trace span."""
    if tracer is None:
        return None

    span = tracer.start_span(f"{SPAN_TURN} {turn_num}", context=context)
    return span


def _extract_current_turn_messages(messages) -> list:
    """Extract only the current turn's messages from the full history.

    In an agent loop, each request includes all previous messages.
    - Turn 1: [user] — just the initial user message
    - Turn N: [...history, assistant(tool_use), user(tool_result)] — we only want the last pair

    Returns the messages belonging to the current turn only.
    """
    if not messages:
        return []

    # Find the index of the last assistant message
    last_assistant_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        role = getattr(messages[i], "role", None) if hasattr(messages[i], "role") else messages[i].get("role") if isinstance(messages[i], dict) else None
        if role == "assistant":
            last_assistant_idx = i
            break

    if last_assistant_idx == -1:
        # No assistant message — this is the first turn, return all messages
        return list(messages)

    # Return from last assistant message onwards (assistant + user follow-up)
    return list(messages[last_assistant_idx:])


def start_llm_span(
    tracer: Optional[Tracer],
    request_data: Any,
    request_id: str,
    session_id: Optional[str] = None,
    stream: bool = False,
    is_ptc: bool = False,
    context=None,
) -> Optional[Span]:
    """Start a gen_ai.chat span for an LLM request."""
    if tracer is None:
        return None

    span = tracer.start_span(SPAN_GEN_AI_CHAT, context=context)

    span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
    span.set_attribute(GEN_AI_SYSTEM, "aws.bedrock")
    span.set_attribute(PROXY_REQUEST_ID, request_id)
    span.set_attribute(PROXY_STREAM, stream)
    span.set_attribute(PROXY_IS_PTC, is_ptc)

    if hasattr(request_data, "model") and request_data.model:
        span.set_attribute(GEN_AI_REQUEST_MODEL, request_data.model)

    if hasattr(request_data, "max_tokens") and request_data.max_tokens:
        span.set_attribute(GEN_AI_REQUEST_MAX_OUTPUT_TOKENS, request_data.max_tokens)

    if hasattr(request_data, "temperature") and request_data.temperature is not None:
        span.set_attribute(GEN_AI_REQUEST_TEMPERATURE, request_data.temperature)

    if hasattr(request_data, "top_p") and request_data.top_p is not None:
        span.set_attribute(GEN_AI_REQUEST_TOP_P, request_data.top_p)

    if session_id:
        span.set_attribute(GEN_AI_CONVERSATION_ID, session_id)
        span.set_attribute(LANGFUSE_SESSION_ID, session_id)

    # Optionally trace prompt content (PII opt-in)
    if settings.otel_trace_content:
        try:
            all_messages = getattr(request_data, "messages", [])
            messages = _extract_current_turn_messages(all_messages)
            if messages:
                prompt_summary = []
                for msg in messages:
                    role = getattr(msg, "role", "unknown") if hasattr(msg, "role") else msg.get("role", "unknown")
                    content = getattr(msg, "content", "") if hasattr(msg, "content") else msg.get("content", "")
                    if isinstance(content, str):
                        prompt_summary.append(f"{role}: {content}")
                    elif isinstance(content, list):
                        block_parts = []
                        for block in content:
                            btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                            if btype == "text":
                                text = getattr(block, "text", "") if hasattr(block, "text") else block.get("text", "")
                                if text:
                                    block_parts.append(text)
                            elif btype == "tool_use":
                                name = getattr(block, "name", "") if hasattr(block, "name") else block.get("name", "")
                                tool_input = getattr(block, "input", {}) if hasattr(block, "input") else block.get("input", {})
                                block_parts.append(f"[tool_use: {name}({str(tool_input)})]")
                            elif btype == "tool_result":
                                tid = getattr(block, "tool_use_id", "") if hasattr(block, "tool_use_id") else block.get("tool_use_id", "")
                                # Extract text from tool_result content
                                tc = getattr(block, "content", "") if hasattr(block, "content") else block.get("content", "")
                                if isinstance(tc, str):
                                    block_parts.append(f"[tool_result({tid}): {tc}]")
                                elif isinstance(tc, list):
                                    tr_texts = []
                                    for tb in tc:
                                        tb_type = getattr(tb, "type", None) or (tb.get("type") if isinstance(tb, dict) else None)
                                        if tb_type == "text":
                                            tr_texts.append(getattr(tb, "text", "") if hasattr(tb, "text") else tb.get("text", ""))
                                    block_parts.append(f"[tool_result({tid}): {' '.join(tr_texts)}]")
                                else:
                                    block_parts.append(f"[tool_result({tid})]")
                        if block_parts:
                            prompt_summary.append(f"{role}: {' '.join(block_parts)}")

                prompt_text = "\n".join(prompt_summary)
                # Set as span attribute (Langfuse maps this to observation Input)
                span.set_attribute("gen_ai.prompt", prompt_text)
                # Also add as event for detailed view
                span.add_event("gen_ai.content.prompt", {"gen_ai.prompt": prompt_text})
        except Exception:
            pass  # Don't fail on content tracing errors

    return span


def set_llm_response_attributes(span: Optional[Span], response: Any) -> None:
    """Set response attributes on an LLM span."""
    if span is None:
        return

    try:
        # Set response ID
        response_id = getattr(response, "id", None)
        if response_id:
            span.set_attribute(GEN_AI_RESPONSE_ID, response_id)

        # Set stop reason
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason:
            span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [stop_reason])

        # Set token usage
        usage = getattr(response, "usage", None)
        if usage:
            input_tokens = getattr(usage, "input_tokens", 0)
            output_tokens = getattr(usage, "output_tokens", 0)
            if input_tokens:
                span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
            if output_tokens:
                span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)

            # Cache tokens
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            if cache_read:
                span.set_attribute(PROXY_USAGE_CACHE_READ_TOKENS, cache_read)
            if cache_write:
                span.set_attribute(PROXY_USAGE_CACHE_WRITE_TOKENS, cache_write)

            # Set Langfuse usage_details JSON with all token fields including cache
            # This is the only way Langfuse picks up cache tokens via OTEL
            usage_details = {"input": input_tokens or 0, "output": output_tokens or 0}
            if cache_read:
                usage_details["cache_read_input_tokens"] = cache_read
            if cache_write:
                usage_details["cache_creation_input_tokens"] = cache_write
            span.set_attribute(LANGFUSE_OBSERVATION_USAGE_DETAILS, json.dumps(usage_details))

        # Optionally trace completion content
        if settings.otel_trace_content:
            content = getattr(response, "content", [])
            if content:
                output_parts = []
                for block in content:
                    btype = getattr(block, "type", None)
                    if btype == "text":
                        output_parts.append(getattr(block, "text", ""))
                    elif btype == "tool_use":
                        name = getattr(block, "name", "")
                        tool_input = getattr(block, "input", {})
                        output_parts.append(f"[tool_use: {name}({str(tool_input)})]")
                    elif btype == "thinking":
                        thinking_text = getattr(block, "thinking", "")
                        output_parts.append(f"[thinking: {thinking_text}]")
                if output_parts:
                    completion_text = "\n".join(output_parts)
                    span.set_attribute("gen_ai.completion", completion_text)
                    span.add_event("gen_ai.content.completion", {"gen_ai.completion": completion_text})

    except Exception as e:
        logger.debug(f"Error setting response attributes on span: {e}")


def start_tool_span(
    tracer: Optional[Tracer],
    tool_name: str,
    tool_use_id: str,
    context=None,
) -> Optional[Span]:
    """Start a gen_ai.execute_tool child span."""
    if tracer is None:
        return None

    span = tracer.start_span(f"{SPAN_GEN_AI_EXECUTE_TOOL} {tool_name}", context=context)
    span.set_attribute(GEN_AI_TOOL_NAME, tool_name)
    span.set_attribute(GEN_AI_TOOL_CALL_ID, tool_use_id)
    return span


def start_ptc_span(
    tracer: Optional[Tracer],
    session_id: str,
) -> Optional[Span]:
    """Start a ptc.code_execution child span."""
    if tracer is None:
        return None

    span = tracer.start_span(SPAN_PTC_CODE_EXECUTION)
    span.set_attribute(PROXY_PTC_SESSION_ID, session_id)
    return span


def set_error_on_span(span: Optional[Span], error: Exception) -> None:
    """Set error status and record exception on a span."""
    if span is None:
        return

    try:
        span.set_status(StatusCode.ERROR, str(error))
        span.record_exception(error)
    except Exception:
        pass  # Don't fail on tracing errors
