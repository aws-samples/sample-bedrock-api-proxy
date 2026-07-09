"""SigV4-signed JSON-RPC client for the Amazon Bedrock AgentCore Gateway WebSearch tool.

Ported from the bedrock-api-proxy's AgentCoreSearchProvider so this package stays
fully self-contained. The Gateway speaks MCP (JSON-RPC over HTTPS) but requires
SigV4 request signing (service "bedrock-agentcore"), which MCP clients cannot do
themselves — this client is the signing half of the bridge.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TOOL_NAME = "WebSearch"
SERVICE_NAME = "bedrock-agentcore"
MAX_QUERY_CHARS = 200
MAX_RESULTS_CAP = 25


class GatewayError(RuntimeError):
    """Raised when the AgentCore Gateway returns an error or unusable response."""


class AgentCoreGatewayClient:
    """Async client that signs and forwards JSON-RPC calls to an AgentCore Gateway."""

    def __init__(
        self,
        gateway_url: str,
        region: str = "us-east-1",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not gateway_url:
            raise GatewayError(
                "AgentCore Gateway URL is required. Set AGENTCORE_GATEWAY_URL to your "
                "Gateway MCP endpoint (see README, section 'Configuration')."
            )
        self.gateway_url = gateway_url
        self.region = region
        self.timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        # AgentCore Gateway namespaces MCP tool names as "{target}___{toolName}".
        # The concrete name is discovered at runtime via tools/list and cached here.
        self._resolved_tool_name: str | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy-initialize the httpx client for connection reuse."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout, transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _signed_headers(self, payload: bytes) -> dict[str, str]:
        """Create SigV4 headers for an AgentCore Gateway MCP request."""
        import botocore.session
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        session = botocore.session.get_session()
        credentials = session.get_credentials()
        if credentials is None:
            raise GatewayError(
                "AWS credentials are required to sign AgentCore Gateway requests — "
                "configure a profile (AWS_PROFILE), environment variables, or an instance role."
            )

        request = AWSRequest(
            method="POST",
            url=self.gateway_url,
            data=payload,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        SigV4Auth(credentials.get_frozen_credentials(), SERVICE_NAME, self.region).add_auth(request)
        return dict(request.headers.items())

    @staticmethod
    def _json_from_response_text(text: str) -> dict[str, Any]:
        """Parse direct JSON or an SSE stream carrying JSON-RPC data frames."""
        stripped = text.strip()
        if not stripped:
            raise GatewayError("AgentCore Gateway returned an empty response")

        if stripped.startswith("{"):
            return json.loads(stripped)

        data_frames = []
        for line in stripped.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                value = line.removeprefix("data:").strip()
                if value and value != "[DONE]":
                    data_frames.append(value)

        if not data_frames:
            raise GatewayError("AgentCore Gateway response did not contain JSON data")
        return json.loads(data_frames[-1])

    @staticmethod
    def _extract_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract WebSearch results from a JSON-RPC MCP response."""
        if "error" in payload:
            message = payload.get("error", {}).get("message", "unknown MCP error")
            raise GatewayError(f"AgentCore Gateway web search failed: {message}")

        result = payload.get("result", payload)
        if result.get("isError"):
            raise GatewayError("AgentCore Gateway WebSearch returned an error result")

        for item in result.get("content", []):
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text", "")
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            results = parsed.get("results")
            if isinstance(results, list):
                return [r for r in results if isinstance(r, dict)]

        return []

    async def _post(self, request: dict[str, Any]) -> dict[str, Any]:
        """Sign and POST a JSON-RPC request, returning the parsed response payload."""
        payload = json.dumps(request).encode("utf-8")
        headers = self._signed_headers(payload)
        try:
            response = await self.client.post(self.gateway_url, content=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise GatewayError(f"AgentCore Gateway request failed: {exc}") from exc

        if response.status_code >= 400:
            body_excerpt = response.text[:200]
            raise GatewayError(
                f"AgentCore Gateway returned HTTP {response.status_code}: {body_excerpt}"
            )
        return self._json_from_response_text(response.text)

    async def _list_tools(self) -> list[dict[str, Any]]:
        """Fetch the tools advertised by the AgentCore Gateway via tools/list."""
        data = await self._post(
            {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/list",
                "params": {},
            }
        )
        if "error" in data:
            message = data.get("error", {}).get("message", "unknown MCP error")
            raise GatewayError(f"AgentCore Gateway tools/list failed: {message}")

        result = data.get("result", data)
        tools = result.get("tools", [])
        return [t for t in tools if isinstance(t, dict)]

    async def _resolve_tool_name(self) -> str:
        """Resolve the concrete WebSearch tool name exposed by the Gateway.

        AgentCore Gateway namespaces MCP tool names as ``{target}___{toolName}``
        (e.g. ``web-search-tool___WebSearch``), so the bare ``WebSearch`` name may
        not match. Discover the real name once via tools/list and cache it.
        """
        if self._resolved_tool_name:
            return self._resolved_tool_name

        tools = await self._list_tools()
        names = [t.get("name", "") for t in tools if t.get("name")]

        # Prefer an exact match, then a namespaced "{target}___WebSearch" match,
        # then any tool whose name ends with the expected tool name.
        suffix = f"___{TOOL_NAME}"
        candidates = (
            [n for n in names if n == TOOL_NAME]
            + [n for n in names if n.endswith(suffix)]
            + [n for n in names if n.endswith(TOOL_NAME)]
        )
        for name in candidates:
            self._resolved_tool_name = name
            logger.info("Resolved AgentCore Gateway tool name: %s", name)
            return name

        raise GatewayError(
            f"AgentCore Gateway does not expose a '{TOOL_NAME}' tool. " f"Available tools: {names}"
        )

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, str | None]]:
        """Execute a web search via the AgentCore Gateway WebSearch MCP tool."""
        search_query = query[:MAX_QUERY_CHARS]
        if len(query) > MAX_QUERY_CHARS:
            logger.info("Truncated query from %s to %s characters", len(query), MAX_QUERY_CHARS)

        tool_name = await self._resolve_tool_name()
        data = await self._post(
            {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "query": search_query,
                        "maxResults": max(1, min(max_results, MAX_RESULTS_CAP)),
                    },
                },
            }
        )
        raw_results = self._extract_results(data)
        results: list[dict[str, str | None]] = [
            {
                # AgentCore may return null url/title (e.g. sourceless climate
                # tables); coerce to "" and drop items with no usable source URL.
                "url": item.get("url") or "",
                "title": item.get("title") or "",
                "content": item.get("text") or item.get("content") or item.get("snippet") or "",
                "published_date": item.get("publishedDate") or item.get("page_age"),
            }
            for item in raw_results
            if item.get("url")
        ]

        logger.info("AgentCore WebSearch returned %s usable results", len(results))
        return results[:max_results]
