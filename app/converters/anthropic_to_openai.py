"""
Converter from Anthropic Messages API format to OpenAI Chat Completions API format.

Handles conversion of requests for non-Claude models routed through
Bedrock Mantle's OpenAI-compatible endpoint.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.schemas.anthropic import (
    ImageContent,
    MessageRequest,
    SystemMessage,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)

logger = logging.getLogger(__name__)

# Server-side tool name prefixes that should be filtered out
# These are proxy-implemented tools, not passed to the model
SERVER_TOOL_PREFIXES = (
    "web_search_",
    "web_fetch_",
)

SERVER_TOOL_NAMES = {
    "code_execution",
}

SERVER_TOOL_TYPE_PREFIXES = (
    "computer_",
)


def _is_server_tool(tool: Dict[str, Any]) -> bool:
    """Check if a tool definition is a server-side tool that should be skipped."""
    name = tool.get("name", "")
    tool_type = tool.get("type", "")

    if name in SERVER_TOOL_NAMES:
        return True
    for prefix in SERVER_TOOL_PREFIXES:
        if name.startswith(prefix):
            return True
    for prefix in SERVER_TOOL_TYPE_PREFIXES:
        if tool_type.startswith(prefix):
            return True
    return False


class AnthropicToOpenAIConverter:
    """Converts Anthropic Messages API format to OpenAI Chat Completions API format."""

    def convert_request(self, request: MessageRequest) -> Dict[str, Any]:
        """Convert an Anthropic MessageRequest to an OpenAI Chat Completions request dict.

        Args:
            request: Anthropic MessageRequest object.

        Returns:
            Dictionary suitable for OpenAI Chat Completions API.
        """
        result: Dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
        }

        # Build messages list
        messages: List[Dict[str, Any]] = []

        # System message
        if request.system:
            system_text = self._convert_system(request.system)
            if system_text:
                messages.append({"role": "system", "content": system_text})

        # Conversation messages
        for msg in request.messages:
            converted = self._convert_message(msg.role, msg.content)
            messages.extend(converted)

        result["messages"] = messages

        # Optional scalar parameters
        if request.temperature is not None:
            result["temperature"] = request.temperature
        if request.top_p is not None:
            result["top_p"] = request.top_p
        if request.stop_sequences:
            result["stop"] = request.stop_sequences
        if request.stream is not None:
            result["stream"] = request.stream

        # Tools
        if request.tools:
            openai_tools = self._convert_tools(request.tools)
            if openai_tools:
                result["tools"] = openai_tools

        # Tool choice
        if request.tool_choice is not None:
            result["tool_choice"] = self._convert_tool_choice(request.tool_choice)

        # Thinking → reasoning_effort (Bedrock Mantle format)
        # if request.thinking and settings.enable_extended_thinking:
        #     if self._is_kimi_k25_model(request.model) or self._is_glm_47_model(request.model):
        #         # Kimi K2.5 always uses reasoning_effort="high"
        #         result["reasoning_effort"] = "high"
        #         result["extra_body"] = {"include_reasoning": True}
        #     else:
        #         effort = self._convert_thinking_to_effort(request.thinking)
        #         if effort:
        #             result["reasoning_effort"] = effort
        #             result["extra_body"] = {"include_reasoning": True}
        if request.thinking:
            result["reasoning_effort"] = "high"
            result["extra_body"] = {"include_reasoning": True}

        return result

    def _convert_system(
        self, system: Any
    ) -> str:
        """Convert Anthropic system prompt to a plain string.

        Anthropic system can be a string or a list of SystemMessage blocks.
        The field_validator on MessageRequest already converts strings to
        list-of-SystemMessage, so we always expect a list here.
        """
        if isinstance(system, str):
            return system
        if isinstance(system, list):
            texts = []
            for block in system:
                if isinstance(block, SystemMessage):
                    texts.append(block.text)
                elif isinstance(block, dict):
                    texts.append(block.get("text", ""))
            return "\n".join(texts)
        return ""

    def _convert_message(
        self, role: str, content: Any
    ) -> List[Dict[str, Any]]:
        """Convert a single Anthropic message to one or more OpenAI messages.

        Tool results become separate 'tool' role messages, so a single
        Anthropic message may expand to multiple OpenAI messages.
        """
        if isinstance(content, str):
            return [{"role": role, "content": content}]

        if not isinstance(content, list):
            return [{"role": role, "content": str(content)}]

        if role == "user":
            return self._convert_user_content_blocks(content)
        elif role == "assistant":
            return self._convert_assistant_content_blocks(content)
        else:
            return [{"role": role, "content": str(content)}]

    def _convert_user_content_blocks(
        self, blocks: list
    ) -> List[Dict[str, Any]]:
        """Convert user message content blocks.

        Text and image blocks become multipart content on a single user message.
        Tool result blocks become separate 'tool' role messages.
        """
        content_parts: List[Dict[str, Any]] = []
        tool_messages: List[Dict[str, Any]] = []

        for block in blocks:
            if isinstance(block, TextContent) or (
                isinstance(block, dict) and block.get("type") == "text"
            ):
                text = block.text if isinstance(block, TextContent) else block.get("text", "")
                content_parts.append({"type": "text", "text": text})

            elif isinstance(block, ImageContent) or (
                isinstance(block, dict) and block.get("type") == "image"
            ):
                source = block.source if isinstance(block, ImageContent) else block.get("source", {})
                if isinstance(source, dict):
                    media_type = source.get("media_type", "image/png")
                    data = source.get("data", "")
                else:
                    media_type = source.media_type
                    data = source.data
                data_url = f"data:{media_type};base64,{data}"
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_url},
                })

            elif isinstance(block, ToolResultContent) or (
                isinstance(block, dict) and block.get("type") == "tool_result"
            ):
                tool_msg = self._convert_tool_result(block)
                tool_messages.append(tool_msg)

        result: List[Dict[str, Any]] = []
        if content_parts:
            # If there's only one text part, simplify to a string
            if len(content_parts) == 1 and content_parts[0]["type"] == "text":
                result.append({"role": "user", "content": content_parts[0]["text"]})
            else:
                result.append({"role": "user", "content": content_parts})
        result.extend(tool_messages)
        return result

    def _convert_tool_result(self, block: Any) -> Dict[str, Any]:
        """Convert a tool_result block to an OpenAI tool message."""
        if isinstance(block, ToolResultContent):
            tool_call_id = block.tool_use_id
            content = block.content
        else:
            tool_call_id = block.get("tool_use_id", "")
            content = block.get("content", "")

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # Extract text from content blocks
            parts = []
            for item in content:
                if isinstance(item, TextContent):
                    parts.append(item.text)
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            text = "\n".join(parts) if parts else ""
        else:
            text = str(content)

        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": text,
        }

    def _convert_assistant_content_blocks(
        self, blocks: list
    ) -> List[Dict[str, Any]]:
        """Convert assistant message content blocks.

        Text blocks are concatenated into a single content string.
        Tool use blocks become tool_calls.
        """
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []

        for block in blocks:
            if isinstance(block, TextContent) or (
                isinstance(block, dict) and block.get("type") == "text"
            ):
                text = block.text if isinstance(block, TextContent) else block.get("text", "")
                text_parts.append(text)

            elif isinstance(block, ToolUseContent) or (
                isinstance(block, dict) and block.get("type") == "tool_use"
            ):
                if isinstance(block, ToolUseContent):
                    tc_id = block.id
                    tc_name = block.name
                    tc_input = block.input
                else:
                    tc_id = block.get("id", "")
                    tc_name = block.get("name", "")
                    tc_input = block.get("input", {})

                tool_calls.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": tc_name,
                        "arguments": json.dumps(tc_input),
                    },
                })

        msg: Dict[str, Any] = {"role": "assistant"}
        content_text = "\n".join(text_parts) if text_parts else None
        if content_text:
            msg["content"] = content_text
        else:
            msg["content"] = None
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return [msg]

    def _convert_tools(self, tools: List[Any]) -> List[Dict[str, Any]]:
        """Convert Anthropic tool definitions to OpenAI function tools.

        Skips server-side tools (web_search_*, web_fetch_*, code_execution, computer_*).
        """
        openai_tools: List[Dict[str, Any]] = []
        for tool in tools:
            tool_dict = tool.model_dump() if hasattr(tool, "model_dump") else (
                tool if isinstance(tool, dict) else {}
            )
            if _is_server_tool(tool_dict):
                continue

            name = tool_dict.get("name", "")
            description = tool_dict.get("description", "")
            input_schema = tool_dict.get("input_schema", {})

            openai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": input_schema,
                },
            })
        return openai_tools

    def _convert_tool_choice(self, tool_choice: Any) -> Any:
        """Convert Anthropic tool_choice to OpenAI tool_choice."""
        if isinstance(tool_choice, str):
            # Anthropic string shorthand: "auto" or "any"
            if tool_choice == "any":
                return "required"
            return tool_choice  # "auto" passes through

        if isinstance(tool_choice, dict):
            tc_type = tool_choice.get("type", "")
            if tc_type == "auto":
                return "auto"
            elif tc_type == "any":
                return "required"
            elif tc_type == "none":
                return "none"
            elif tc_type == "tool":
                return {
                    "type": "function",
                    "function": {"name": tool_choice.get("name", "")},
                }
        return "auto"

    @staticmethod
    def _is_kimi_k25_model(model: str) -> bool:
        """Check if the model is a Kimi K2.5 model."""
        model_lower = model.lower()
        return "kimi-k2.5" in model_lower or "kimi_k2.5" in model_lower

    @staticmethod
    def _is_glm_47_model(model: str) -> bool:
        """Check if the model is a glm 4.7 model."""
        model_lower = model.lower()
        return "glm-4.7" in model_lower or "glm_4.7" in model_lower
    
    def _convert_thinking_to_effort(self, thinking: Dict[str, Any]) -> Optional[str]:
        """Convert Anthropic thinking config to reasoning effort level.

        Maps budget_tokens to effort levels based on configured thresholds.
        Returns the effort string for Bedrock Mantle's reasoning_effort parameter.
        """
        if thinking.get("type") != "enabled":
            return None

        budget_tokens = thinking.get("budget_tokens", 0)
        high_threshold = settings.openai_compat_thinking_high_threshold
        medium_threshold = settings.openai_compat_thinking_medium_threshold

        if budget_tokens >= high_threshold:
            return "high"
        elif budget_tokens >= medium_threshold:
            return "medium"
        else:
            return "low"
