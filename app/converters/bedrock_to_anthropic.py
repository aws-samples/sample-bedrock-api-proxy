"""
Converter from AWS Bedrock Converse API format to Anthropic Messages API format.

Handles conversion of responses, including content blocks, tool use,
usage statistics, and streaming events.
"""
import base64
import json
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from app.schemas.anthropic import (
    ContentBlock,
    ImageContent,
    ImageSource,
    MessageResponse,
    RedactedThinkingContent,
    TextContent,
    ThinkingContent,
    ToolUseContent,
    ServerToolUseContent,
    Usage,
)


class BedrockToAnthropicConverter:
    """Converts Bedrock API format to Anthropic API format."""

    def __init__(self):
        """Initialize converter."""
        pass

    def convert_response(
        self,
        bedrock_response: Dict[str, Any],
        model: str,
        request_id: Optional[str] = None,
    ) -> MessageResponse:
        """
        Convert Bedrock Converse response to Anthropic MessageResponse format.

        Args:
            bedrock_response: Response from Bedrock Converse API
            model: Original model ID from request
            request_id: Optional request ID

        Returns:
            MessageResponse object in Anthropic format
        """
        print(f"\n[CONVERTER] Converting Bedrock response to Anthropic format")
        print(f"[CONVERTER] Request ID: {request_id}")

        # Extract output message
        output = bedrock_response.get("output", {})
        message_data = output.get("message", {})

        print(f"[CONVERTER] Bedrock raw content blocks:")
        bedrock_content = message_data.get("content", [])
        for i, block in enumerate(bedrock_content):
            block_type = list(block.keys())[0] if block else "empty"
            if block_type == "text":
                text_preview = block["text"][:100] if block["text"] else "(empty)"
                print(f"  [{i}] text: {text_preview}")
            elif block_type == "toolUse":
                tool_name = block["toolUse"].get("name", "unknown")
                print(f"  [{i}] toolUse: {tool_name}")
            else:
                print(f"  [{i}] {block_type}")

        # Convert content blocks
        content = self._convert_content_blocks(bedrock_content)

        print(f"[CONVERTER] Anthropic converted content blocks:")
        for i, block in enumerate(content):
            if hasattr(block, 'type'):
                if block.type == "text":
                    text_preview = block.text[:100] if block.text else "(empty)"
                    print(f"  [{i}] text: {text_preview}")
                elif block.type == "tool_use":
                    print(f"  [{i}] tool_use: {block.name}")
                else:
                    print(f"  [{i}] {block.type}")

        # Convert usage
        usage = self._convert_usage(bedrock_response.get("usage", {}))

        # Convert stop reason
        stop_reason = self._convert_stop_reason(bedrock_response.get("stopReason"))

        print(f"[CONVERTER] Stop reason: {bedrock_response.get('stopReason')} -> {stop_reason}")
        print(f"[CONVERTER] Usage: input={usage.input_tokens}, output={usage.output_tokens}")
        print(f"[CONVERTER] Final content blocks count: {len(content)}\n")

        # Generate message ID if not provided
        message_id = request_id or f"msg_{uuid4().hex}"

        return MessageResponse(
            id=message_id,
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason=stop_reason,
            stop_sequence=None,  # Bedrock doesn't return the actual stop sequence
            usage=usage,
        )

    def _convert_content_blocks(
        self, bedrock_content: List[Dict[str, Any]]
    ) -> List[ContentBlock]:
        """
        Convert Bedrock content blocks to Anthropic format.

        Args:
            bedrock_content: List of content blocks from Bedrock

        Returns:
            List of ContentBlock objects in Anthropic format

        Note:
            When stop_reason is "tool_use", Bedrock often returns content like:
            [{"text": ""}, {"toolUse": {...}}]
            We filter out empty text blocks to match Anthropic's API behavior.
        """
        anthropic_content = []

        for block in bedrock_content:
            if "text" in block:
                # Skip empty text blocks (common when stop_reason is tool_use)
                # Bedrock sometimes returns an empty text block before tool_use
                text = block["text"]
                if text:  # Only add non-empty text blocks
                    anthropic_content.append(TextContent(type="text", text=text))

            elif "image" in block:
                # Convert image bytes to base64
                image_data = block["image"]
                image_bytes = image_data.get("source", {}).get("bytes", b"")
                image_format = image_data.get("format", "png")

                # Map format to media type
                media_type_map = {
                    "png": "image/png",
                    "jpeg": "image/jpeg",
                    "jpg": "image/jpeg",
                    "gif": "image/gif",
                    "webp": "image/webp",
                }
                media_type = media_type_map.get(image_format.lower(), "image/png")

                image_base64 = base64.b64encode(image_bytes).decode("utf-8")

                anthropic_content.append(
                    ImageContent(
                        type="image",
                        source=ImageSource(
                            type="base64",
                            media_type=media_type,
                            data=image_base64,
                        ),
                    )
                )

            elif "toolUse" in block:
                tool_use_data = block["toolUse"]
                anthropic_content.append(
                    ToolUseContent(
                        type="tool_use",
                        id=tool_use_data.get("toolUseId", f"toolu_{uuid4().hex}"),
                        name=tool_use_data.get("name", ""),
                        input=tool_use_data.get("input", {}),
                    )
                )

            elif "reasoningContent" in block:
                # Handle Bedrock reasoning/thinking content blocks
                reasoning_data = block["reasoningContent"]

                if "redactedContent" in reasoning_data:
                    # Redacted thinking block
                    anthropic_content.append(
                        RedactedThinkingContent(
                            type="redacted_thinking",
                            data=reasoning_data["redactedContent"],
                        )
                    )
                elif "reasoningText" in reasoning_data:
                    # Normal thinking block
                    reasoning_text = reasoning_data["reasoningText"]
                    thinking_text = reasoning_text.get("text", "")
                    signature = reasoning_text.get("signature")

                    if thinking_text:  # Only add non-empty thinking blocks
                        anthropic_content.append(
                            ThinkingContent(
                                type="thinking",
                                thinking=thinking_text,
                                signature=signature,
                            )
                        )

        return anthropic_content

    def _convert_usage(self, bedrock_usage: Dict[str, Any]) -> Usage:
        """
        Convert Bedrock usage statistics to Anthropic format.

        Args:
            bedrock_usage: Usage data from Bedrock

        Returns:
            Usage object in Anthropic format
        """
        return Usage(
            input_tokens=bedrock_usage.get("inputTokens", 0),
            output_tokens=bedrock_usage.get("outputTokens", 0),
            cache_creation_input_tokens=None,  # Will be set from cache metrics if available
            cache_read_input_tokens=None,
        )

    def _convert_stop_reason(self, bedrock_stop_reason: Optional[str]) -> Optional[str]:
        """
        Convert Bedrock stop reason to Anthropic format.

        Args:
            bedrock_stop_reason: Stop reason from Bedrock

        Returns:
            Stop reason in Anthropic format
        """
        if not bedrock_stop_reason:
            return None

        # Map Bedrock stop reasons to Anthropic stop reasons
        stop_reason_map = {
            "end_turn": "end_turn",
            "max_tokens": "max_tokens",
            "stop_sequence": "stop_sequence",
            "tool_use": "tool_use",
            "content_filtered": "end_turn",  # Map content_filtered to end_turn
            "complete": "end_turn",
        }

        return stop_reason_map.get(bedrock_stop_reason.lower(), "end_turn")

    def convert_stream_event(
        self,
        bedrock_event: Dict[str, Any],
        model: str,
        message_id: str,
        current_index: int,
    ) -> List[Dict[str, Any]]:
        """
        Convert Bedrock stream event to Anthropic stream events.

        Args:
            bedrock_event: Event from Bedrock ConverseStream
            model: Original model ID from request
            message_id: Message ID for this conversation
            current_index: Current content block index

        Returns:
            List of Anthropic stream events (may be multiple events per Bedrock event)
        """
        events = []

        # Debug log the event type
        # event_type = list(bedrock_event.keys())[0] if bedrock_event else "unknown"
        # if event_type in ["contentBlockStart", "contentBlockDelta", "contentBlockStop"]:
        #     print(f"[CONVERTER STREAM] Event: {event_type}")

        # messageStart event
        if "messageStart" in bedrock_event:
            events.append(
                {
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
            )

        # contentBlockStart event
        elif "contentBlockStart" in bedrock_event:
            start_data = bedrock_event["contentBlockStart"]
            start_block = start_data.get("start", {})
            index = start_data.get("contentBlockIndex", current_index)

            if "toolUse" in start_block:
                tool_use = start_block["toolUse"]
                tool_name = tool_use.get("name", "")
                # print(f"[CONVERTER STREAM]   -> Starting tool_use block [{index}]: {tool_name}")
                events.append(
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_use.get("toolUseId", f"toolu_{uuid4().hex}"),
                            "name": tool_name,
                        },
                    }
                )
            elif "reasoningContent" in start_block:
                # Thinking block start
                reasoning_content = start_block["reasoningContent"]
                if "redactedContent" in reasoning_content:
                    # Redacted thinking block
                    events.append(
                        {
                            "type": "content_block_start",
                            "index": index,
                            "content_block": {"type": "redacted_thinking", "data": ""},
                        }
                    )
                else:
                    # Normal thinking block
                    events.append(
                        {
                            "type": "content_block_start",
                            "index": index,
                            "content_block": {"type": "thinking", "thinking": ""},
                        }
                    )
            else:
                # Text block start
                # print(f"[CONVERTER STREAM]   -> Starting text block [{index}]")
                events.append(
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "text", "text": ""},
                    }
                )

        # contentBlockDelta event
        elif "contentBlockDelta" in bedrock_event:
            delta_data = bedrock_event["contentBlockDelta"]
            delta = delta_data.get("delta", {})
            index = delta_data.get("contentBlockIndex", current_index)

            # Handle reasoning content (thinking models output)
            if "reasoningContent" in delta:
                # Extract text from reasoningContent (it's a dict with "text" key)
                reasoning_text = delta["reasoningContent"]
                if isinstance(reasoning_text, dict):
                    reasoning_text = reasoning_text.get("text", "")

                # text_preview = reasoning_text[:50] if reasoning_text else ""
                # print(f"[CONVERTER STREAM]   -> thinking_delta [{index}]: {text_preview}...")

                # Convert Bedrock reasoningContent to Anthropic thinking_delta
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "thinking_delta",
                            "thinking": reasoning_text,
                        },
                    }
                )
            elif "text" in delta:
                # text_preview = delta["text"][:50] if delta["text"] else ""
                # print(f"[CONVERTER STREAM]   -> text_delta [{index}]: {text_preview}...")
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": delta["text"]},
                    }
                )
            elif "toolUse" in delta:
                tool_use_delta = delta["toolUse"]
                # input_preview = tool_use_delta.get("input", "")[:50]
                # print(f"[CONVERTER STREAM]   -> input_json_delta [{index}]: {input_preview}...")
                # Tool input comes as JSON string that needs to be accumulated
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": tool_use_delta.get("input", ""),
                        },
                    }
                )

        # contentBlockStop event
        elif "contentBlockStop" in bedrock_event:
            stop_data = bedrock_event["contentBlockStop"]
            index = stop_data.get("contentBlockIndex", current_index)

            # print(f"[CONVERTER STREAM]   -> Stopping content block [{index}]")
            events.append({"type": "content_block_stop", "index": index})

        # messageStop event
        elif "messageStop" in bedrock_event:
            stop_data = bedrock_event["messageStop"]
            stop_reason = self._convert_stop_reason(stop_data.get("stopReason"))

            # print(f"[CONVERTER STREAM] Message stop - reason: {stop_data.get('stopReason')} -> {stop_reason}")

            # First send message_delta with stop_reason
            events.append(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": 0},  # Will be updated from metadata
                }
            )

            # Then send message_stop
            events.append({"type": "message_stop"})

        # metadata event (contains usage information)
        elif "metadata" in bedrock_event:
            metadata = bedrock_event["metadata"]
            usage = metadata.get("usage", {})

            # Update the usage in message_start event or send as part of final events
            # This is typically the last event, so we'll store it for final processing
            # For now, we'll attach it to a message_delta event if one exists
            pass

        return events

    def create_error_event(self, error_code: str, error_message: str) -> Dict[str, Any]:
        """
        Create an Anthropic-formatted error event for streaming.

        Maps Bedrock error codes to Anthropic error types.

        Args:
            error_code: Bedrock error code (e.g., 'ThrottlingException')
            error_message: Error message

        Returns:
            Error event dictionary
        """
        # Map Bedrock error codes to Anthropic error types
        error_type_mapping = {
            "ThrottlingException": "rate_limit_error",
            "TooManyRequestsException": "rate_limit_error",
            "ServiceQuotaExceededException": "rate_limit_error",
            "ServiceUnavailableException": "api_error",
            "ModelNotReadyException": "api_error",
            "ResourceNotFoundException": "not_found_error",
            "ValidationException": "invalid_request_error",
            "AccessDeniedException": "permission_error",
            "internal_error": "api_error",
            "no_stream": "api_error",
        }

        anthropic_error_type = error_type_mapping.get(error_code, "api_error")

        return {
            "type": "error",
            "error": {
                "type": anthropic_error_type,
                "message": error_message,
            },
        }

    def create_ping_event(self) -> Dict[str, Any]:
        """
        Create a ping event for keep-alive.

        Returns:
            Ping event dictionary
        """
        return {"type": "ping"}

    def merge_usage_into_events(
        self, events: List[Dict[str, Any]], usage: Dict[str, int]
    ) -> List[Dict[str, Any]]:
        """
        Merge usage information into stream events.

        Args:
            events: List of stream events
            usage: Usage dictionary with token counts

        Returns:
            Updated events list with usage information
        """
        # Update message_start event with input tokens
        for event in events:
            if event.get("type") == "message_start":
                event["message"]["usage"]["input_tokens"] = usage.get(
                    "inputTokens", 0
                )

            # Update message_delta event with output tokens
            elif event.get("type") == "message_delta":
                event["usage"]["output_tokens"] = usage.get("outputTokens", 0)

        return events
