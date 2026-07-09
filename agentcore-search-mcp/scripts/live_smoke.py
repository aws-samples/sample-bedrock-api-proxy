"""Live smoke test against a real AgentCore Gateway.

Prints `LIVE_SMOKE: OK (...)` on success, `LIVE_SMOKE: SKIPPED (...)` when no
gateway is configured. Requires real AWS credentials when it runs.
"""

from __future__ import annotations

import asyncio
import os


def main() -> None:
    url = os.environ.get("AGENTCORE_GATEWAY_URL")
    if not url:
        print("LIVE_SMOKE: SKIPPED (no AGENTCORE_GATEWAY_URL)")
        return

    from agentcore_search_mcp.gateway import AgentCoreGatewayClient

    async def run() -> None:
        client = AgentCoreGatewayClient(
            url or "", region=os.environ.get("AGENTCORE_GATEWAY_REGION", "us-east-1")
        )
        try:
            results = await client.search("latest AWS Bedrock announcements", max_results=3)
            first = results[0]["url"] if results else "n/a"
            print(f"LIVE_SMOKE: OK ({len(results)} results; first: {first})")
        finally:
            await client.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
