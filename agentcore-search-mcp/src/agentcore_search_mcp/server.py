"""MCP server exposing AgentCore Gateway WebSearch as a `web_search` tool."""

from __future__ import annotations

import logging
import os
from typing import Any

try:  # mcp >= 2.x renamed FastMCP to MCPServer
    from mcp.server import MCPServer as _Server  # type: ignore[attr-defined]
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from agentcore_search_mcp.gateway import AgentCoreGatewayClient, GatewayError

logger = logging.getLogger(__name__)

_client: AgentCoreGatewayClient | None = None
_client_key: tuple[str, str, float] | None = None


def get_client() -> AgentCoreGatewayClient:
    """Return a gateway client built from env config, reused across tool calls."""
    gateway_url = os.environ.get("AGENTCORE_GATEWAY_URL", "")
    if not gateway_url:
        raise GatewayError(
            "AGENTCORE_GATEWAY_URL is not set. Set it to your AgentCore Gateway MCP "
            "endpoint (https://<gateway-id>.gateway.bedrock-agentcore.<region>."
            "amazonaws.com/mcp) — see README, section 'Configuration'."
        )
    region = os.environ.get("AGENTCORE_GATEWAY_REGION", "us-east-1")
    timeout = float(os.environ.get("AGENTCORE_SEARCH_TIMEOUT", "30"))

    global _client, _client_key
    key = (gateway_url, region, timeout)
    if _client is None or _client_key != key:
        _client = AgentCoreGatewayClient(gateway_url, region=region, timeout=timeout)
        _client_key = key
    return _client


async def run_web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Shared tool implementation (also called directly by tests)."""
    results = await get_client().search(query, max_results=max_results)
    return {"results": results}


def create_server(port: int = 8900) -> Any:
    """Build the MCP server with the `web_search` tool registered."""
    try:
        mcp = _Server("agentcore-search", port=port)
    except TypeError:  # SDK variant without settings kwargs
        mcp = _Server("agentcore-search")

    @mcp.tool()
    async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
        """Search the web via Amazon Bedrock AgentCore Gateway WebSearch.

        Args:
            query: The search query (truncated to 200 characters by the gateway).
            max_results: Number of results to return, 1-25 (default 5).

        Returns:
            {"results": [{"url", "title", "content", "published_date"}, ...]}
        """
        return await run_web_search(query, max_results=max_results)

    return mcp
