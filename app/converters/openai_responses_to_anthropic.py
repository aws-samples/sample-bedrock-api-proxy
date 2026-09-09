"""
Converter for OpenAI Responses API response format to Anthropic Messages API format.

Used by the proxy's web-search agentic loop: after calling an upstream model via
the Responses API, the raw Responses dict is converted back to an Anthropic
``MessageResponse`` so the existing loop (which inspects ``response.content`` for
``tool_use`` blocks and accumulates text) keeps working unchanged.
"""

import json
import logging
from typing import Any
from uuid import uuid4

from app.core.exceptions import BedrockAPIError
from app.schemas.anthropic import (
    ContentBlock,
    MessageResponse,
    TextContent,
    ThinkingContent,
    ToolUseContent,
    Usage,
)

logger = logging.getLogger(__name__)


class OpenAIResponsesToAnthropicConverter:
    """Converts OpenAI Responses API responses to Anthropic Messages API format."""

    def convert_response(self, resp: dict[str, Any], model: str) -> MessageResponse:
        """Convert an OpenAI Responses API response dict to an Anthropic MessageResponse.

        Args:
            resp: The OpenAI Responses API response dict.
            model: The model identifier to use in the Anthropic response
                (the original request model).

        Returns:
            A MessageResponse in Anthropic format.
        """
        self.validate_response(resp)
        content: list[ContentBlock] = []
        has_tool_call = False

        for item in resp.get("output", []) or []:
            item_type = item.get("type")

            if item_type == "reasoning":
                for summary in item.get("summary") or []:
                    if summary.get("type") == "summary_text" and summary.get("text"):
                        content.append(ThinkingContent(thinking=summary["text"]))
                continue

            if item_type == "function_call":
                has_tool_call = True
                arguments_str = item.get("arguments")
                try:
                    arguments = json.loads(arguments_str)
                    if not isinstance(arguments, dict):
                        arguments = {}
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                content.append(
                    ToolUseContent(
                        type="tool_use",
                        # Continuation requires call_id (not the fc_ id).
                        id=item.get("call_id", ""),
                        name=item.get("name", ""),
                        input=arguments,
                    )
                )
                continue

            if item_type == "message":
                for entry in item.get("content", []) or []:
                    entry_type = entry.get("type")
                    if entry_type == "output_text":
                        content.append(
                            TextContent(type="text", text=entry.get("text", ""))
                        )
                    elif entry_type == "refusal":
                        content.append(TextContent(text=entry.get("refusal", "")))
                    else:
                        logger.debug(
                            "Skipping unrecognized message content entry type: %s",
                            entry_type,
                        )
                continue

            logger.debug("Skipping unrecognized output item type: %s", item_type)

        # An Anthropic MessageResponse with empty content can stall the agentic
        # loop; mirror the Chat-Completions converter and emit an empty text block.
        if not content:
            content.append(TextContent(type="text", text=""))

        stop_reason = self.stop_reason(resp, has_tool_call)
        usage = self.convert_usage(resp.get("usage") or {})
        response_id = resp.get("id") or f"msg_{uuid4().hex[:24]}"

        return MessageResponse(
            id=response_id,
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason=stop_reason,  # type: ignore[arg-type]
            stop_sequence=None,
            usage=usage,
        )

    @staticmethod
    def validate_response(resp: dict[str, Any]) -> None:
        """Reject failed/nonterminal envelopes, including HTTP-200 API errors."""
        status = resp.get("status")
        error = resp.get("error")
        if (
            error
            or status not in (None, "completed", "incomplete")
            or not isinstance(resp.get("output"), list)
        ):
            details = error if isinstance(error, dict) else resp
            raise BedrockAPIError(
                error_code=details.get("code")
                or details.get("__type")
                or status
                or "api_error",
                error_message=details.get("message")
                or f"Responses API returned {status or 'an invalid response'}",
            )

    @staticmethod
    def stop_reason(resp: dict[str, Any], has_tool_call: bool) -> str:
        """Apply truncation before tool-use termination to avoid retry loops."""
        if resp.get("status") == "incomplete":
            reason = (resp.get("incomplete_details") or {}).get("reason")
            if reason == "max_output_tokens":
                return "max_tokens"
            if reason == "content_filter":
                return "refusal"
            raise BedrockAPIError(
                error_code="incomplete_response",
                error_message=f"Responses API returned incomplete output: {reason}",
            )
        return "tool_use" if has_tool_call else "end_turn"

    @staticmethod
    def convert_usage(usage_data: dict[str, Any]) -> Usage:
        """Normalize cache-inclusive Responses input to Anthropic accounting.

        Anthropic input_tokens excludes both cache reads and writes; its output
        token count includes reasoning. Use this for both sync and streaming.
        """
        input_details = usage_data.get("input_tokens_details") or {}
        cache_read = input_details.get("cached_tokens")
        cache_write = input_details.get("cache_write_tokens")
        output_details = usage_data.get("output_tokens_details") or {}
        reasoning_tokens = output_details.get("reasoning_tokens")
        return Usage(
            input_tokens=max(
                (usage_data.get("input_tokens", 0) or 0)
                - (cache_read or 0)
                - (cache_write or 0),
                0,
            ),
            output_tokens=usage_data.get("output_tokens", 0) or 0,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
            reasoning_tokens=(
                int(reasoning_tokens)
                if isinstance(reasoning_tokens, (int, float))
                and not isinstance(reasoning_tokens, bool)
                else None
            ),
        )
