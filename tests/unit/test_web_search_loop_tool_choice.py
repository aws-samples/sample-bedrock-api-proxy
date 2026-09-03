"""
Regression tests for the web search agentic loop's tool_choice handling.

A client may force the web_search tool with
`tool_choice={"type": "tool", "name": "web_search"}` (Claude Code does this on
the internal query behind its WebSearch tool). The proxy must honour that on the
first turn only — replaying it on every continuation forces another search each
turn, so the loop can never reach `end_turn` and runs until MAX_ITERATIONS.
"""
from typing import Any, Dict, List

import pytest

from app.schemas.anthropic import MessageRequest, MessageResponse, Usage
from app.services.web_search import SearchResult
from app.services.web_search_service import WebSearchService


class _RecordingBedrock:
    """Fake bedrock_service that records the tool_choice of every iteration.

    Returns a `web_search` tool_use for the first `tool_use_turns` calls, then a
    plain text answer (`end_turn`).
    """

    def __init__(self, tool_use_turns: int = 1):
        self.tool_use_turns = tool_use_turns
        self.tool_choices: List[Any] = []

    async def invoke_model(self, request: MessageRequest, **kwargs: Any) -> MessageResponse:
        self.tool_choices.append(request.tool_choice)
        turn = len(self.tool_choices)

        if turn <= self.tool_use_turns:
            content: List[Dict[str, Any]] = [{
                "type": "tool_use",
                "id": f"toolu_{turn}",
                "name": "web_search",
                "input": {"query": "anything"},
            }]
            stop_reason = "tool_use"
        else:
            content = [{"type": "text", "text": "done [1]."}]
            stop_reason = "end_turn"

        return MessageResponse(
            id=f"msg_{turn}",
            content=content,  # type: ignore[arg-type]
            model=request.model,
            stop_reason=stop_reason,
            usage=Usage(input_tokens=10, output_tokens=5),
        )


@pytest.fixture
def service(monkeypatch):
    svc = WebSearchService()

    async def _fake_search(query, config):
        return [SearchResult(title="t", url="https://example.com", content="c")]

    monkeypatch.setattr(svc, "_execute_search", _fake_search)
    return svc


def _request(tool_choice: Any) -> MessageRequest:
    return MessageRequest(
        model="claude-sonnet-4-5",
        max_tokens=256,
        messages=[{"role": "user", "content": "search please"}],
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        tool_choice=tool_choice,
    )


@pytest.mark.asyncio
async def test_forced_tool_choice_is_only_applied_to_the_first_turn(service):
    bedrock = _RecordingBedrock(tool_use_turns=1)

    response = await service.handle_request(
        request=_request({"type": "tool", "name": "web_search"}),
        bedrock_service=bedrock,
        request_id="req-1",
        service_tier="standard",
        anthropic_beta=None,
    )

    assert bedrock.tool_choices == [
        {"type": "tool", "name": "web_search"},  # first turn: client intent honoured
        {"type": "auto"},                        # continuation: relaxed
    ]
    assert response.stop_reason == "end_turn"
    assert response.usage.server_tool_use == {"web_search_requests": 1}


@pytest.mark.asyncio
async def test_auto_tool_choice_is_passed_through_unchanged(service):
    bedrock = _RecordingBedrock(tool_use_turns=1)

    await service.handle_request(
        request=_request({"type": "auto"}),
        bedrock_service=bedrock,
        request_id="req-2",
        service_tier="standard",
        anthropic_beta=None,
    )

    assert bedrock.tool_choices == [{"type": "auto"}, {"type": "auto"}]


@pytest.mark.asyncio
async def test_streaming_loop_also_relaxes_forced_tool_choice(service):
    bedrock = _RecordingBedrock(tool_use_turns=1)

    events = [
        event
        async for event in service.handle_request_streaming(
            request=_request({"type": "tool", "name": "web_search"}),
            bedrock_service=bedrock,
            request_id="req-3",
            service_tier="standard",
            anthropic_beta=None,
        )
    ]

    assert bedrock.tool_choices == [
        {"type": "tool", "name": "web_search"},
        {"type": "auto"},
    ]
    assert any("message_stop" in e for e in events)
