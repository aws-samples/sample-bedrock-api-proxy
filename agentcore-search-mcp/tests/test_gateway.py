"""Unit tests for AgentCoreGatewayClient — no network, no AWS credentials needed."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from agentcore_search_mcp.gateway import AgentCoreGatewayClient, GatewayError

GATEWAY_URL = "https://gw-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
NAMESPACED = "web-search-tool___WebSearch"


def unsigned(client: AgentCoreGatewayClient) -> AgentCoreGatewayClient:
    """Bypass SigV4 so tests need no AWS credentials."""
    client._signed_headers = lambda payload: {"Content-Type": "application/json"}  # type: ignore[method-assign]
    return client


def tools_list_result(names: list[str]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {"tools": [{"name": n} for n in names]},
    }


def tools_call_result(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": "2",
        "result": {"content": [{"type": "text", "text": json.dumps({"results": results})}]},
    }


SAMPLE_RESULTS = [
    {"url": "https://example.com/a", "title": "A", "text": "alpha", "publishedDate": "2026-01-01"},
    {"url": "https://example.com/b", "title": "B", "snippet": "beta"},
]


def make_client(
    responder: Callable[[httpx.Request, dict[str, Any]], httpx.Response],
    calls: list[dict[str, Any]] | None = None,
) -> AgentCoreGatewayClient:
    """Client wired to a MockTransport; `responder` maps (request, body) → response."""
    recorded = calls if calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        recorded.append(body)
        return responder(request, body)

    client = AgentCoreGatewayClient(GATEWAY_URL, transport=httpx.MockTransport(handler))
    return unsigned(client)


def default_responder(
    names: list[str], results: list[dict[str, Any]]
) -> Callable[..., httpx.Response]:
    def responder(request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if body["method"] == "tools/list":
            return httpx.Response(200, json=tools_list_result(names))
        return httpx.Response(200, json=tools_call_result(results))

    return responder


async def test_search_happy_path_direct_json() -> None:
    client = make_client(default_responder([NAMESPACED], SAMPLE_RESULTS))
    results = await client.search("hello")
    assert results == [
        {
            "url": "https://example.com/a",
            "title": "A",
            "content": "alpha",
            "published_date": "2026-01-01",
        },
        {"url": "https://example.com/b", "title": "B", "content": "beta", "published_date": None},
    ]


async def test_search_happy_path_sse_response() -> None:
    def responder(request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        payload = (
            tools_list_result([NAMESPACED])
            if body["method"] == "tools/list"
            else tools_call_result(SAMPLE_RESULTS)
        )
        sse = f"event: message\ndata: {json.dumps(payload)}\n\ndata: [DONE]\n"
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    client = make_client(responder)
    results = await client.search("hello")
    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/a"


async def test_namespaced_tool_name_resolved_and_used() -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(default_responder([NAMESPACED, "other___Thing"], SAMPLE_RESULTS), calls)
    await client.search("q")
    call = next(c for c in calls if c["method"] == "tools/call")
    assert call["params"]["name"] == NAMESPACED


async def test_exact_tool_name_preferred_over_suffixed() -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(default_responder([NAMESPACED, "WebSearch"], SAMPLE_RESULTS), calls)
    await client.search("q")
    call = next(c for c in calls if c["method"] == "tools/call")
    assert call["params"]["name"] == "WebSearch"


async def test_no_matching_tool_raises_with_available_names() -> None:
    client = make_client(default_responder(["foo___Bar"], []))
    with pytest.raises(GatewayError, match="does not expose.*foo___Bar"):
        await client.search("q")


async def test_drops_results_with_null_url() -> None:
    mixed = [
        {"url": None, "title": "sourceless table", "text": "climate data"},
        {"url": "https://example.com/keep", "title": "Keep", "text": "kept"},
        {"title": "missing url entirely", "text": "also dropped"},
    ]
    client = make_client(default_responder([NAMESPACED], mixed))
    results = await client.search("q")
    assert [r["url"] for r in results] == ["https://example.com/keep"]


async def test_null_title_coerced_to_empty_string() -> None:
    client = make_client(
        default_responder([NAMESPACED], [{"url": "https://x.com", "title": None, "text": "t"}])
    )
    results = await client.search("q")
    assert results[0]["title"] == ""


async def test_jsonrpc_error_raised_with_message() -> None:
    def responder(request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if body["method"] == "tools/list":
            return httpx.Response(200, json=tools_list_result([NAMESPACED]))
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": "2", "error": {"message": "quota exceeded"}}
        )

    client = make_client(responder)
    with pytest.raises(GatewayError, match="quota exceeded"):
        await client.search("q")


async def test_is_error_result_raised() -> None:
    def responder(request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if body["method"] == "tools/list":
            return httpx.Response(200, json=tools_list_result([NAMESPACED]))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "2", "result": {"isError": True}})

    client = make_client(responder)
    with pytest.raises(GatewayError, match="error result"):
        await client.search("q")


async def test_query_truncated_to_200_chars() -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(default_responder([NAMESPACED], SAMPLE_RESULTS), calls)
    await client.search("x" * 300)
    call = next(c for c in calls if c["method"] == "tools/call")
    assert call["params"]["arguments"]["query"] == "x" * 200


async def test_max_results_clamped_low_and_high() -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(default_responder([NAMESPACED], SAMPLE_RESULTS), calls)
    await client.search("q", max_results=0)
    await client.search("q", max_results=100)
    tool_calls = [c for c in calls if c["method"] == "tools/call"]
    assert tool_calls[0]["params"]["arguments"]["maxResults"] == 1
    assert tool_calls[1]["params"]["arguments"]["maxResults"] == 25


async def test_http_403_surfaced_with_status() -> None:
    def responder(request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(403, text="Forbidden: signature mismatch")

    client = make_client(responder)
    with pytest.raises(GatewayError, match="403"):
        await client.search("q")


async def test_empty_response_body_raises() -> None:
    def responder(request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        if body["method"] == "tools/list":
            return httpx.Response(200, json=tools_list_result([NAMESPACED]))
        return httpx.Response(200, text="")

    client = make_client(responder)
    with pytest.raises(GatewayError, match="empty response"):
        await client.search("q")


async def test_resolved_tool_name_cached_across_searches() -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(default_responder([NAMESPACED], SAMPLE_RESULTS), calls)
    await client.search("first")
    await client.search("second")
    list_calls = [c for c in calls if c["method"] == "tools/list"]
    assert len(list_calls) == 1
