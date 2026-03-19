# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Anthropic-Bedrock API Proxy** — a FastAPI service that translates between the Anthropic Messages API format and AWS Bedrock's APIs. Clients using the Anthropic Python SDK can seamlessly access any Bedrock model.

**Key Insight**: Bidirectional translation middleware. Requests: Anthropic format → Bedrock format → Bedrock API → Bedrock response → Anthropic format.

## Development Setup

```bash
# Install
uv sync                    # or: pip install -e ".[dev]"
cp .env.example .env       # configure AWS credentials + settings

# Setup
uv run scripts/setup_tables.py
uv run scripts/create_api_key.py --user-id dev-user --name "Development Key"

# Run
uv run uvicorn app.main:app --reload                           # dev
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 -w 4   # prod
docker-compose up -d                                            # full stack

# Test
uv run pytest                                    # all tests
uv run pytest --cov=app --cov-report=html        # with coverage
uv run pytest -m integration                     # integration only

# Code quality
black app tests && ruff check app tests && mypy app
```

## Architecture

### Dual API Mode

- **InvokeModel API** (Claude models): Native Anthropic format, minimal conversion, full beta feature support
- **Converse API** (non-Claude models): Requires format conversion, unified API for all Bedrock models
- **OpenAI Chat Completions API** (non-Claude models, optional): When `ENABLE_OPENAI_COMPAT=True`, non-Claude models use Bedrock's OpenAI-compatible endpoint via bedrock-mantle instead of Converse API

**Routing**: If model ID contains "anthropic" or "claude" → InvokeModel; else if `ENABLE_OPENAI_COMPAT` → OpenAI Chat Completions; else → Converse.

> **Detailed conversion flows, content block mapping, and streaming implementation**: see [docs/architecture/detailed-flows.md](docs/architecture/detailed-flows.md)

### Configuration

All config in `app/core/config.py` (Pydantic Settings, loads from env vars / `.env`). When adding new features, add corresponding feature flags and config options.

### DynamoDB Tables

| Table | Purpose |
|-------|---------|
| `anthropic-proxy-api-keys` | API keys, budgets, rate limits |
| `anthropic-proxy-usage` | Per-request usage logs |
| `anthropic-proxy-usage-stats` | Aggregated token counts |
| `anthropic-proxy-model-pricing` | Model pricing data |
| `anthropic-proxy-model-mapping` | Anthropic → Bedrock model ID mapping |

> **Full schema, budget computation, and aggregation details**: see [docs/architecture/detailed-flows.md](docs/architecture/detailed-flows.md)

## Project Structure

```
app/
├── api/              # Route handlers (thin, delegates to services)
├── converters/       # Core Anthropic↔Bedrock conversion logic
├── core/             # Configuration, logging, metrics
├── db/               # DynamoDB client and managers
├── middleware/       # Auth and rate limiting
├── schemas/          # Pydantic models (anthropic.py, bedrock.py, web_search.py, web_fetch.py, ptc.py)
├── services/         # Business logic, Bedrock calls, PTC, web search/fetch
└── tracing/          # OpenTelemetry distributed tracing
admin_portal/
├── backend/          # Separate FastAPI app (auth, api_keys, pricing, dashboard, model_mapping)
└── frontend/         # Static frontend (served at /admin/ in production)
```

## Key Files

1. `app/converters/anthropic_to_bedrock.py` — Request conversion
2. `app/converters/bedrock_to_anthropic.py` — Response conversion
3. `app/services/bedrock_service.py` — Bedrock API calls (InvokeModel + Converse)
4. `app/api/messages.py` — Main API endpoint handler
5. `app/core/config.py` — Configuration and settings
6. `app/services/ptc_service.py` — PTC orchestration
7. `app/services/web_search_service.py` — Web search agentic loop
8. `app/services/web_fetch_service.py` — Web fetch agentic loop
9. `app/tracing/provider.py` — OpenTelemetry provider
10. `admin_portal/backend/main.py` — Admin portal backend

## Features

Each feature has detailed docs in [docs/architecture/features.md](docs/architecture/features.md):

- **Programmatic Tool Calling (PTC)**: Docker sandbox code execution with client-side tool calls. Requires Docker + EC2 launch type on ECS.
- **Web Search**: Proxy-side `web_search_20250305`/`web_search_20260209` via Tavily or Brave. Agentic loop (up to 25 iterations).
- **Web Fetch**: Proxy-side `web_fetch_20250910`/`web_fetch_20260209` via httpx (no API key needed).
- **Beta Header Mapping**: Maps Anthropic beta headers → Bedrock beta headers for supported models.
- **Tool Input Examples**: `input_examples` param on tool definitions, passed via `additionalModelRequestFields`.
- **Cache TTL**: Extends `cache_control` with configurable TTL (5m or 1h). Priority: API key → request → env → default.
- **OpenTelemetry Tracing**: OTEL GenAI semantic conventions, session-based trace grouping. Zero overhead when disabled.
- **Admin Portal**: Separate FastAPI app for API key/usage/pricing management with Cognito auth.
- **OpenAI-Compatible API**: Non-Claude models can optionally use Bedrock's OpenAI Chat Completions API via bedrock-mantle endpoint instead of Converse API. Controlled by `ENABLE_OPENAI_COMPAT` flag. Maps `thinking` to OpenAI `reasoning` with configurable effort thresholds.

## Common Development Tasks

### Adding a New Anthropic Feature

1. Update Pydantic schemas (`app/schemas/anthropic.py`, `app/schemas/bedrock.py`)
2. Update request converter (`app/converters/anthropic_to_bedrock.py`)
3. Update response converter (`app/converters/bedrock_to_anthropic.py`)
4. Add tests (`tests/unit/test_converters.py`)
5. Add feature flag if optional

### Adding a New Model Mapping

```python
from app.db.dynamodb import DynamoDBClient
client = DynamoDBClient()
client.model_mapping_manager.set_mapping(
    anthropic_model_id="claude-sonnet-4-5-20250929",
    bedrock_model_id='global.anthropic.claude-sonnet-4-5-20250929-v1:0'
)
```

Or update `DEFAULT_MODEL_MAPPING` in `app/core/config.py`.

### Streaming

SSE format: `event: <type>\ndata: <json>\n\n`. Uses FastAPI `StreamingResponse`. See `app/api/messages.py` → `create_message()` streaming branch and `app/services/bedrock_service.py` → `invoke_model_stream()`.

## AWS Deployment (CDK)

```bash
cd cdk
./scripts/deploy.sh -e dev -p arm64           # Fargate (default)
./scripts/deploy.sh -e dev -p arm64 -l ec2    # EC2 (for PTC/Docker)
./scripts/deploy.sh -e prod -p amd64 -r us-east-1
```

Key CDK files: `cdk/config/config.ts`, `cdk/lib/ecs-stack.ts`, `cdk/scripts/deploy.sh`

| Feature | Fargate | EC2 |
|---------|---------|-----|
| PTC Support | No | Yes |
| Management | Serverless | Some (ASG, AMI) |
| Dev instances | — | Spot for cost savings |

## Design Decisions

- **Sync boto3**: DynamoDB ops are fast enough (<10ms) that sync calls don't bottleneck. Avoids aioboto3 complexity.
- **Token bucket rate limiting**: Allows burst traffic while maintaining average rate limits. Per-key, in-memory.
- **DynamoDB over Redis**: Persistence, serverless-friendly, single-region, native AWS integration.
- **Bedrock-specific passthrough**: Optional params (e.g., guardrails) pass through without breaking Anthropic SDK compatibility.

## Environment Variables

**Required:**
- `AWS_REGION` — AWS region for Bedrock and DynamoDB
- `MASTER_API_KEY` — Master key for admin access (or `REQUIRE_API_KEY=False` for dev)

**Feature Flags:** `ENABLE_TOOL_USE`, `ENABLE_EXTENDED_THINKING`, `ENABLE_DOCUMENT_SUPPORT`, `ENABLE_PROGRAMMATIC_TOOL_CALLING`, `ENABLE_WEB_SEARCH`, `ENABLE_WEB_FETCH`, `ENABLE_TRACING`

**OpenAI-Compat:** `ENABLE_OPENAI_COMPAT`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_COMPAT_THINKING_HIGH_THRESHOLD`, `OPENAI_COMPAT_THINKING_MEDIUM_THRESHOLD`

See `.env.example` for full list including PTC, web search, web fetch, cache TTL, tracing, and beta header settings.

## API Compatibility

100% Anthropic Messages API compatible. Key differences:
- Model IDs mapped to Bedrock ARNs (or pass ARNs directly)
- Adds rate limiting (429 + `Retry-After`)
- Auth via `x-api-key` header
- PTC requires `anthropic-beta: advanced-tool-use-2025-11-20` + Docker
- Web search/fetch are proxy-side implementations
- Cache TTL supports `1h` extension

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md) for health endpoints, common errors, Docker/PTC issues, and debugging tips.

## Testing Strategy

- **Unit tests** (`tests/unit/`): Converters, schemas, middleware
- **Integration tests** (`tests/integration/`): Full request flow with mocked Bedrock
- **AWS mocking**: Use `moto` for DynamoDB and Bedrock

## Performance

- Conversion overhead: ~10-50ms (negligible vs Bedrock latency)
- DynamoDB lookup: 1-10ms
- Streaming: No buffering, events streamed as received
- Bottleneck: Almost always Bedrock API response time
