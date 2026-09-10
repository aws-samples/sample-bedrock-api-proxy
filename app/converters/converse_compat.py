"""Request-scoped compatibility for clients using the Converse API.

Keep the tool-name mapping with the invocation, never on a shared converter:
simultaneous requests may expose different MCP tools.
"""

import hashlib
import json
import re
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from app.core.exceptions import ValidationError

_TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
_GPT_MODEL = re.compile(r"(?:^|[/.])openai\.gpt-")
_CONTINUE_RESPONSE = (
    "Continue the preceding assistant response from where it left off. "
    "Return only the continuation, without repeating the existing text."
)


class ConverseRequestAdapter:
    """Normalize a Converse request and restore client tool names in replies."""

    def __init__(self, request: dict[str, Any], resolved_model_id: str):
        self.request = deepcopy(request)
        self._original_names: dict[str, str] = {}
        self._is_gpt = bool(_GPT_MODEL.search(resolved_model_id))
        self._reasoning_indices: set[int] = set()
        self._visible_indices: dict[int, int] = {}
        self.stop_sequences: list[str] = []
        self._normalize_system_messages()
        self._normalize_tools()
        if self._is_gpt:
            self.stop_sequences = self.request.get("inferenceConfig", {}).pop(
                "stopSequences", []
            )
            if any(not stop for stop in self.stop_sequences):
                raise ValidationError("Stop sequences must not be empty strings.")
            self._normalize_gpt_history()
            self._normalize_gpt_tool_result_images()
            self._normalize_gpt_last_turn()

    def _normalize_gpt_tool_result_images(self) -> None:
        # GPT Converse accepts user images, but rejects images nested inside
        # toolResult.content. Keep the actual image bytes in the same user turn,
        # immediately after their result, with explicit attachment references.
        for message in self.request.get("messages", []):
            if message["role"] != "user":
                continue
            content = []
            for block in message.get("content", []):
                content.append(block)
                result = block.get("toolResult")
                if result is None:
                    continue
                attachments = []
                result_content = []
                for child in result.get("content", []):
                    if "image" not in child:
                        result_content.append(child)
                        continue
                    attachments.append(child)
                    result_content.append(
                        {
                            "text": (
                                f"Image attachment {len(attachments)} for tool result "
                                f"{result['toolUseId']} is provided immediately "
                                "after this result."
                            )
                        }
                    )
                if attachments:
                    result["content"] = result_content
                    content.extend(attachments)
            message["content"] = content

    def _normalize_gpt_history(self) -> None:
        # Claude thinking/signatures are provider-specific state. GPT Converse
        # rejects reasoningText in historical assistant messages. Preserve the
        # actual conversation and tool calls/results, not foreign reasoning.
        messages = []
        for message in self.request.get("messages", []):
            if message["role"] == "assistant":
                message["content"] = [
                    block
                    for block in message.get("content", [])
                    if "reasoningContent" not in block
                ]
                if not message["content"]:
                    continue
            messages.append(message)
        self.request["messages"] = messages

    def _normalize_system_messages(self) -> None:
        # Claude Code's mid-conversation-system beta can append a system turn
        # after the user's input. Converse expects these instructions in its
        # top-level system field, not in the user/assistant conversation.
        # Preserve their authority and order rather than relabeling as user text.
        messages = []
        system = self.request.get("system", [])
        for message in self.request.get("messages", []):
            if message["role"] != "system":
                messages.append(message)
                continue
            for block in message.get("content", []):
                if set(block) not in ({"text"}, {"cachePoint"}):
                    raise ValidationError(
                        "Converse inline system messages only support text "
                        "and cache points."
                    )
                system.append(block)
        self.request["messages"] = messages
        if system:
            self.request["system"] = system

    def _normalize_tools(self) -> None:
        # Only visit protocol fields. Tool inputs/schemas may also contain a
        # "name" property, and must not be rewritten.
        name_fields = []
        tool_config = self.request.get("toolConfig", {})
        for tool in tool_config.get("tools", []):
            spec = tool.get("toolSpec")
            if spec is None:  # e.g. a cachePoint
                continue
            name_fields.append(spec)
            if not (spec.get("description") or "").strip():
                spec["description"] = f"Tool {spec['name']}."

        choice = tool_config.get("toolChoice", {}).get("tool")
        if choice is not None:
            name_fields.append(choice)

        for message in self.request.get("messages", []):
            for block in message.get("content", []):
                if "toolUse" in block:
                    name_fields.append(block["toolUse"])

        names = {field["name"] for field in name_fields}
        occupied = set(names)
        aliases = {}
        for name in sorted(names):
            if _TOOL_NAME.fullmatch(name):
                continue
            prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:47]
            counter = 0
            while True:
                source = name if counter == 0 else f"{name}\0{counter}"
                digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
                alias = f"{prefix}_{digest}"
                if alias not in occupied:
                    break
                counter += 1
            occupied.add(alias)
            aliases[name] = alias
            self._original_names[alias] = name

        for field in name_fields:
            field["name"] = aliases.get(field["name"], field["name"])

    def _normalize_gpt_last_turn(self) -> None:
        messages = self.request.get("messages", [])
        # Empty assistant prefills have no content to continue. The converter
        # already removes empty text blocks; remove the empty trailing turn too.
        while (
            messages
            and messages[-1]["role"] == "assistant"
            and not messages[-1].get("content")
        ):
            messages.pop()
        if not messages:
            raise ValidationError("GPT models require a non-empty user message.")
        if messages[-1]["role"] != "assistant":
            return
        if any("toolUse" in block for block in messages[-1]["content"]):
            raise ValidationError(
                "GPT models require a user turn containing tool results after "
                "an assistant tool call. Supply the missing tool results."
            )
        # GPT cannot accept an Anthropic assistant prefill. Preserve that text
        # and request its continuation explicitly; do not discard conversation
        # history or fabricate results for unfinished tool calls.
        messages.append({"role": "user", "content": [{"text": _CONTINUE_RESPONSE}]})

    def restore_response(self, response: dict[str, Any]) -> dict[str, Any]:
        if not self._original_names and not self._is_gpt:
            return response
        restored = deepcopy(response)
        message = restored.get("output", {}).get("message", {})
        if self._is_gpt and "content" in message:
            # Do not expose GPT encrypted reasoning as Claude thinking blocks:
            # these cannot be replayed to Claude after a model switch.
            message["content"] = [
                block for block in message["content"] if "reasoningContent" not in block
            ]
        for block in message.get("content", []):
            if "toolUse" in block:
                self._restore_tool_name(block["toolUse"])
        if self.stop_sequences:
            content = message.get("content", [])
            text = "".join(block.get("text", "") for block in content)
            matches = [
                (text.find(stop), order, stop)
                for order, stop in enumerate(self.stop_sequences)
                if stop in text
            ]
            if matches:
                position, _, matched = min(matches)
                kept = []
                offset = 0
                for block in content:
                    if "text" in block:
                        if offset + len(block["text"]) >= position:
                            prefix = block["text"][: position - offset]
                            if prefix:
                                kept.append({**block, "text": prefix})
                            break
                        offset += len(block["text"])
                    kept.append(block)
                message["content"] = kept
                restored["stopReason"] = "stop_sequence"
                restored["_proxy_stop_sequence"] = matched
        return restored

    def buffered_stream(self, response: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Emulate stops for GPT using a completed response, then emit valid SSE.

        Bedrock GPT rejects server-side stop sequences. Buffering the uncommon
        stop-enabled streaming request prevents leaking a stop marker or later
        tool calls. Token usage remains the provider's actual billed usage.
        """
        response = self.restore_response(response)
        yield {"metadata": {"usage": response.get("usage", {})}}
        yield {"messageStart": {"role": "assistant"}}
        content = response.get("output", {}).get("message", {}).get("content", [])
        for index, block in enumerate(content):
            if "text" in block:
                start = {}
                delta = {"text": block["text"]}
            elif "toolUse" in block:
                tool = block["toolUse"]
                start = {
                    "toolUse": {"toolUseId": tool["toolUseId"], "name": tool["name"]}
                }
                delta = {"toolUse": {"input": json.dumps(tool.get("input", {}))}}
            else:
                raise ValidationError("Unsupported GPT content in stop-enabled stream.")
            yield {"contentBlockStart": {"contentBlockIndex": index, "start": start}}
            yield {"contentBlockDelta": {"contentBlockIndex": index, "delta": delta}}
            yield {"contentBlockStop": {"contentBlockIndex": index}}
        yield {
            "messageStop": {
                "stopReason": response.get("stopReason"),
                "_proxy_stop_sequence": response.get("_proxy_stop_sequence"),
            }
        }

    def restore_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if not self._original_names and not self._is_gpt:
            return event
        restored = deepcopy(event)
        if self._is_gpt:
            for kind in ("contentBlockStart", "contentBlockDelta", "contentBlockStop"):
                if kind not in restored:
                    continue
                data = restored[kind]
                index = data.get("contentBlockIndex", 0)
                content = data.get("start", data.get("delta", {}))
                if "reasoningContent" in content:
                    self._reasoning_indices.add(index)
                if index in self._reasoning_indices:
                    return None
                # An empty start does not identify the content type. Let the
                # service inject its start on the first visible delta instead.
                if kind == "contentBlockStart" and not content:
                    return None
                if kind == "contentBlockStop" and index not in self._visible_indices:
                    return None
                if index not in self._visible_indices:
                    self._visible_indices[index] = len(self._visible_indices)
                data["contentBlockIndex"] = self._visible_indices[index]
        tool = restored.get("contentBlockStart", {}).get("start", {}).get("toolUse")
        if tool is not None:
            self._restore_tool_name(tool)
        return restored

    def _restore_tool_name(self, tool_use: dict[str, Any]) -> None:
        name = tool_use.get("name")
        if name in self._original_names:
            tool_use["name"] = self._original_names[name]
