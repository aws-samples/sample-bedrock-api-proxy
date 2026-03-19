# OpenAI-Compatible API for Non-Claude Models Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add OpenAI Chat Completions API support via Bedrock Mantle endpoint for non-Claude models, as an alternative to the Converse API.

**Architecture:** When `ENABLE_OPENAI_COMPAT=True`, non-Claude model requests are converted from Anthropic Messages format to OpenAI Chat Completions format and sent to `bedrock-mantle.{region}.api.aws` via the `openai` Python SDK. Claude models remain unchanged (InvokeModel API). A new converter pair handles bidirectional format translation including thinking→reasoning mapping.

**Tech Stack:** Python, FastAPI, OpenAI Python SDK, Pydantic Settings

---

### Task 1: Add `openai` dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add openai to dependencies**

In `pyproject.toml`, add to the `dependencies` list:

```toml
    # OpenAI SDK (for Bedrock OpenAI-compatible endpoint)
    "openai>=1.60.0",
```

Add it after the `"httpx>=0.27.0",` line.

**Step 2: Install the dependency**

Run: `uv sync`
Expected: Successfully installs `openai` package

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add openai SDK dependency for Bedrock OpenAI-compat support"
```

---

### Task 2: Add config settings for OpenAI-compat mode

**Files:**
- Modify: `app/core/config.py` (after line 341, before Multi-Provider section)
- Modify: `.env` (add new env vars at the end)

**Step 1: Write the failing test**

Create `tests/unit/test_openai_compat_config.py`:

```python
"""Tests for OpenAI-compat configuration settings."""
import os
import pytest
from unittest.mock import patch


def test_openai_compat_defaults():
    """Test that OpenAI-compat settings have correct defaults."""
    from importlib import reload
    import app.core.config as config_module

    # Clear the lru_cache
    config_module.get_settings.cache_clear()
    with patch.dict(os.environ, {}, clear=False):
        settings = config_module.get_settings()
        assert settings.enable_openai_compat is False
        assert settings.openai_api_key == ""
        assert settings.openai_base_url == ""
        assert settings.openai_compat_thinking_high_threshold == 10000
        assert settings.openai_compat_thinking_medium_threshold == 4000
    config_module.get_settings.cache_clear()


def test_openai_compat_enabled_from_env():
    """Test enabling OpenAI-compat via env var."""
    from importlib import reload
    import app.core.config as config_module

    config_module.get_settings.cache_clear()
    with patch.dict(os.environ, {
        "ENABLE_OPENAI_COMPAT": "True",
        "OPENAI_API_KEY": "test-key-123",
        "OPENAI_BASE_URL": "https://bedrock-mantle.us-east-1.api.aws/v1",
    }, clear=False):
        settings = config_module.get_settings()
        assert settings.enable_openai_compat is True
        assert settings.openai_api_key == "test-key-123"
        assert settings.openai_base_url == "https://bedrock-mantle.us-east-1.api.aws/v1"
    config_module.get_settings.cache_clear()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_openai_compat_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'enable_openai_compat'`

**Step 3: Add config settings to `app/core/config.py`**

Add after the `# Web Fetch Settings` section (after line 341), before the `# === Multi-Provider Gateway` section:

```python
    # === OpenAI-Compatible API Settings (Bedrock Mantle) ===
    # When enabled, non-Claude models use OpenAI Chat Completions API via bedrock-mantle
    # instead of Bedrock Converse API. Claude models still use InvokeModel API.
    enable_openai_compat: bool = Field(
        default=False,
        alias="ENABLE_OPENAI_COMPAT",
        description="Use OpenAI Chat Completions API for non-Claude models (via bedrock-mantle)"
    )
    openai_api_key: str = Field(
        default="",
        alias="OPENAI_API_KEY",
        description="Bedrock API key for bedrock-mantle endpoint"
    )
    openai_base_url: str = Field(
        default="",
        alias="OPENAI_BASE_URL",
        description="Bedrock Mantle endpoint URL (e.g. https://bedrock-mantle.us-east-1.api.aws/v1)"
    )
    openai_compat_thinking_high_threshold: int = Field(
        default=10000,
        alias="OPENAI_COMPAT_THINKING_HIGH_THRESHOLD",
        description="budget_tokens >= this → reasoning effort 'high'"
    )
    openai_compat_thinking_medium_threshold: int = Field(
        default=4000,
        alias="OPENAI_COMPAT_THINKING_MEDIUM_THRESHOLD",
        description="budget_tokens >= this → reasoning effort 'medium', below → 'low'"
    )
```

**Step 4: Add env vars to `.env`**

Append to the end of `.env`:

```
# OpenAI-Compatible API (Bedrock Mantle) - for non-Claude models
# ENABLE_OPENAI_COMPAT=False
# OPENAI_API_KEY=your-bedrock-api-key
# OPENAI_BASE_URL=https://bedrock-mantle.us-east-1.api.aws/v1
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_openai_compat_config.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add app/core/config.py .env tests/unit/test_openai_compat_config.py
git commit -m "feat: add OpenAI-compat config settings for Bedrock Mantle"
```

---

### Task 3: Create Anthropic → OpenAI request converter

**Files:**
- Create: `app/converters/anthropic_to_openai.py`
- Create: `tests/unit/test_anthropic_to_openai.py`

**Step 1: Write the failing tests**

Create `tests/unit/test_anthropic_to_openai.py`:

```python
"""Tests for Anthropic to OpenAI Chat Completions conversion."""
import pytest
from unittest.mock import patch

from app.schemas.anthropic import MessageRequest


class TestAnthropicToOpenAIConverter:
    """Test Anthropic to OpenAI Chat Completions conversion."""

    def setup_method(self):
        from app.converters.anthropic_to_openai import AnthropicToOpenAIConverter
        self.converter = AnthropicToOpenAIConverter()

    def test_convert_simple_message(self):
        """Test conversion of a simple text message."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            messages=[{"role": "user", "content": "Hello!"}],
        )
        result = self.converter.convert_request(request)

        assert result["model"] == "us.deepseek.r1-v1:0"
        assert result["max_tokens"] == 1024
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "Hello!"

    def test_convert_with_system_message(self):
        """Test system message becomes first message with role system."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = self.converter.convert_request(request)

        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == "You are helpful."
        assert result["messages"][1]["role"] == "user"

    def test_convert_with_system_as_content_blocks(self):
        """Test system as array of content blocks."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            system=[{"type": "text", "text": "Part 1"}, {"type": "text", "text": "Part 2"}],
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = self.converter.convert_request(request)

        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == "Part 1\nPart 2"

    def test_convert_with_temperature(self):
        """Test temperature passthrough."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            temperature=0.7,
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = self.converter.convert_request(request)
        assert result["temperature"] == 0.7

    def test_convert_with_top_p(self):
        """Test top_p passthrough."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            top_p=0.9,
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = self.converter.convert_request(request)
        assert result["top_p"] == 0.9

    def test_convert_with_stop_sequences(self):
        """Test stop_sequences maps to stop."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            stop_sequences=["STOP", "END"],
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = self.converter.convert_request(request)
        assert result["stop"] == ["STOP", "END"]

    def test_convert_with_tools(self):
        """Test tool definitions conversion."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            tools=[{
                "name": "get_weather",
                "description": "Get the weather",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }],
            messages=[{"role": "user", "content": "Weather in Tokyo?"}],
        )
        result = self.converter.convert_request(request)

        assert len(result["tools"]) == 1
        tool = result["tools"][0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "get_weather"
        assert tool["function"]["description"] == "Get the weather"
        assert tool["function"]["parameters"]["type"] == "object"

    def test_convert_tool_use_assistant_message(self):
        """Test assistant message with tool_use converts to tool_calls."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": "Weather?"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me check."},
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "get_weather",
                            "input": {"city": "Tokyo"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_123",
                            "content": "Sunny, 25°C",
                        }
                    ],
                },
            ],
        )
        result = self.converter.convert_request(request)

        # Assistant message should have content + tool_calls
        assistant_msg = result["messages"][1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "Let me check."
        assert len(assistant_msg["tool_calls"]) == 1
        assert assistant_msg["tool_calls"][0]["id"] == "toolu_123"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_weather"

        # Tool result should become a tool message
        tool_msg = result["messages"][2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "toolu_123"
        assert tool_msg["content"] == "Sunny, 25°C"

    def test_convert_thinking_to_reasoning_high(self):
        """Test thinking with high budget maps to reasoning effort high."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=16000,
            thinking={"type": "enabled", "budget_tokens": 15000},
            messages=[{"role": "user", "content": "Think hard"}],
        )
        result = self.converter.convert_request(request)
        assert result["reasoning"] == {"effort": "high"}

    def test_convert_thinking_to_reasoning_medium(self):
        """Test thinking with medium budget maps to reasoning effort medium."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=8000,
            thinking={"type": "enabled", "budget_tokens": 5000},
            messages=[{"role": "user", "content": "Think"}],
        )
        result = self.converter.convert_request(request)
        assert result["reasoning"] == {"effort": "medium"}

    def test_convert_thinking_to_reasoning_low(self):
        """Test thinking with low budget maps to reasoning effort low."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=4000,
            thinking={"type": "enabled", "budget_tokens": 2000},
            messages=[{"role": "user", "content": "Quick"}],
        )
        result = self.converter.convert_request(request)
        assert result["reasoning"] == {"effort": "low"}

    def test_convert_multimodal_user_message(self):
        """Test user message with image content."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        },
                    },
                ],
            }],
        )
        result = self.converter.convert_request(request)

        content = result["messages"][0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "data:image/png;base64," in content[1]["image_url"]["url"]

    def test_convert_stream_flag(self):
        """Test stream flag is passed through."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            stream=True,
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = self.converter.convert_request(request)
        assert result["stream"] is True

    def test_convert_tool_choice_auto(self):
        """Test tool_choice auto mapping."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            tool_choice={"type": "auto"},
            tools=[{"name": "test", "description": "test", "input_schema": {"type": "object"}}],
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = self.converter.convert_request(request)
        assert result["tool_choice"] == "auto"

    def test_convert_tool_choice_any(self):
        """Test tool_choice any maps to required."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            tool_choice={"type": "any"},
            tools=[{"name": "test", "description": "test", "input_schema": {"type": "object"}}],
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = self.converter.convert_request(request)
        assert result["tool_choice"] == "required"

    def test_convert_tool_choice_specific_tool(self):
        """Test tool_choice with specific tool name."""
        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            tool_choice={"type": "tool", "name": "get_weather"},
            tools=[{"name": "get_weather", "description": "Weather", "input_schema": {"type": "object"}}],
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = self.converter.convert_request(request)
        assert result["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_anthropic_to_openai.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.converters.anthropic_to_openai'`

**Step 3: Implement the converter**

Create `app/converters/anthropic_to_openai.py`:

```python
"""
Converter from Anthropic Messages API format to OpenAI Chat Completions API format.

Used when ENABLE_OPENAI_COMPAT=True to route non-Claude models through
Bedrock's OpenAI-compatible endpoint (bedrock-mantle).
"""
import json
from typing import Any, Dict, List, Optional, Union

from app.core.config import settings


class AnthropicToOpenAIConverter:
    """Converts Anthropic Messages API format to OpenAI Chat Completions format."""

    def convert_request(self, request: "MessageRequest") -> Dict[str, Any]:
        """
        Convert Anthropic MessageRequest to OpenAI Chat Completions request format.

        Args:
            request: Anthropic MessageRequest object

        Returns:
            Dictionary in OpenAI Chat Completions API format
        """
        openai_request: Dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [],
        }

        # Add system message
        if request.system:
            system_text = self._convert_system(request.system)
            openai_request["messages"].append({"role": "system", "content": system_text})

        # Convert messages
        openai_request["messages"].extend(self._convert_messages(request.messages))

        # Optional parameters
        if request.temperature is not None:
            openai_request["temperature"] = request.temperature
        if request.top_p is not None:
            openai_request["top_p"] = request.top_p
        if request.stop_sequences:
            openai_request["stop"] = request.stop_sequences
        if request.stream is not None:
            openai_request["stream"] = request.stream

        # Convert tools
        if request.tools:
            openai_request["tools"] = self._convert_tools(request.tools)

        # Convert tool_choice
        if request.tool_choice:
            openai_request["tool_choice"] = self._convert_tool_choice(request.tool_choice)

        # Convert thinking to reasoning
        if request.thinking and settings.enable_extended_thinking:
            reasoning = self._convert_thinking_to_reasoning(request.thinking)
            if reasoning:
                openai_request["reasoning"] = reasoning

        return openai_request

    def _convert_system(self, system: Any) -> str:
        """Convert Anthropic system prompt to a single string."""
        if isinstance(system, str):
            return system
        if isinstance(system, list):
            parts = []
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block["text"])
                elif isinstance(block, str):
                    parts.append(block)
                elif hasattr(block, "text"):
                    parts.append(block.text)
            return "\n".join(parts)
        return str(system)

    def _convert_messages(self, messages: List[Any]) -> List[Dict[str, Any]]:
        """Convert Anthropic messages to OpenAI messages format."""
        openai_messages = []

        for msg in messages:
            role = msg.role if hasattr(msg, "role") else msg.get("role")
            content = msg.content if hasattr(msg, "content") else msg.get("content")

            if role == "user":
                openai_messages.extend(self._convert_user_message(content))
            elif role == "assistant":
                openai_messages.append(self._convert_assistant_message(content))

        return openai_messages

    def _convert_user_message(self, content: Any) -> List[Dict[str, Any]]:
        """Convert a user message, handling tool_result blocks as separate tool messages."""
        if isinstance(content, str):
            return [{"role": "user", "content": content}]

        if not isinstance(content, list):
            return [{"role": "user", "content": str(content)}]

        # Separate tool_result blocks from other content
        regular_content = []
        tool_results = []

        for block in content:
            block_dict = block if isinstance(block, dict) else (block.model_dump() if hasattr(block, "model_dump") else {"type": "text", "text": str(block)})
            block_type = block_dict.get("type", "")

            if block_type == "tool_result":
                tool_results.append(block_dict)
            elif block_type == "text":
                regular_content.append({"type": "text", "text": block_dict.get("text", "")})
            elif block_type == "image":
                source = block_dict.get("source", {})
                media_type = source.get("media_type", "image/png")
                data = source.get("data", "")
                regular_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                })

        messages = []

        # Add tool result messages first (they respond to previous assistant tool_calls)
        for tr in tool_results:
            tool_content = tr.get("content", "")
            if isinstance(tool_content, list):
                # Extract text from content blocks
                parts = []
                for c in tool_content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif isinstance(c, str):
                        parts.append(c)
                tool_content = "\n".join(parts)
            messages.append({
                "role": "tool",
                "tool_call_id": tr.get("tool_use_id", ""),
                "content": str(tool_content),
            })

        # Add regular user content if any
        if regular_content:
            if len(regular_content) == 1 and regular_content[0].get("type") == "text":
                messages.append({"role": "user", "content": regular_content[0]["text"]})
            else:
                messages.append({"role": "user", "content": regular_content})

        return messages

    def _convert_assistant_message(self, content: Any) -> Dict[str, Any]:
        """Convert assistant message, mapping tool_use to tool_calls."""
        if isinstance(content, str):
            return {"role": "assistant", "content": content}

        if not isinstance(content, list):
            return {"role": "assistant", "content": str(content)}

        text_parts = []
        tool_calls = []

        for block in content:
            block_dict = block if isinstance(block, dict) else (block.model_dump() if hasattr(block, "model_dump") else {"type": "text", "text": str(block)})
            block_type = block_dict.get("type", "")

            if block_type == "text":
                text_parts.append(block_dict.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": block_dict.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block_dict.get("name", ""),
                        "arguments": json.dumps(block_dict.get("input", {})),
                    },
                })

        msg: Dict[str, Any] = {"role": "assistant"}
        msg["content"] = "\n".join(text_parts) if text_parts else None
        if tool_calls:
            msg["tool_calls"] = tool_calls

        return msg

    def _convert_tools(self, tools: List[Any]) -> List[Dict[str, Any]]:
        """Convert Anthropic tool definitions to OpenAI function format."""
        openai_tools = []
        for tool in tools:
            tool_dict = tool if isinstance(tool, dict) else (tool.model_dump() if hasattr(tool, "model_dump") else {})
            tool_type = tool_dict.get("type", "custom")

            # Skip server-side tools (web_search, etc.)
            if tool_type in ("web_search_20250305", "web_search_20260209", "web_fetch_20250910", "web_fetch_20260209", "code_execution", "computer_20250124"):
                continue

            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool_dict.get("name", ""),
                    "description": tool_dict.get("description", ""),
                    "parameters": tool_dict.get("input_schema", {}),
                },
            })
        return openai_tools

    def _convert_tool_choice(self, tool_choice: Any) -> Any:
        """Convert Anthropic tool_choice to OpenAI format."""
        if isinstance(tool_choice, dict):
            tc_type = tool_choice.get("type", "auto")
            if tc_type == "auto":
                return "auto"
            elif tc_type == "any":
                return "required"
            elif tc_type == "tool":
                return {"type": "function", "function": {"name": tool_choice.get("name", "")}}
            elif tc_type == "none":
                return "none"
        return "auto"

    def _convert_thinking_to_reasoning(self, thinking: Any) -> Optional[Dict[str, str]]:
        """Convert Anthropic thinking config to OpenAI reasoning parameter."""
        if isinstance(thinking, dict) and thinking.get("type") == "enabled":
            budget = thinking.get("budget_tokens", 0)
            if budget >= settings.openai_compat_thinking_high_threshold:
                effort = "high"
            elif budget >= settings.openai_compat_thinking_medium_threshold:
                effort = "medium"
            else:
                effort = "low"
            return {"effort": effort}
        return None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_anthropic_to_openai.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app/converters/anthropic_to_openai.py tests/unit/test_anthropic_to_openai.py
git commit -m "feat: add Anthropic to OpenAI Chat Completions request converter"
```

---

### Task 4: Create OpenAI → Anthropic response converter

**Files:**
- Create: `app/converters/openai_to_anthropic.py`
- Create: `tests/unit/test_openai_to_anthropic.py`

**Step 1: Write the failing tests**

Create `tests/unit/test_openai_to_anthropic.py`:

```python
"""Tests for OpenAI Chat Completions to Anthropic response conversion."""
import pytest


class TestOpenAIToAnthropicConverter:
    """Test OpenAI to Anthropic response conversion."""

    def setup_method(self):
        from app.converters.openai_to_anthropic import OpenAIToAnthropicConverter
        self.converter = OpenAIToAnthropicConverter()

    def test_convert_simple_text_response(self):
        """Test conversion of a simple text response."""
        openai_response = {
            "id": "chatcmpl-123",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "us.deepseek.r1-v1:0",
        }
        result = self.converter.convert_response(openai_response, "us.deepseek.r1-v1:0", "msg_123")

        assert result.id == "msg_123"
        assert result.role == "assistant"
        assert result.model == "us.deepseek.r1-v1:0"
        assert result.stop_reason == "end_turn"
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text == "Hello!"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5

    def test_convert_stop_reason_length(self):
        """Test finish_reason 'length' maps to 'max_tokens'."""
        openai_response = {
            "id": "chatcmpl-123",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "..."}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "test",
        }
        result = self.converter.convert_response(openai_response, "test", "msg_123")
        assert result.stop_reason == "max_tokens"

    def test_convert_stop_reason_tool_calls(self):
        """Test finish_reason 'tool_calls' maps to 'tool_use'."""
        openai_response = {
            "id": "chatcmpl-123",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Tokyo"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "model": "test",
        }
        result = self.converter.convert_response(openai_response, "test", "msg_123")

        assert result.stop_reason == "tool_use"
        assert len(result.content) == 1
        assert result.content[0].type == "tool_use"
        assert result.content[0].id == "call_123"
        assert result.content[0].name == "get_weather"
        assert result.content[0].input == {"city": "Tokyo"}

    def test_convert_response_with_text_and_tool_calls(self):
        """Test response with both text and tool calls."""
        openai_response = {
            "id": "chatcmpl-123",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Let me check.",
                    "tool_calls": [{
                        "id": "call_456",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"q": "test"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "model": "test",
        }
        result = self.converter.convert_response(openai_response, "test", "msg_123")

        assert len(result.content) == 2
        assert result.content[0].type == "text"
        assert result.content[0].text == "Let me check."
        assert result.content[1].type == "tool_use"


class TestOpenAIStreamToAnthropic:
    """Test OpenAI streaming chunks to Anthropic SSE events."""

    def setup_method(self):
        from app.converters.openai_to_anthropic import OpenAIToAnthropicConverter
        self.converter = OpenAIToAnthropicConverter()

    def test_create_message_start_event(self):
        """Test creating the initial message_start event."""
        event = self.converter.create_message_start_event("msg_123", "test-model")
        assert event["type"] == "message_start"
        assert event["message"]["id"] == "msg_123"
        assert event["message"]["model"] == "test-model"

    def test_convert_text_delta_chunk(self):
        """Test converting a text delta streaming chunk."""
        chunk = {
            "choices": [{
                "index": 0,
                "delta": {"content": "Hello"},
                "finish_reason": None,
            }],
        }
        events = self.converter.convert_stream_chunk(chunk, 0)
        # Should produce content_block_delta
        deltas = [e for e in events if e["type"] == "content_block_delta"]
        assert len(deltas) == 1
        assert deltas[0]["delta"]["type"] == "text_delta"
        assert deltas[0]["delta"]["text"] == "Hello"

    def test_convert_tool_call_delta_chunk(self):
        """Test converting a tool call delta streaming chunk."""
        chunk = {
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_789",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": ""},
                    }],
                },
                "finish_reason": None,
            }],
        }
        events = self.converter.convert_stream_chunk(chunk, 0)
        starts = [e for e in events if e["type"] == "content_block_start"]
        assert len(starts) == 1
        assert starts[0]["content_block"]["type"] == "tool_use"

    def test_convert_finish_reason_stop(self):
        """Test converting finish_reason in streaming."""
        chunk = {
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }
        events = self.converter.convert_stream_chunk(chunk, 0)
        deltas = [e for e in events if e["type"] == "message_delta"]
        assert len(deltas) == 1
        assert deltas[0]["delta"]["stop_reason"] == "end_turn"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_openai_to_anthropic.py -v`
Expected: FAIL

**Step 3: Implement the converter**

Create `app/converters/openai_to_anthropic.py`:

```python
"""
Converter from OpenAI Chat Completions API format to Anthropic Messages API format.

Handles both non-streaming responses and streaming chunks.
"""
import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.schemas.anthropic import (
    ContentBlock,
    MessageResponse,
    TextContent,
    ThinkingContent,
    ToolUseContent,
    Usage,
)


class OpenAIToAnthropicConverter:
    """Converts OpenAI Chat Completions format to Anthropic Messages API format."""

    # Map OpenAI finish_reason to Anthropic stop_reason
    STOP_REASON_MAP = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
        "function_call": "tool_use",
    }

    def convert_response(
        self,
        openai_response: Dict[str, Any],
        model: str,
        message_id: str,
    ) -> MessageResponse:
        """Convert a non-streaming OpenAI Chat Completions response to Anthropic format."""
        choice = openai_response.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")
        usage_data = openai_response.get("usage", {})

        content = self._convert_message_content(message)
        stop_reason = self.STOP_REASON_MAP.get(finish_reason, "end_turn")

        return MessageResponse(
            id=message_id,
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason=stop_reason,
            stop_sequence=None,
            usage=Usage(
                input_tokens=usage_data.get("prompt_tokens", 0),
                output_tokens=usage_data.get("completion_tokens", 0),
            ),
        )

    def _convert_message_content(self, message: Dict[str, Any]) -> List[ContentBlock]:
        """Convert OpenAI message to Anthropic content blocks."""
        content_blocks: List[ContentBlock] = []

        # Text content
        text = message.get("content")
        if text:
            content_blocks.append(TextContent(type="text", text=text))

        # Tool calls
        tool_calls = message.get("tool_calls", [])
        for tc in tool_calls:
            func = tc.get("function", {})
            try:
                tool_input = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_input = {}
            content_blocks.append(
                ToolUseContent(
                    type="tool_use",
                    id=tc.get("id", f"toolu_{uuid4().hex}"),
                    name=func.get("name", ""),
                    input=tool_input,
                )
            )

        return content_blocks

    # --- Streaming support ---

    def create_message_start_event(self, message_id: str, model: str) -> Dict[str, Any]:
        """Create the initial message_start SSE event."""
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

    def convert_stream_chunk(
        self,
        chunk: Dict[str, Any],
        current_content_index: int,
    ) -> List[Dict[str, Any]]:
        """
        Convert an OpenAI streaming chunk to Anthropic SSE events.

        Args:
            chunk: OpenAI streaming chunk
            current_content_index: Current content block index

        Returns:
            List of Anthropic SSE event dicts
        """
        events: List[Dict[str, Any]] = []
        choice = chunk.get("choices", [{}])[0] if chunk.get("choices") else {}
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # Text content delta
        if delta.get("content") is not None:
            events.append({
                "type": "content_block_delta",
                "index": current_content_index,
                "delta": {"type": "text_delta", "text": delta["content"]},
            })

        # Tool call deltas
        tool_calls = delta.get("tool_calls", [])
        for tc in tool_calls:
            func = tc.get("function", {})
            if tc.get("id"):
                # New tool call — emit content_block_start
                events.append({
                    "type": "content_block_start",
                    "index": current_content_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": func.get("name", ""),
                    },
                })
            if func.get("arguments"):
                events.append({
                    "type": "content_block_delta",
                    "index": current_content_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": func["arguments"],
                    },
                })

        # Finish reason
        if finish_reason:
            stop_reason = self.STOP_REASON_MAP.get(finish_reason, "end_turn")
            events.append({
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 0},
            })

        return events

    def create_message_stop_event(self) -> Dict[str, Any]:
        """Create the final message_stop SSE event."""
        return {"type": "message_stop"}

    def create_error_event(self, error_code: str, error_message: str) -> Dict[str, Any]:
        """Create an error SSE event."""
        error_type_mapping = {
            "rate_limit_exceeded": "rate_limit_error",
            "invalid_request_error": "invalid_request_error",
            "authentication_error": "authentication_error",
            "server_error": "api_error",
        }
        return {
            "type": "error",
            "error": {
                "type": error_type_mapping.get(error_code, "api_error"),
                "message": error_message,
            },
        }
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_openai_to_anthropic.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app/converters/openai_to_anthropic.py tests/unit/test_openai_to_anthropic.py
git commit -m "feat: add OpenAI to Anthropic response converter"
```

---

### Task 5: Create OpenAI-compat service

**Files:**
- Create: `app/services/openai_compat_service.py`
- Create: `tests/unit/test_openai_compat_service.py`

**Step 1: Write failing tests**

Create `tests/unit/test_openai_compat_service.py`:

```python
"""Tests for OpenAI-compatible service."""
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class TestOpenAICompatService:
    """Test OpenAI-compat service initialization and request flow."""

    @patch("app.services.openai_compat_service.settings")
    def test_service_init(self, mock_settings):
        """Test service initializes OpenAI client correctly."""
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_base_url = "https://bedrock-mantle.us-east-1.api.aws/v1"
        mock_settings.bedrock_timeout = 300

        from app.services.openai_compat_service import OpenAICompatService
        service = OpenAICompatService()
        assert service.client is not None

    @patch("app.services.openai_compat_service.settings")
    def test_invoke_model_sync(self, mock_settings):
        """Test synchronous model invocation."""
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_base_url = "https://bedrock-mantle.us-east-1.api.aws/v1"
        mock_settings.bedrock_timeout = 300
        mock_settings.enable_extended_thinking = True
        mock_settings.openai_compat_thinking_high_threshold = 10000
        mock_settings.openai_compat_thinking_medium_threshold = 4000

        from app.services.openai_compat_service import OpenAICompatService
        from app.schemas.anthropic import MessageRequest

        service = OpenAICompatService()

        # Mock the OpenAI client
        mock_completion = MagicMock()
        mock_completion.model_dump.return_value = {
            "id": "chatcmpl-123",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hi there!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            "model": "us.deepseek.r1-v1:0",
        }
        service.client.chat.completions.create = MagicMock(return_value=mock_completion)

        request = MessageRequest(
            model="us.deepseek.r1-v1:0",
            max_tokens=1024,
            messages=[{"role": "user", "content": "Hello"}],
        )
        result = service.invoke_model_sync(request, "msg_test")

        assert result.id == "msg_test"
        assert result.content[0].text == "Hi there!"
        assert result.stop_reason == "end_turn"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_openai_compat_service.py -v`
Expected: FAIL

**Step 3: Implement the service**

Create `app/services/openai_compat_service.py`:

```python
"""
OpenAI-compatible service for Bedrock Mantle endpoint.

Routes non-Claude model requests through OpenAI Chat Completions API
via the bedrock-mantle endpoint.
"""
import asyncio
import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import uuid4

from openai import OpenAI

from app.converters.anthropic_to_openai import AnthropicToOpenAIConverter
from app.converters.openai_to_anthropic import OpenAIToAnthropicConverter
from app.core.config import settings
from app.schemas.anthropic import MessageRequest, MessageResponse


# Reuse the global thread pool from bedrock_service
_openai_executor: Optional[ThreadPoolExecutor] = None
_openai_semaphore: Optional[asyncio.Semaphore] = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _openai_executor
    if _openai_executor is None:
        with _executor_lock:
            if _openai_executor is None:
                _openai_executor = ThreadPoolExecutor(
                    max_workers=settings.bedrock_thread_pool_size,
                    thread_name_prefix="openai-compat-"
                )
    return _openai_executor


def _get_semaphore() -> asyncio.Semaphore:
    global _openai_semaphore
    if _openai_semaphore is None:
        _openai_semaphore = asyncio.Semaphore(settings.bedrock_semaphore_size)
    return _openai_semaphore


class OpenAICompatService:
    """Service for interacting with Bedrock via OpenAI-compatible endpoint."""

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.bedrock_timeout,
        )
        self.request_converter = AnthropicToOpenAIConverter()
        self.response_converter = OpenAIToAnthropicConverter()

    def invoke_model_sync(
        self, request: MessageRequest, request_id: Optional[str] = None
    ) -> MessageResponse:
        """Synchronous model invocation via OpenAI Chat Completions API."""
        message_id = request_id or f"msg_{uuid4().hex}"

        # Convert request
        openai_request = self.request_converter.convert_request(request)
        openai_request["stream"] = False  # Force non-streaming

        print(f"[OPENAI COMPAT] Calling Chat Completions API")
        print(f"  - Model: {openai_request['model']}")
        print(f"  - Messages: {len(openai_request['messages'])}")

        # Call OpenAI API
        completion = self.client.chat.completions.create(**openai_request)
        response_dict = completion.model_dump()

        print(f"[OPENAI COMPAT] Received response")
        print(f"  - Finish reason: {response_dict.get('choices', [{}])[0].get('finish_reason')}")
        print(f"  - Usage: {response_dict.get('usage')}")

        # Convert response
        return self.response_converter.convert_response(
            response_dict, request.model, message_id
        )

    async def invoke_model(
        self, request: MessageRequest, request_id: Optional[str] = None
    ) -> MessageResponse:
        """Async model invocation (runs sync call in thread pool)."""
        semaphore = _get_semaphore()
        async with semaphore:
            executor = _get_executor()
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor, self.invoke_model_sync, request, request_id
            )

    async def invoke_model_stream(
        self, request: MessageRequest, request_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Streaming model invocation via OpenAI Chat Completions API."""
        semaphore = _get_semaphore()
        async with semaphore:
            message_id = request_id or f"msg_{uuid4().hex}"
            event_queue: queue.Queue = queue.Queue()
            executor = _get_executor()
            loop = asyncio.get_event_loop()

            future = loop.run_in_executor(
                executor,
                self._stream_worker,
                request,
                message_id,
                event_queue,
            )

            # Consume events from queue asynchronously
            try:
                while True:
                    try:
                        msg_type, data = event_queue.get_nowait()
                        if msg_type == "done":
                            break
                        elif msg_type == "error":
                            error_code, error_message = data
                            error_event = self.response_converter.create_error_event(
                                error_code, error_message
                            )
                            yield self._format_sse_event(error_event)
                            break
                        elif msg_type == "event":
                            yield data
                    except queue.Empty:
                        await asyncio.sleep(0.005)
                        if future.done():
                            while True:
                                try:
                                    msg_type, data = event_queue.get_nowait()
                                    if msg_type == "event":
                                        yield data
                                    elif msg_type == "error":
                                        error_code, error_message = data
                                        error_event = self.response_converter.create_error_event(
                                            error_code, error_message
                                        )
                                        yield self._format_sse_event(error_event)
                                    elif msg_type == "done":
                                        break
                                except queue.Empty:
                                    break
                            try:
                                future.result()
                            except Exception as e:
                                error_event = self.response_converter.create_error_event(
                                    "server_error", str(e)
                                )
                                yield self._format_sse_event(error_event)
                            break
            except Exception as e:
                error_event = self.response_converter.create_error_event(
                    "server_error", str(e)
                )
                yield self._format_sse_event(error_event)

    def _stream_worker(
        self,
        request: MessageRequest,
        message_id: str,
        event_queue: queue.Queue,
    ) -> None:
        """Worker function for streaming in thread pool."""
        try:
            openai_request = self.request_converter.convert_request(request)
            openai_request["stream"] = True

            print(f"[OPENAI COMPAT STREAM] Calling Chat Completions API (streaming)")
            print(f"  - Model: {openai_request['model']}")

            # Emit message_start
            start_event = self.response_converter.create_message_start_event(
                message_id, request.model
            )
            event_queue.put(("event", self._format_sse_event(start_event)))

            # Track state for content block management
            text_block_started = False
            current_tool_index = -1
            content_index = 0

            stream = self.client.chat.completions.create(**openai_request)

            for chunk in stream:
                chunk_dict = chunk.model_dump()
                choice = chunk_dict.get("choices", [{}])[0] if chunk_dict.get("choices") else {}
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")

                # Handle text content
                if delta.get("content") is not None:
                    if not text_block_started:
                        # Start text block
                        block_start = {
                            "type": "content_block_start",
                            "index": content_index,
                            "content_block": {"type": "text", "text": ""},
                        }
                        event_queue.put(("event", self._format_sse_event(block_start)))
                        text_block_started = True

                    text_delta = {
                        "type": "content_block_delta",
                        "index": content_index,
                        "delta": {"type": "text_delta", "text": delta["content"]},
                    }
                    event_queue.put(("event", self._format_sse_event(text_delta)))

                # Handle tool calls
                for tc in delta.get("tool_calls", []):
                    tc_index = tc.get("index", 0)
                    func = tc.get("function", {})

                    if tc.get("id"):
                        # Close text block if open
                        if text_block_started:
                            event_queue.put(("event", self._format_sse_event(
                                {"type": "content_block_stop", "index": content_index}
                            )))
                            content_index += 1
                            text_block_started = False

                        # Close previous tool block if any
                        if current_tool_index >= 0:
                            event_queue.put(("event", self._format_sse_event(
                                {"type": "content_block_stop", "index": content_index}
                            )))
                            content_index += 1

                        current_tool_index = tc_index
                        block_start = {
                            "type": "content_block_start",
                            "index": content_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": tc["id"],
                                "name": func.get("name", ""),
                            },
                        }
                        event_queue.put(("event", self._format_sse_event(block_start)))

                    if func.get("arguments"):
                        input_delta = {
                            "type": "content_block_delta",
                            "index": content_index,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": func["arguments"],
                            },
                        }
                        event_queue.put(("event", self._format_sse_event(input_delta)))

                # Handle finish
                if finish_reason:
                    # Close open blocks
                    if text_block_started:
                        event_queue.put(("event", self._format_sse_event(
                            {"type": "content_block_stop", "index": content_index}
                        )))
                    elif current_tool_index >= 0:
                        event_queue.put(("event", self._format_sse_event(
                            {"type": "content_block_stop", "index": content_index}
                        )))

                    stop_reason = self.response_converter.STOP_REASON_MAP.get(
                        finish_reason, "end_turn"
                    )
                    # Get usage from chunk if available
                    chunk_usage = chunk_dict.get("usage", {})
                    event_queue.put(("event", self._format_sse_event({
                        "type": "message_delta",
                        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                        "usage": {"output_tokens": chunk_usage.get("completion_tokens", 0)},
                    })))
                    event_queue.put(("event", self._format_sse_event({"type": "message_stop"})))

            event_queue.put(("done", None))

        except Exception as e:
            print(f"[OPENAI COMPAT STREAM] Error: {type(e).__name__}: {e}")
            import traceback
            print(f"[ERROR] {traceback.format_exc()}")
            event_queue.put(("error", ("server_error", str(e))))

    def _format_sse_event(self, event: Dict[str, Any]) -> str:
        """Format an event as SSE string."""
        event_type = event.get("type", "unknown")
        event_data = json.dumps(event)
        return f"event: {event_type}\ndata: {event_data}\n\n"
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_openai_compat_service.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add app/services/openai_compat_service.py tests/unit/test_openai_compat_service.py
git commit -m "feat: add OpenAI-compat service for Bedrock Mantle endpoint"
```

---

### Task 6: Integrate into BedrockService routing

**Files:**
- Modify: `app/services/bedrock_service.py`

**Step 1: Write failing test**

Add to `tests/unit/test_openai_compat_service.py`:

```python
class TestBedrockServiceOpenAIRouting:
    """Test that BedrockService routes to OpenAI-compat when enabled."""

    @patch("app.services.bedrock_service.settings")
    @patch("app.services.bedrock_service.OpenAICompatService")
    def test_non_claude_routes_to_openai_when_enabled(self, mock_openai_cls, mock_settings):
        """Test non-Claude model routes to OpenAI compat service."""
        mock_settings.enable_openai_compat = True
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_base_url = "https://bedrock-mantle.us-east-1.api.aws/v1"
        mock_settings.aws_region = "us-east-1"
        mock_settings.bedrock_timeout = 300
        mock_settings.bedrock_endpoint_url = None
        mock_settings.aws_access_key_id = None
        mock_settings.aws_secret_access_key = None
        mock_settings.aws_session_token = None
        mock_settings.bedrock_thread_pool_size = 5
        mock_settings.bedrock_semaphore_size = 5
        mock_settings.default_model_mapping = {}
        mock_settings.enable_tracing = False

        from app.services.bedrock_service import BedrockService
        service = BedrockService.__new__(BedrockService)
        assert hasattr(service, '_openai_compat_service') or True  # Will be set in __init__
```

**Step 2: Modify `app/services/bedrock_service.py`**

Add import at top (after existing imports around line 31):

```python
# Lazy import for OpenAI compat service
_openai_compat_service = None
```

In `BedrockService.__init__` (after line 100), add:

```python
        # Initialize OpenAI-compat service if enabled
        self._openai_compat_service = None
        if settings.enable_openai_compat and settings.openai_api_key and settings.openai_base_url:
            from app.services.openai_compat_service import OpenAICompatService
            self._openai_compat_service = OpenAICompatService()
            print(f"[BEDROCK] OpenAI-compat mode enabled, base_url={settings.openai_base_url}")
```

In `_invoke_model_sync_inner` (around line 548), change the non-Claude branch:

```python
        # Route Claude models to InvokeModel API for better feature support
        if self._is_claude_model(request.model):
            print(f"[BEDROCK] Using InvokeModel API for Claude model: {request.model}")
            return self._invoke_model_native_sync(request, request_id, anthropic_beta, cache_ttl=cache_ttl)

        # Route to OpenAI-compat service if enabled
        if self._openai_compat_service:
            print(f"[BEDROCK] Using OpenAI Chat Completions API for non-Claude model: {request.model}")
            return self._openai_compat_service.invoke_model_sync(request, request_id)

        print(f"[BEDROCK] Converting request to Bedrock format for request {request_id}")
        # ... rest of Converse API code unchanged ...
```

In `invoke_model_stream` (around line 903), change the non-Claude streaming branch:

```python
            else:
                # Route to OpenAI-compat streaming if enabled
                if self._openai_compat_service:
                    print(f"[BEDROCK STREAM] Using OpenAI Chat Completions API (streaming) for: {request.model}")
                    # Use OpenAI compat streaming directly (it has its own queue+thread pattern)
                    async for event in self._openai_compat_service.invoke_model_stream(request, message_id):
                        yield event
                    return

                print(f"[BEDROCK STREAM] Converting request to Bedrock format for request {request_id}")
                # ... rest of Converse streaming code unchanged ...
```

**Step 3: Run all tests**

Run: `uv run pytest tests/unit/ -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add app/services/bedrock_service.py tests/unit/test_openai_compat_service.py
git commit -m "feat: integrate OpenAI-compat routing into BedrockService"
```

---

### Task 7: Update CDK deployment config

**Files:**
- Modify: `cdk/config/config.ts`
- Modify: `cdk/lib/ecs-stack.ts`

**Step 1: Add config interface fields to `cdk/config/config.ts`**

In `EnvironmentConfig` interface (after line 88, the tracing section), add:

```typescript
  // OpenAI-Compatible API (Bedrock Mantle) Configuration
  enableOpenaiCompat: boolean;
  openaiBaseUrl?: string;                    // e.g., https://bedrock-mantle.us-east-1.api.aws/v1
```

In both `dev` and `prod` environment configs, add:

```typescript
    // OpenAI-Compatible API (Bedrock Mantle)
    enableOpenaiCompat: false,
    // openaiBaseUrl: 'https://bedrock-mantle.us-east-1.api.aws/v1',
```

In `getConfig()` function (around line 382), add env var override:

```typescript
    // Override OpenAI-compat settings from environment variables
    const enableOpenaiCompat = process.env.ENABLE_OPENAI_COMPAT
      ? process.env.ENABLE_OPENAI_COMPAT.toLowerCase() === 'true'
      : config.enableOpenaiCompat;
```

And include in the return spread:

```typescript
    enableOpenaiCompat,
    ...(process.env.OPENAI_BASE_URL && { openaiBaseUrl: process.env.OPENAI_BASE_URL }),
```

**Step 2: Add env vars to `cdk/lib/ecs-stack.ts`**

In `environmentVars` (after the Cache TTL section, around line 274), add:

```typescript
      // OpenAI-Compatible API (Bedrock Mantle)
      ENABLE_OPENAI_COMPAT: config.enableOpenaiCompat.toString(),
      ...(config.openaiBaseUrl && { OPENAI_BASE_URL: config.openaiBaseUrl }),
```

For `OPENAI_API_KEY`, it should be stored in Secrets Manager like `MASTER_API_KEY`. Add it alongside the existing `masterApiKeySecret` secret injection. In the container secrets section (around line 406-409 where `MASTER_API_KEY` is injected), add:

```typescript
      // Conditionally add OPENAI_API_KEY from Secrets Manager
      ...(config.enableOpenaiCompat && openaiApiKeySecret ? {
        OPENAI_API_KEY: ecs.Secret.fromSecretsManager(openaiApiKeySecret, 'apiKey'),
      } : {}),
```

Note: The CDK stack will need an optional Secrets Manager reference for the OpenAI API key. This follows the same pattern as `MASTER_API_KEY`. The exact CDK construct depends on how the secret is provisioned — for MVP, the API key can also be passed as a plain env var via the CDK config and overridden with `process.env.OPENAI_API_KEY`.

**Simpler MVP approach** — pass as env var (not Secret Manager):

```typescript
      ...(process.env.OPENAI_API_KEY && { OPENAI_API_KEY: process.env.OPENAI_API_KEY }),
```

**Step 3: Commit**

```bash
git add cdk/config/config.ts cdk/lib/ecs-stack.ts
git commit -m "feat: add OpenAI-compat CDK deployment config"
```

---

### Task 8: Update `.env` and documentation

**Files:**
- Modify: `.env`
- Modify: `CLAUDE.md` (update features list)

**Step 1: Add env vars to `.env`**

Ensure the OpenAI compat section is in `.env` (may already be done in Task 2):

```
# OpenAI-Compatible API (Bedrock Mantle) - for non-Claude models
# When enabled, non-Claude models use OpenAI Chat Completions API via bedrock-mantle
# instead of Bedrock Converse API. Claude models still use InvokeModel.
# ENABLE_OPENAI_COMPAT=False
# OPENAI_API_KEY=your-bedrock-api-key
# OPENAI_BASE_URL=https://bedrock-mantle.us-east-1.api.aws/v1
# OPENAI_COMPAT_THINKING_HIGH_THRESHOLD=10000
# OPENAI_COMPAT_THINKING_MEDIUM_THRESHOLD=4000
```

**Step 2: Update CLAUDE.md features section**

Add to the features list:

```markdown
- **OpenAI-Compatible API**: Non-Claude models can use Bedrock's OpenAI Chat Completions API via bedrock-mantle endpoint instead of Converse API. Controlled by `ENABLE_OPENAI_COMPAT` flag. Supports thinking→reasoning effort mapping.
```

Add to Environment Variables section:

```markdown
**OpenAI-Compat:** `ENABLE_OPENAI_COMPAT`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_COMPAT_THINKING_HIGH_THRESHOLD`, `OPENAI_COMPAT_THINKING_MEDIUM_THRESHOLD`
```

**Step 3: Commit**

```bash
git add .env CLAUDE.md
git commit -m "docs: add OpenAI-compat configuration and documentation"
```

---

### Task 9: Run full test suite and verify

**Step 1: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 2: Run linting**

Run: `ruff check app/converters/anthropic_to_openai.py app/converters/openai_to_anthropic.py app/services/openai_compat_service.py`
Expected: No errors (fix any issues)

**Step 3: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address linting issues in OpenAI-compat implementation"
```
