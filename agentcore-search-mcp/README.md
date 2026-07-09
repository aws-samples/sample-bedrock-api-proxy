# agentcore-search-mcp

An MCP server that exposes the **Amazon Bedrock AgentCore Gateway WebSearch** tool to any MCP client (Claude Code, Codex, Cursor, or your own agent).

[中文文档 / Chinese documentation](README_ZH.md) · [Agent-executable install steps](INSTALL_FOR_AGENTS.md)

## What is this

AgentCore Gateway already speaks MCP (JSON-RPC over HTTPS) — but it requires **AWS SigV4 request signing** (service `bedrock-agentcore`), which MCP clients cannot do themselves. This server is a local bridge: it accepts plain MCP over stdio and signs every upstream call.

```
┌────────────────┐   stdio (MCP)   ┌──────────────────────┐   SigV4-signed JSON-RPC   ┌───────────────────────┐
│  MCP client    │ ──────────────► │  agentcore-search-mcp │ ────────────────────────► │  AgentCore Gateway    │
│  (Claude Code, │ ◄────────────── │  (this server)        │ ◄──────────────────────── │  WebSearch target     │
│  Codex, ...)   │                 └──────────────────────┘                            └───────────────────────┘
└────────────────┘
```

Exposed tool: `web_search(query, max_results=5)` → `{"results": [{"url", "title", "content", "published_date"}]}`.

## Prerequisites

- **An AgentCore Gateway with a WebSearch target.** No gateway yet? Deploy one with the bundled script (see below). Or create it manually in the AWS console: Amazon Bedrock AgentCore → Gateways → create a gateway and attach the Web Search connector target, then copy the gateway's MCP endpoint URL. Note: the Web Search connector is region-limited (us-east-1 at the time of writing).
- **AWS credentials** via any standard chain (environment variables, `AWS_PROFILE`, or an instance role) with permission to invoke the gateway (e.g. `bedrock-agentcore:InvokeGateway` or your account's equivalent policy for the gateway resource).
- **Python ≥ 3.10** and [uv](https://docs.astral.sh/uv/) (recommended; pip also works).

### Deploy the gateway with the bundled script

```bash
cd agentcore-search-mcp
uv sync                          # once — the script uses the venv's botocore
bash scripts/deploy_gateway.sh
```

The script is **idempotent** (re-runs reuse the existing role/gateway/target by name) and creates three things in your account:

1. IAM service role `agentcore-search-mcp-service-role` (trusts `bedrock-agentcore.amazonaws.com`; inline policy allowing `InvokeGateway` + `InvokeWebSearch`)
2. AgentCore Gateway `agentcore-search-mcp` (MCP protocol, `AWS_IAM` authorizer)
3. Web Search connector target `web-search-tool` (created via the SigV4 control-plane REST API — the connector target shape isn't expressible in the aws CLI yet)

On success it prints the `AGENTCORE_GATEWAY_URL`, a ready-to-run `claude mcp add` command, and a smoke-test one-liner. Names are overridable via `GATEWAY_NAME` / `TARGET_NAME` / `ROLE_NAME` env vars. Requires deploy-time permissions for IAM role creation and `bedrock-agentcore-control` (gateway CRUD) — broader than the runtime invoke-only permission. Teardown is manual (console or `aws bedrock-agentcore-control delete-gateway-target` / `delete-gateway`, then delete the IAM role).

## Installation

The package is published on PyPI ([agentcore-search-mcp](https://pypi.org/project/agentcore-search-mcp/)) — no checkout needed:

```bash
# (a) simplest: run straight from PyPI
uvx agentcore-search-mcp --version

# (b) pip
pip install agentcore-search-mcp
```

Or work from a source checkout of this directory (`agentcore-search-mcp/`):

```bash
# (c) uv project install
uv sync && uv run agentcore-search-mcp --version

# (d) run the checkout ad hoc with uvx
uvx --from /abs/path/to/agentcore-search-mcp agentcore-search-mcp --version
```

Expected output in all cases: `agentcore-search-mcp 0.1.0`. With the PyPI form, client configs below can use `"command": "uvx", "args": ["agentcore-search-mcp"]` instead of the `--directory` form. Release process: [RELEASING.md](RELEASING.md).

## Configuration

All configuration is via environment variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `AGENTCORE_GATEWAY_URL` | yes | — | Gateway MCP endpoint: `https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp` |
| `AGENTCORE_GATEWAY_REGION` | no | `us-east-1` | Region used for SigV4 signing (must match the gateway's region) |
| `AGENTCORE_SEARCH_TIMEOUT` | no | `30` | Upstream HTTP timeout in seconds |
| `AWS_PROFILE` / `AWS_ACCESS_KEY_ID` etc. | yes (any chain) | — | Standard AWS credential chain used for signing |

## Client setup

All examples use the published PyPI package via `uvx` — no checkout or path needed. Running from a source checkout instead? Replace `uvx agentcore-search-mcp` with `uv --directory /abs/path/to/agentcore-search-mcp run agentcore-search-mcp` (after `uv sync` there).

### Claude Code

One-liner (project scope):

```bash
claude mcp add agentcore-search --scope project \
  --env AGENTCORE_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
  --env AGENTCORE_GATEWAY_REGION=us-east-1 \
  -- uvx agentcore-search-mcp
```

Or add to `.mcp.json` (see [`.mcp.json.example`](.mcp.json.example)):

```json
{
  "mcpServers": {
    "agentcore-search": {
      "command": "uvx",
      "args": ["agentcore-search-mcp"],
      "env": {
        "AGENTCORE_GATEWAY_URL": "https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        "AGENTCORE_GATEWAY_REGION": "us-east-1"
      }
    }
  }
}
```

### Codex

```bash
codex mcp add agentcore-search \
  --env AGENTCORE_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
  -- uvx agentcore-search-mcp
```

Or in `~/.codex/config.toml`:

```toml
[mcp_servers.agentcore-search]
command = "uvx"
args = ["agentcore-search-mcp"]
env = { AGENTCORE_GATEWAY_URL = "https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp" }
```

### Cursor

`.cursor/mcp.json` in your project (same JSON shape as the Claude Code `.mcp.json` block above).

### Generic MCP client

Spawn `uvx agentcore-search-mcp` as a stdio subprocess with the env vars set; then `initialize` → `tools/list` → `tools/call web_search`.

## Verify

1. `claude mcp list` (or your client's equivalent) shows `agentcore-search` as connected.
2. In the client, ask: *"Use the web_search tool to find the latest AWS Bedrock announcements."*
3. Direct smoke test without a client:

```bash
AGENTCORE_GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
  uv run python scripts/live_smoke.py
```

## Transports

- **stdio** (default) — what all desktop MCP clients use.
- **streamable-http** — `agentcore-search-mcp --transport streamable-http --port 8900` for shared/remote use. The HTTP listener has **no auth of its own**; bind it to localhost or front it with your own auth layer.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `HTTP 403` from the gateway | Credentials missing/expired, no permission on the gateway resource, or `AGENTCORE_GATEWAY_REGION` doesn't match the gateway's region (SigV4 scope mismatch) |
| `does not expose a 'WebSearch' tool` | Wrong gateway URL, or the Web Search connector target isn't attached to this gateway — the error lists the tools that ARE available |
| Fewer results than `max_results` | Intentional: results without a source URL (`url: null`) are filtered out |
| Timeouts | Raise `AGENTCORE_SEARCH_TIMEOUT`; check network egress to `*.gateway.bedrock-agentcore.<region>.amazonaws.com` |
| `AGENTCORE_GATEWAY_URL is not set` | Set the env var in the client's MCP server config (`env` block), not just your shell |

## Limitations

- Queries are truncated to **200 characters** (gateway limit).
- At most **25 results** per call.
- `allowed_domains` / `blocked_domains` / user-location filtering are not supported by the AgentCore Web Search connector.
