# Troubleshooting Guide

## Health Endpoints

- `GET /health` - Basic health check
- `GET /ready` - Readiness check
- `GET /liveness` - Liveness check
- `GET /health/ptc` - PTC/Docker status
- `GET /health/web-search` - Web search provider status
- `GET /health/web-fetch` - Web fetch status

## Common Issues

### "Rate limit exceeded" Errors

- Check token bucket configuration: `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW`
- Verify API key's custom rate limit in DynamoDB
- Rate limits reset based on time window, not calendar time

### "Invalid API key" Errors

- Ensure DynamoDB tables are created: `python scripts/setup_tables.py`
- Verify API key exists: Check `anthropic-proxy-api-keys` table
- Check `is_active` flag is `True`
- Master key bypasses validation (for admin use): Set `MASTER_API_KEY` in `.env`

### Conversion Errors

- Most common: Missing fields in Pydantic models
- Check that new Anthropic features are mapped in converters
- Verify model ID mapping exists (or allow passthrough)

### Claude Code with GPT through Bedrock Converse

For deployments using `ENABLE_BEDROCK_RESPONSES=False` and
`ENABLE_OPENAI_COMPAT=False`, non-Claude requests to `/v1/messages` use
Converse/ConverseStream. Scoped non-Claude models otherwise default to Runtime
Responses, whose conversion path is separate. `ENABLE_OPENAI_PASSTHROUGH`
controls the separate `/openai/v1/*` routes and does not change this path.

The Converse request adapter handles these differences automatically:

- **Inline system messages from Claude Code's mid-conversation-system beta:**
  moved into Converse's top-level `system` field in their original order,
  following any existing system instructions. They retain system authority;
  user and assistant messages keep their order. Non-text inline system content
  is rejected instead of being silently dropped. Non-empty inline system
  messages retain their native format on the Claude API path. System messages
  containing `tool_addition`/`tool_removal` retain the existing InvokeModel-only
  handling and are still omitted on the Converse path.
- **MCP tool names longer than 64 characters or containing unsupported characters:**
  converted to deterministic, collision-checked aliases. Tool definitions,
  historical tool calls and explicit tool choices use the same alias. Both
  regular and streaming replies restore the original client tool name. Tool IDs,
  arguments and results are preserved; mappings are isolated per request.
- **Empty tool descriptions:** replaced with a description derived from the tool
  name, satisfying Bedrock's minimum length requirement.
- **Images returned by tools such as Claude Code `Read`:** GPT Converse rejects
  images nested inside `toolResult.content`, even when the model accepts direct
  user images. Images are moved immediately after their tool result within the
  same user turn. Attachment references preserve the association with each
  `toolUseId`; image bytes, formats, result status and other result content are
  retained. This does not add vision support to a text-only model.
- **GPT assistant prefills:** an empty trailing assistant turn is removed. For a
  non-empty assistant prefill, its content is retained and a user instruction
  requests only the continuation. This is a semantic approximation of Anthropic
  prefilling, not a guarantee of an exact text or JSON prefix. Other model
  providers retain their existing message behavior.
- **Missing tool results:** a GPT request ending with an assistant tool call is
  rejected with `invalid_request_error`; the caller must supply the tool results.
  The proxy does not synthesize tool execution results.
- **SDK parameter validation failures:** returned as HTTP 400 for regular
  requests, or an `invalid_request_error` SSE event after streaming starts,
  instead of a retryable internal error.
- **Reasoning history when switching models:** GPT requests omit provider-specific
  historical reasoning blocks while retaining text, tool calls and tool results.
  GPT reasoning metadata is not exposed as Claude thinking in replies; streaming
  content indices remain contiguous. Claude requests omit empty unsigned thinking
  placeholders, but preserve valid signature-only Fable thinking blocks.
- **Empty system messages after compaction/resume:** empty inline system turns
  are omitted on the native Claude path. Non-empty instructions are retained.
- **GPT `stopSequences` errors (including auto-mode classifier requests):** stop
  sequences are enforced by the proxy instead of sent to Bedrock GPT. The reply
  is cut before the first matching sequence, with `stop_reason=stop_sequence`
  and the matched `stop_sequence`. Subsequent content/tool calls are not returned.
  Stop-enabled GPT streams buffer the provider response before emitting SSE, so
  their first content arrives later; usage includes the full provider generation.
  Streams without stops remain incremental. Classifier prompts, rules and
  decisions are not overridden.

Use a proxy alias such as `claude-opus-5[1m]` rather than sending that alias
directly as a Bedrock inference-profile ID. Default mappings are maintained
in the `model-mappings/` submodule and refreshed by the model mapping sync
service. Check the active mapping or add a deployment override when an alias
is missing. The `[1m]` suffix selects the same profile and still relies on the
client's context-window beta header.

These fixes require rebuilding and deploying the proxy image. Changing a model
mapping or restarting Claude Code alone does not update server code.

### Streaming Cuts Off Early

- Check `STREAMING_TIMEOUT` setting
- Verify client keeps connection alive
- Look for exceptions in `invoke_model_stream()` generator

### AWS Credentials Issues

- For local development: Use AWS CLI credentials or environment variables
- For ECS/Lambda: Use IAM roles (preferred)
- Required permissions: `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `dynamodb:*`

### PTC / Docker Issues

- **"Docker not available" error**: Ensure Docker daemon is running (`docker ps`)
- **Container timeout**: Increase `PTC_EXECUTION_TIMEOUT` or check for infinite loops
- **Session expired**: Sessions timeout after 4.5 minutes; use `container.id` for reuse
- **Missing sandbox image**: Pull image manually: `docker pull python:3.11-slim`
- **Permission denied**: Ensure user has Docker socket access (`/var/run/docker.sock`)
- **Health check failing**: Check `/health/ptc` endpoint for detailed status
- **Tool calls not returning**: Verify client sends `tool_result` back to continue execution

## Docker-in-Docker (DinD) Bind Mount Issue

**Problem**: When the proxy runs inside a Docker container (e.g., ECS EC2 with Docker socket mount), PTC sandbox containers fail with "Container failed to become ready" because bind mounts don't work correctly.

**Root Cause**: Docker bind mounts resolve paths from the **Docker daemon's perspective** (the host), not from inside the container making the API call.

```
┌─────────────────────────────────────────────────────────────────┐
│ EC2 Host                                                        │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Proxy Container (ECS Task)                               │   │
│  │                                                          │   │
│  │  tempfile.mkdtemp() creates /tmp/ptc_sandbox_xxx         │   │
│  │  File written: /tmp/ptc_sandbox_xxx/runner.py  ← EXISTS  │   │
│  │                                                          │   │
│  │  Docker API call: volumes={"/tmp/ptc_sandbox_xxx": ...}  │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  Docker daemon receives path "/tmp/ptc_sandbox_xxx"             │
│  Looks for it on HOST filesystem ← DOESN'T EXIST (or empty)    │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Sandbox Container                                        │   │
│  │  /sandbox/ is EMPTY - runner.py not found!               │   │
│  │  python /sandbox/runner.py → fails immediately           │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Solution**: Use Docker's `put_archive` API to copy files directly into the container instead of bind mounts. This works regardless of where the proxy runs.

**How to verify the issue** (via SSM on ECS EC2 instance):
```bash
# Check if temp dirs exist on host vs inside proxy container
echo "=== Host /tmp ===" && ls -la /tmp/ | grep ptc
echo "=== Inside proxy container /tmp ===" && docker exec <proxy_container_id> ls -la /tmp/ | grep ptc

# Test bind mount behavior
docker exec <proxy_container_id> sh -c "mkdir -p /tmp/test && echo content > /tmp/test/file.txt"
docker run --rm -v /tmp/test:/test python:3.11-slim cat /test/file.txt  # Will fail - empty!
```

**Key code changes** (`app/services/ptc/sandbox.py`):
- Removed bind mount volumes from container config
- Added `_copy_file_to_container()` method using `put_archive`
- Changed runner path from `/sandbox/runner.py` to `/tmp/runner.py`
- Removed `read_only=True` (incompatible with `put_archive`)

**Security maintained via**:
- `network_disabled=True` - No network access
- `security_opt=["no-new-privileges"]` - Prevent privilege escalation
- `cap_drop=["ALL"]` - Drop all Linux capabilities
- Memory and CPU limits

## Debugging Tips

### Enable Debug Logging

Set `LOG_LEVEL=DEBUG` in `.env`. Look for `Converting Anthropic request` and `Converting Bedrock response` messages.

### Inspect Raw Bedrock Requests/Responses

Add logging in `app/services/bedrock_service.py`.

### Test Converters Directly

```python
from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter
from app.schemas.anthropic import MessageRequest

converter = AnthropicToBedrockConverter()
bedrock_request = converter.convert_request(your_request)
print(bedrock_request)
```
