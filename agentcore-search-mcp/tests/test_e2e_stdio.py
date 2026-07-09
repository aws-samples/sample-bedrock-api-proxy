"""End-to-end test: real stdio MCP server process against a local fake AgentCore gateway."""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

NAMESPACED = "web-search-tool___WebSearch"

CANNED_RESULTS = [
    {
        "url": "https://example.com/e2e",
        "title": "E2E",
        "text": "end to end",
        "publishedDate": "2026-07-09",
    },
    {"url": None, "title": "sourceless", "text": "must be filtered"},
]


class FakeGatewayHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        if body["method"] == "tools/list":
            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"tools": [{"name": NAMESPACED}]},
            }
        else:
            payload = {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"results": CANNED_RESULTS})}]
                },
            }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence request logging."""


@pytest.fixture()
def fake_gateway_url() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), FakeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/mcp"
    server.shutdown()


async def test_stdio_round_trip(fake_gateway_url: str) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentcore_search_mcp"],
        env={
            "PATH": os.environ.get("PATH", ""),
            "AGENTCORE_GATEWAY_URL": fake_gateway_url,
            "AGENTCORE_GATEWAY_REGION": "us-east-1",
            # Dummy static credentials so SigV4 signing works offline.
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool = next(t for t in tools.tools if t.name == "web_search")
            schema = tool.inputSchema
            assert "query" in schema["properties"]
            assert "query" in schema.get("required", [])
            assert "max_results" in schema["properties"]

            result = await session.call_tool("web_search", {"query": "e2e check"})
            assert not result.isError
            payload = result.structuredContent or json.loads(result.content[0].text)  # type: ignore[union-attr]
            urls = [r["url"] for r in payload["results"]]
            # The null-url canned item must have been filtered by the bridge.
            assert urls == ["https://example.com/e2e"]
            assert payload["results"][0]["published_date"] == "2026-07-09"
