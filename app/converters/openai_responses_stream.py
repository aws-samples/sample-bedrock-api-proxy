"""Incremental Responses SSE translation, with no accumulated generated text."""

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.converters.openai_responses_to_anthropic import (
    OpenAIResponsesToAnthropicConverter,
)
from app.core.exceptions import BedrockAPIError


@dataclass
class _Block:
    index: int
    kind: str
    length: int = 0
    closed: bool = False


class OpenAIResponsesStreamConverter:
    """Per-request state for output items, content parts, and terminal usage.

    Only block metadata and emitted character counts are retained. Done events
    can supply a missing suffix without replaying already streamed content.
    Tool identity always comes from call_id, never the output item's id.
    """

    def __init__(self, model: str):
        self.model = model
        self.started = False
        self.terminal = False
        self.has_tool_call = False
        self.blocks: dict[tuple[int, str, int], _Block] = {}
        self.converter = OpenAIResponsesToAnthropicConverter()

    def _start(
        self,
        key: tuple[int, str, int],
        content: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> _Block:
        if key not in self.blocks:
            block = _Block(index=len(self.blocks), kind=content["type"])
            self.blocks[key] = block
            events.append(
                {
                    "type": "content_block_start",
                    "index": block.index,
                    "content_block": content,
                }
            )
        return self.blocks[key]

    @staticmethod
    def _delta(block: _Block, text: str, events: list[dict[str, Any]]) -> None:
        if not text or block.closed:
            return
        delta_type, field = {
            "text": ("text_delta", "text"),
            "thinking": ("thinking_delta", "thinking"),
            "tool_use": ("input_json_delta", "partial_json"),
        }[block.kind]
        events.append(
            {
                "type": "content_block_delta",
                "index": block.index,
                "delta": {"type": delta_type, field: text},
            }
        )
        block.length += len(text)

    @staticmethod
    def _close(block: _Block, events: list[dict[str, Any]]) -> None:
        if not block.closed:
            events.append({"type": "content_block_stop", "index": block.index})
            block.closed = True

    def _text(
        self,
        key: tuple[int, str, int],
        text: str,
        events: list[dict[str, Any]],
        *,
        final: bool = False,
    ) -> None:
        kind = key[1]
        content = (
            {"type": "thinking", "thinking": ""}
            if kind == "thinking"
            else {"type": "text", "text": ""}
        )
        block = self._start(key, content, events)
        self._delta(block, text[block.length :] if final else text, events)
        if final:
            self._close(block, events)

    def _item(
        self,
        output_index: int,
        item: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        final: bool,
    ) -> None:
        kind = item.get("type")
        if kind == "function_call":
            if not item.get("call_id") or not item.get("name"):
                raise BedrockAPIError(
                    "invalid_response", "Function call is missing call_id or name"
                )
            self.has_tool_call = True
            block = self._start(
                (output_index, "tool_use", 0),
                {
                    "type": "tool_use",
                    "id": item["call_id"],
                    "name": item["name"],
                    "input": {},
                },
                events,
            )
            arguments = item.get("arguments") or ""
            self._delta(block, arguments[block.length :], events)
            if final:
                self._close(block, events)
        elif kind in ("message", "reasoning"):
            parts = item.get("content" if kind == "message" else "summary") or []
            for index, part in enumerate(parts):
                self._part(output_index, index, part, events, final=final)

    def _part(
        self,
        output_index: int,
        index: int,
        part: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        final: bool,
    ) -> None:
        kind = part.get("type")
        if kind in ("output_text", "summary_text", "refusal"):
            self._text(
                (
                    output_index,
                    "thinking" if kind == "summary_text" else "text",
                    index,
                ),
                part.get("refusal" if kind == "refusal" else "text") or "",
                events,
                final=final,
            )

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """Consume one SDK event and return immediately available Messages events."""
        if self.terminal:
            return []
        event_type = event.get("type", "")
        response = event.get("response") or {}
        if event_type in ("error", "response.failed", "response.error"):
            error = response.get("error") or event.get("error") or event
            raise BedrockAPIError(
                error.get("code") or "api_error",
                error.get("message") or "Responses stream failed",
            )

        events: list[dict[str, Any]] = []
        if not self.started:
            self.started = True
            events.append(
                {
                    "type": "message_start",
                    "message": {
                        "id": response.get("id") or f"msg_{uuid4().hex[:24]}",
                        "type": "message",
                        "role": "assistant",
                        "model": self.model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": self.converter.convert_usage(
                            response.get("usage") or {}
                        ).model_dump(exclude_none=True),
                    },
                }
            )

        output_index = event.get("output_index", 0)
        if event_type in ("response.output_item.added", "response.output_item.done"):
            self._item(
                output_index,
                event.get("item") or {},
                events,
                final=event_type.endswith(".done"),
            )
        elif event_type in (
            "response.content_part.added",
            "response.content_part.done",
            "response.reasoning_summary_part.added",
            "response.reasoning_summary_part.done",
        ):
            self._part(
                output_index,
                event.get("summary_index", event.get("content_index", 0)),
                event.get("part") or {},
                events,
                final=event_type.endswith(".done"),
            )
        elif event_type in (
            "response.output_text.delta",
            "response.output_text.done",
            "response.reasoning_summary_text.delta",
            "response.reasoning_summary_text.done",
            "response.refusal.delta",
            "response.refusal.done",
        ):
            final = event_type.endswith(".done")
            thinking = "reasoning_summary_text" in event_type
            self._text(
                (
                    output_index,
                    "thinking" if thinking else "text",
                    event.get("summary_index", event.get("content_index", 0)),
                ),
                event.get(
                    ("refusal" if "refusal" in event_type else "text")
                    if final
                    else "delta"
                )
                or "",
                events,
                final=final,
            )
        elif event_type in (
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        ):
            block = self.blocks.get((output_index, "tool_use", 0))
            if block is None:
                raise BedrockAPIError(
                    "invalid_response",
                    "Function arguments arrived before call identity",
                )
            if event_type.endswith(".done"):
                self._delta(
                    block, (event.get("arguments") or "")[block.length :], events
                )
                self._close(block, events)
            else:
                self._delta(block, event.get("delta") or "", events)
        elif event_type in ("response.completed", "response.incomplete"):
            # Event type remains authoritative for endpoints omitting status.
            response = {**response, "status": event_type.removeprefix("response.")}
            self.converter.validate_response(response)
            for index, item in enumerate(response.get("output") or []):
                self._item(index, item, events, final=True)
            stop_reason = self.converter.stop_reason(response, self.has_tool_call)
            for block in self.blocks.values():
                self._close(block, events)
            events.extend(
                [
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": stop_reason,
                            "stop_sequence": None,
                        },
                        "usage": self.converter.convert_usage(
                            response.get("usage") or {}
                        ).model_dump(exclude_none=True),
                    },
                    {"type": "message_stop"},
                ]
            )
            self.terminal = True
        return events
