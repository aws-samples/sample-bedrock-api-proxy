"""
Converter for OpenAI Chat Completions API response format to Anthropic Messages API format.

Translates OpenAI response structures into Anthropic-compatible responses,
enabling non-Claude models accessed via Bedrock Mantle to return responses
in the Anthropic Messages API format expected by clients.
"""
import json
import logging
from typing import Any, Dict

from app.schemas.anthropic import (
    MessageResponse,
    TextContent,
    ThinkingContent,
    ToolUseContent,
    Usage,
)

logger = logging.getLogger(__name__)


class OpenAIToAnthropicConverter:
    """Converts OpenAI Chat Completions API responses to Anthropic Messages API format."""

    STOP_REASON_MAP: Dict[str, str] = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }

    ERROR_TYPE_MAP: Dict[str, str] = {
        "400": "invalid_request_error",
        "401": "authentication_error",
        "403": "permission_error",
        "404": "not_found_error",
        "429": "rate_limit_error",
        "500": "api_error",
        "529": "overloaded_error",
    }

    def convert_response(
        self, openai_response: Dict[str, Any], model: str, message_id: str
    ) -> MessageResponse:
        """Convert an OpenAI Chat Completions response to an Anthropic MessageResponse.

        Args:
            openai_response: The OpenAI-format response dict.
            model: The model identifier to use in the Anthropic response.
            message_id: The message ID to use in the Anthropic response.

        Returns:
            A MessageResponse in Anthropic format.
        """
        choice = openai_response.get("choices", [{}])[0]
        message = choice.get("message", {})

        # Build content blocks
        content = []

        # Reasoning/thinking content (from models with reasoning_effort)
        # Bedrock Mantle returns this as "reasoning" (not "reasoning_content")
        reasoning = message.get("reasoning") or message.get("reasoning_content")
        if reasoning:
            content.append(ThinkingContent(type="thinking", thinking=reasoning))

        # Text content
        text = message.get("content")
        if text:
            content.append(TextContent(type="text", text=text))

        # Tool calls
        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                arguments_str = func.get("arguments", "{}")
                try:
                    arguments = json.loads(arguments_str)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                content.append(
                    ToolUseContent(
                        type="tool_use",
                        id=tc.get("id", ""),
                        name=func.get("name", ""),
                        input=arguments,
                    )
                )

        # If no content at all, add empty text block
        if not content:
            content.append(TextContent(type="text", text=""))

        # Map stop reason
        finish_reason = choice.get("finish_reason", "stop")
        stop_reason = self.STOP_REASON_MAP.get(finish_reason, "end_turn")  # type: ignore[arg-type]

        # Map usage
        openai_usage = openai_response.get("usage", {})
        usage = Usage(
            input_tokens=openai_usage.get("prompt_tokens", 0),
            output_tokens=openai_usage.get("completion_tokens", 0),
        )

        return MessageResponse(
            id=message_id,
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason=stop_reason,
            stop_sequence=None,
            usage=usage,
        )

    def create_message_start_event(
        self, message_id: str, model: str
    ) -> Dict[str, Any]:
        """Create a message_start SSE event for streaming.

        Args:
            message_id: The message ID.
            model: The model identifier.

        Returns:
            A message_start event dict.
        """
        return {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }

    def create_message_stop_event(self) -> Dict[str, Any]:
        """Create a message_stop SSE event for streaming.

        Returns:
            A message_stop event dict.
        """
        return {"type": "message_stop"}

    def create_error_event(
        self, error_code: str, error_message: str
    ) -> Dict[str, Any]:
        """Create an error SSE event.

        Args:
            error_code: The HTTP error code as a string.
            error_message: The error message.

        Returns:
            An error event dict in Anthropic format.
        """
        error_type = self.ERROR_TYPE_MAP.get(error_code, "api_error")
        return {
            "type": "error",
            "error": {"type": error_type, "message": error_message},
        }
