"""
Converter from Anthropic Messages API format to OpenAI Responses API format.

Renders an Anthropic ``MessageRequest`` (including conversation state threaded
as ``messages``) into a kwargs dict suitable for the OpenAI SDK
``client.responses.create(**kwargs)`` call.

Used by Messages requests and the proxy's server-side agentic loops to drive
Responses-API models. The proxy maintains conversation state itself, so the
request is always stateless (``store=False``) and the full conversation is
re-rendered into the ``input`` array on every call.
"""

import json
from typing import Any

from app.converters.anthropic_to_openai import AnthropicToOpenAIConverter
from app.converters.thinking import is_thinking_enabled
from app.schemas.anthropic import (
    Message,
    MessageRequest,
    SystemMessage,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)


class AnthropicToOpenAIResponsesConverter:
    """Converts Anthropic Messages API format to OpenAI Responses API format."""

    def convert_request(self, request: MessageRequest) -> dict[str, Any]:
        """Convert an Anthropic MessageRequest to OpenAI Responses API kwargs.

        Args:
            request: Anthropic MessageRequest object.

        Returns:
            Dictionary suitable for ``client.responses.create(**kwargs)``.
        """
        result: dict[str, Any] = {
            "model": request.model,
            # Proxy maintains conversation state itself; never persist server-side.
            "store": False,
            "max_output_tokens": request.max_tokens,
        }

        # System content → instructions
        if request.system:
            instructions = self._convert_system(request.system)
            if instructions:
                result["instructions"] = instructions

        # Conversation messages → input array
        input_items: list[dict[str, Any]] = []
        for msg in request.messages:
            input_items.extend(self._convert_message(msg))
        result["input"] = input_items

        # Tools
        if request.tools:
            result["tools"] = self._convert_tools(request.tools)

        # Tool choice (translate Anthropic shapes to Responses API shapes)
        if request.tool_choice is not None:
            result["tool_choice"] = self._convert_tool_choice(request.tool_choice)

        if isinstance(request.tool_choice, dict):
            disabled = request.tool_choice.get("disable_parallel_tool_use")
            if disabled in (True, "true"):
                result["parallel_tool_calls"] = False
            elif disabled in (False, "false"):
                result["parallel_tool_calls"] = True

        for field in ("temperature", "top_p", "stream"):
            value = getattr(request, field)
            if value is not None:
                result[field] = value

        if request.thinking is not None and is_thinking_enabled(request.thinking):
            result["reasoning"] = {
                "effort": AnthropicToOpenAIConverter()._convert_thinking_to_effort(
                    request.thinking
                )
            }
        elif (
            request.thinking is not None and request.thinking.get("type") == "disabled"
        ):
            result["reasoning"] = {"effort": "none"}
        # Responses has no stop/stop_sequences parameter. Do not forward it or
        # top_k. Omit the optional reasoning.summary parameter for broader model
        # compatibility, while preserving any summaries returned by the model.

        return result

    def _convert_system(self, system: Any) -> str:
        """Flatten the Anthropic system prompt into a plain string.

        ``system`` may be a string or a list of SystemMessage blocks. The
        field_validator on MessageRequest converts strings into a
        list-of-SystemMessage, so a list is the common case.
        """
        if isinstance(system, str):
            return system
        if isinstance(system, list):
            texts: list[str] = []
            for block in system:
                if isinstance(block, SystemMessage):
                    texts.append(block.text)
                elif isinstance(block, dict):
                    texts.append(block.get("text", ""))
            return "\n".join(texts)
        return ""

    def _convert_message(self, message: Message) -> list[dict[str, Any]]:
        """Convert a single Anthropic message into Responses input items.

        A plain-string message becomes a single role item. A message with
        content blocks may expand into multiple items (a coalesced text item
        plus function_call / function_call_output items).
        """
        role = message.role
        content = message.content

        if isinstance(content, str):
            return [{"role": role, "content": content}]

        if not isinstance(content, list):
            return [{"role": role, "content": str(content)}]

        items: list[dict[str, Any]] = []
        text_parts: list[str] = []
        content_parts: list[dict[str, Any]] = []

        def flush_text() -> None:
            if text_parts:
                content_parts.append(
                    {"type": "input_text", "text": "\n".join(text_parts)}
                )
                text_parts.clear()

        def flush_message() -> None:
            flush_text()
            if content_parts:
                # Retain the established compact shape for text-only replay.
                value: Any = list(content_parts)
                if len(value) == 1 and value[0]["type"] == "input_text":
                    value = value[0]["text"]
                items.append({"role": role, "content": value})
                content_parts.clear()

        for block in content:
            if isinstance(block, TextContent) or (
                isinstance(block, dict) and block.get("type") == "text"
            ):
                text = (
                    block.text
                    if isinstance(block, TextContent)
                    else block.get("text", "")
                )
                text_parts.append(text)

            elif isinstance(block, ToolUseContent) or (
                isinstance(block, dict) and block.get("type") == "tool_use"
            ):
                flush_message()
                if isinstance(block, ToolUseContent):
                    call_id = block.id
                    name = block.name
                    tool_input = block.input
                else:
                    call_id = block.get("id", "")
                    name = block.get("name", "")
                    tool_input = block.get("input", {})
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": json.dumps(tool_input),
                    }
                )

            elif isinstance(block, ToolResultContent) or (
                isinstance(block, dict) and block.get("type") == "tool_result"
            ):
                flush_message()
                items.append(self._convert_tool_result(block))

            else:
                converted = self._convert_media(block)
                if converted is not None:
                    flush_text()
                    content_parts.append(converted)
            # Anthropic thinking/signatures cannot be replayed as OpenAI
            # reasoning items without their original provider-specific state.

        flush_message()
        return items

    @staticmethod
    def _convert_media(block: Any) -> dict[str, Any] | None:
        """Convert the image/PDF source shapes accepted by Messages."""
        data = block.model_dump() if hasattr(block, "model_dump") else block
        if not isinstance(data, dict):
            return None
        source = data.get("source") or {}
        if data.get("type") == "image":
            if source.get("type") == "url":
                return {"type": "input_image", "image_url": source["url"]}
            if source.get("type", "base64") == "base64":
                return {
                    "type": "input_image",
                    "image_url": (
                        f"data:{source['media_type']};base64,{source['data']}"
                    ),
                }
        if data.get("type") == "document" and source.get("type", "base64") == "base64":
            return {
                "type": "input_file",
                "filename": "document.pdf",
                "file_data": f"data:{source['media_type']};base64,{source['data']}",
            }
        return None

    def _convert_tool_result(self, block: Any) -> dict[str, Any]:
        """Convert a tool_result block into a function_call_output item."""
        if isinstance(block, ToolResultContent):
            call_id = block.tool_use_id
            content = block.content
        else:
            call_id = block.get("tool_use_id", "")
            content = block.get("content", "")

        output: Any
        if isinstance(content, str):
            output = content
        elif isinstance(content, list):
            parts: list[str] = []
            rich_parts: list[dict[str, Any]] = []
            has_media = False
            for item in content:
                if isinstance(item, TextContent):
                    parts.append(item.text)
                    rich_parts.append({"type": "input_text", "text": item.text})
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                    rich_parts.append(
                        {"type": "input_text", "text": item.get("text", "")}
                    )
                else:
                    converted = self._convert_media(item)
                    if converted is not None:
                        has_media = True
                        rich_parts.append(converted)
            if has_media:
                output = rich_parts
            elif parts:
                output = "\n".join(parts)
            else:
                # Non-text content (e.g. images) — fall back to a JSON dump.
                output = self._dump_content(content)
        elif content is None:
            output = ""
        else:
            output = str(content)

        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        }

    @staticmethod
    def _dump_content(content: Any) -> str:
        """Best-effort JSON serialization of arbitrary tool-result content."""

        def _default(obj: Any) -> Any:
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            return str(obj)

        try:
            return json.dumps(content, default=_default)
        except (TypeError, ValueError):
            return str(content)

    def _convert_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        """Convert Anthropic Tool definitions to Responses function tools."""
        openai_tools: list[dict[str, Any]] = []
        for tool in tools:
            # Tools may be pydantic Tool objects OR raw dicts (the web-search
            # agentic loop passes dicts). Normalize to a dict first.
            td = (
                tool.model_dump()
                if hasattr(tool, "model_dump")
                else (tool if isinstance(tool, dict) else {})
            )
            # Default name/description to "" — mantle rejects null values.
            name = td.get("name") or ""
            description = td.get("description") or ""
            input_schema = td.get("input_schema")
            # Native server tools are executed by the proxy's existing loops.
            # Runtime accepts function definitions, not server tool types.
            if (
                td.get("type") not in (None, "custom", "function")
                and input_schema is None
            ):
                continue

            if isinstance(input_schema, dict):
                parameters = input_schema
            elif input_schema is not None and hasattr(input_schema, "model_dump"):
                parameters = input_schema.model_dump(exclude_none=True)
            else:
                parameters = {}

            openai_tools.append(
                {
                    "type": "function",
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                }
            )
        return openai_tools

    def _convert_tool_choice(self, tool_choice: Any) -> Any:
        """Translate Anthropic tool_choice into the Responses API shape.

        Mirrors the sibling Chat Completions converter:
        - "auto" → "auto"
        - "any" → "required"
        - {"type":"tool","name":X} → {"type":"function","name":X}
        - "none" / unknown → passed through sensibly.
        """
        if isinstance(tool_choice, str):
            if tool_choice == "any":
                return "required"
            return tool_choice  # "auto", "none", etc. pass through

        if isinstance(tool_choice, dict):
            tc_type = tool_choice.get("type", "")
            if tc_type == "auto":
                return "auto"
            elif tc_type == "any":
                return "required"
            elif tc_type == "none":
                return "none"
            elif tc_type == "tool":
                return {"type": "function", "name": tool_choice.get("name", "")}
        return "auto"
