"""
Streaming span accumulator for SSE responses.

Wraps an SSE async generator to accumulate token usage and set span attributes
when the stream completes. Supports turn-based tracing with tool span creation.
"""
import json
import logging
from typing import Any, AsyncGenerator, List, Optional, Tuple

from opentelemetry.trace import Span, StatusCode, Tracer

from app.tracing.attributes import (
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    PROXY_USAGE_CACHE_READ_TOKENS,
    PROXY_USAGE_CACHE_WRITE_TOKENS,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_USAGE_DETAILS,
    LANGFUSE_TRACE_OUTPUT,
)
from app.tracing.spans import start_tool_span

logger = logging.getLogger(__name__)


class StreamingSpanAccumulator:
    """Wraps an SSE async generator to accumulate metrics and finalize spans."""

    def __init__(
        self,
        span: Optional[Span],
        request_data: Any,
        request_id: str,
        trace_content: bool = False,
        turn_span: Optional[Span] = None,
        turn_ctx=None,
        root_span: Optional[Span] = None,
        tracer: Optional[Tracer] = None,
    ):
        self.span = span
        self.request_data = request_data
        self.request_id = request_id
        self.trace_content = trace_content
        self.turn_span = turn_span
        self.turn_ctx = turn_ctx
        self.root_span = root_span
        self.tracer = tracer

        # Accumulators
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.stop_reason: Optional[str] = None
        self.response_id: Optional[str] = None
        self.accumulated_text: list = []

        # Tool use accumulator: list of (name, id, input_json_str)
        self.tool_use_blocks: List[Tuple[str, str, str]] = []
        self._current_tool_name: Optional[str] = None
        self._current_tool_id: Optional[str] = None
        self._current_tool_input_parts: list = []

    async def wrap_stream(
        self, sse_generator: AsyncGenerator[str, None]
    ) -> AsyncGenerator[str, None]:
        """
        Wrap an SSE generator, accumulating metrics and finalizing span on completion.

        Yields events unchanged (zero-copy pass-through).
        """
        try:
            async for sse_event in sse_generator:
                # Parse and accumulate metrics from the event
                self._accumulate_from_event(sse_event)
                # Yield event unchanged
                yield sse_event

        except Exception as e:
            if self.span:
                self.span.set_status(StatusCode.ERROR, str(e))
                self.span.record_exception(e)
            raise

        finally:
            self._finalize_span()

    def _accumulate_from_event(self, sse_event: str) -> None:
        """Parse SSE event and accumulate metrics."""
        if "data:" not in sse_event:
            return

        try:
            event_type, event_data = self._parse_sse_event(sse_event)
            if event_data is None:
                return

            if event_type == "message_start":
                message = event_data.get("message", {})
                self.response_id = message.get("id")
                usage = message.get("usage", {})
                self.input_tokens = usage.get("input_tokens", 0)
                self.cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                self.cache_write_tokens = usage.get("cache_creation_input_tokens", 0)

            elif event_type == "message_delta":
                delta = event_data.get("delta", {})
                self.stop_reason = delta.get("stop_reason", self.stop_reason)
                usage = event_data.get("usage", {})
                if "output_tokens" in usage:
                    self.output_tokens = usage["output_tokens"]
                if "input_tokens" in usage:
                    self.input_tokens = usage["input_tokens"]
                if "cache_read_input_tokens" in usage:
                    self.cache_read_tokens = usage["cache_read_input_tokens"]
                if "cache_creation_input_tokens" in usage:
                    self.cache_write_tokens = usage["cache_creation_input_tokens"]

            elif event_type == "content_block_start":
                content_block = event_data.get("content_block", {})
                block_type = content_block.get("type")
                if block_type == "tool_use":
                    self._current_tool_name = content_block.get("name", "unknown")
                    self._current_tool_id = content_block.get("id", "")
                    self._current_tool_input_parts = []

            elif event_type == "content_block_delta":
                delta = event_data.get("delta", {})
                delta_type = delta.get("type")
                if delta_type == "text_delta" and self.trace_content:
                    text = delta.get("text", "")
                    if text:
                        self.accumulated_text.append(text)
                elif delta_type == "input_json_delta" and self._current_tool_name:
                    partial = delta.get("partial_json", "")
                    if partial:
                        self._current_tool_input_parts.append(partial)

            elif event_type == "content_block_stop":
                if self._current_tool_name:
                    tool_input_str = "".join(self._current_tool_input_parts)
                    self.tool_use_blocks.append((
                        self._current_tool_name,
                        self._current_tool_id or "",
                        tool_input_str,
                    ))
                    self._current_tool_name = None
                    self._current_tool_id = None
                    self._current_tool_input_parts = []

        except Exception:
            pass  # Don't fail on parse errors

    def _parse_sse_event(self, sse_event: str):
        """Parse SSE event string to extract event type and JSON data."""
        event_type = None
        event_data = None

        for line in sse_event.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    event_data = json.loads(line[5:].strip())
                except (json.JSONDecodeError, ValueError):
                    pass

        return event_type, event_data

    def _finalize_span(self) -> None:
        """Set accumulated attributes on span and end it, then handle Turn span."""
        # Finalize the gen_ai.chat span
        if self.span is not None:
            try:
                if self.response_id:
                    self.span.set_attribute(GEN_AI_RESPONSE_ID, self.response_id)
                if self.input_tokens:
                    self.span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, self.input_tokens)
                if self.output_tokens:
                    self.span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, self.output_tokens)
                if self.stop_reason:
                    self.span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [self.stop_reason])
                if self.cache_read_tokens:
                    self.span.set_attribute(PROXY_USAGE_CACHE_READ_TOKENS, self.cache_read_tokens)
                if self.cache_write_tokens:
                    self.span.set_attribute(PROXY_USAGE_CACHE_WRITE_TOKENS, self.cache_write_tokens)

                # Set Langfuse usage_details JSON with all token fields including cache
                usage_details = {
                    "input": self.input_tokens or 0,
                    "output": self.output_tokens or 0,
                }
                if self.cache_read_tokens:
                    usage_details["cache_read_input_tokens"] = self.cache_read_tokens
                if self.cache_write_tokens:
                    usage_details["cache_creation_input_tokens"] = self.cache_write_tokens
                self.span.set_attribute(LANGFUSE_OBSERVATION_USAGE_DETAILS, json.dumps(usage_details))

                # Add completion content if tracing content
                if self.trace_content and self.accumulated_text:
                    full_text = "".join(self.accumulated_text)
                    self.span.set_attribute("gen_ai.completion", full_text)
                    self.span.add_event("gen_ai.content.completion", {"gen_ai.completion": full_text})

            except Exception as e:
                logger.debug(f"Error finalizing streaming span: {e}")

            finally:
                try:
                    self.span.end()
                except Exception:
                    pass

        # Create tool spans as children of Turn span
        if self.turn_ctx and self.tracer and self.tool_use_blocks:
            try:
                for tool_name, tool_id, tool_input_str in self.tool_use_blocks:
                    tool_span = start_tool_span(self.tracer, tool_name, tool_id, context=self.turn_ctx)
                    if tool_span:
                        if self.trace_content and tool_input_str:
                            tool_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, tool_input_str)
                        tool_span.end()
            except Exception as e:
                logger.debug(f"Error creating tool spans: {e}")

        # Build response text for Turn output
        response_text = None
        if self.trace_content:
            parts = []
            if self.accumulated_text:
                parts.append("".join(self.accumulated_text))
            for tool_name, tool_id, tool_input_str in self.tool_use_blocks:
                parts.append(f"[tool_use: {tool_name}({tool_input_str})]")
            if parts:
                response_text = "\n".join(parts)

        # Set Turn span output and trace output, then end Turn
        if self.turn_span is not None:
            try:
                if self.trace_content and response_text:
                    self.turn_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, response_text)
                    # Set trace-level output on Turn span (exported immediately)
                    self.turn_span.set_attribute(LANGFUSE_TRACE_OUTPUT, response_text)
            except Exception:
                pass
            try:
                self.turn_span.end()
            except Exception:
                pass

        # Also set on root span for when it eventually exports
        if self.root_span is not None and self.trace_content and response_text:
            try:
                self.root_span.set_attribute(LANGFUSE_TRACE_OUTPUT, response_text)
            except Exception:
                pass
