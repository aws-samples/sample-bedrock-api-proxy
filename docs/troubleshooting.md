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
