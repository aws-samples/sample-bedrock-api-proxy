"""CLI entry point for the AgentCore WebSearch MCP server."""

from __future__ import annotations

import argparse
import logging
import sys

from agentcore_search_mcp import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentcore-search-mcp",
        description=(
            "MCP server exposing Amazon Bedrock AgentCore Gateway WebSearch. "
            "Requires AGENTCORE_GATEWAY_URL and AWS credentials (standard chain)."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8900,
        help="Port for streamable-http transport (default: 8900)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    # stdout is the protocol channel in stdio mode — logs must go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    from agentcore_search_mcp.server import create_server

    server = create_server(port=args.port)
    if args.transport == "streamable-http":
        server.run(transport="streamable-http")
    else:
        server.run()


if __name__ == "__main__":
    main()
