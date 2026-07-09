# INSTALL_FOR_AGENTS — agentcore-search-mcp

Execute these steps in order; each step lists the command and the expected outcome.
The package is published on PyPI, so the default flow needs **no source checkout** — `uvx` fetches and runs it directly.
Convention: `<GATEWAY_URL>` = the AgentCore Gateway MCP endpoint (`https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`) — obtained in Step 3.

## Step 1 — Verify uv is installed

```bash
uv --version
```

Expected: `uv X.Y.Z`. If "command not found":

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Step 2 — Verify the package runs via uvx

```bash
uvx agentcore-search-mcp --version
```

Expected: `agentcore-search-mcp 0.1.0` (first run downloads the package from PyPI; takes a few seconds).

## Step 3 — Confirm AWS credentials

```bash
aws sts get-caller-identity --query Account --output text
```

Expected: a 12-digit account ID. If it errors, stop and ask the user to configure AWS credentials (the credentials must be able to invoke the gateway).

## Step 4 — Obtain the gateway URL

If the user already has an AgentCore Gateway with a Web Search target, ask for its MCP endpoint URL and use it as `<GATEWAY_URL>`.

If not, deploy one with the bundled script — this is the only step that needs the source checkout (us-east-1 only; requires IAM + `bedrock-agentcore-control` permissions):

```bash
git clone https://github.com/aws-samples/sample-bedrock-api-proxy.git /tmp/bedrock-api-proxy
cd /tmp/bedrock-api-proxy/agentcore-search-mcp && uv sync && bash scripts/deploy_gateway.sh
```

Expected: exit 0; output contains `Gateway URL: https://...amazonaws.com/mcp` — use that value as `<GATEWAY_URL>`. First run takes 1–3 minutes; re-runs reuse existing resources and finish in seconds. If it fails with an IAM/permission error, stop and surface the error to the user.

## Step 5a — Register with Claude Code (CLI form)

```bash
claude mcp add agentcore-search --scope project \
  --env AGENTCORE_GATEWAY_URL=<GATEWAY_URL> \
  --env AGENTCORE_GATEWAY_REGION=us-east-1 \
  -- uvx agentcore-search-mcp
```

Expected: `Added stdio MCP server agentcore-search ...`. Note: `--scope project` writes the config (including `<GATEWAY_URL>`) to `.mcp.json` in the project root, which may be committed to version control — use `--scope local` to keep it in user-level config instead.

## Step 5b — Alternative: `.mcp.json` (project root)

If the CLI form is unavailable, merge this into the project's `.mcp.json`:

```json
{
  "mcpServers": {
    "agentcore-search": {
      "command": "uvx",
      "args": ["agentcore-search-mcp"],
      "env": {
        "AGENTCORE_GATEWAY_URL": "<GATEWAY_URL>",
        "AGENTCORE_GATEWAY_REGION": "us-east-1"
      }
    }
  }
}
```

## Step 6 — Verify registration

```bash
claude mcp list
```

Expected: a line containing `agentcore-search` with status connected (✓). A restart of the client session may be required before the tool appears.

## Step 7 — Functional check

In the client session, invoke the tool: ask to "use the web_search tool to search for AWS Bedrock AgentCore". Expected: a `web_search` tool call returning a `results` array with `url`/`title`/`content` fields. If it errors with HTTP 403, see README.md → Troubleshooting.

## Appendix — Running from a source checkout instead of PyPI

For development or an unpublished modification, replace `uvx agentcore-search-mcp` in Steps 5a/5b with `uv --directory <ABS_PATH_TO/agentcore-search-mcp> run agentcore-search-mcp` (JSON form: `"command": "uv", "args": ["--directory", "<ABS_PATH_TO/agentcore-search-mcp>", "run", "agentcore-search-mcp"]`), after running `uv sync` in that directory. `<ABS_PATH_TO/agentcore-search-mcp>` = absolute path of the checkout (run `pwd` inside it once).
