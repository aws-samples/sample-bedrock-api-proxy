"""Unit tests for the MCP server layer."""

from __future__ import annotations

from typing import Any

import pytest

import agentcore_search_mcp.server as server_mod
from agentcore_search_mcp.gateway import GatewayError
from agentcore_search_mcp.server import run_web_search


class FakeGatewayClient:
    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        return [
            {"url": "https://example.com", "title": "T", "content": "c", "published_date": None}
        ]


async def test_web_search_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "get_client", lambda: FakeGatewayClient())
    result = await run_web_search("query", max_results=3)
    assert result == {
        "results": [
            {"url": "https://example.com", "title": "T", "content": "c", "published_date": None}
        ]
    }


async def test_missing_gateway_url_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTCORE_GATEWAY_URL", raising=False)
    monkeypatch.setattr(server_mod, "_client", None)
    monkeypatch.setattr(server_mod, "_client_key", None)
    with pytest.raises(GatewayError, match="AGENTCORE_GATEWAY_URL"):
        await run_web_search("query")
