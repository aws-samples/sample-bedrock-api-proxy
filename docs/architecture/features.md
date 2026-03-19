# Feature Reference

## Programmatic Tool Calling (PTC)

PTC allows Claude to generate Python code that calls tools programmatically. The proxy implements this using a Docker sandbox for code execution with client-side tool execution.

### How PTC Works

1. Client sends request with:
   - Header: `anthropic-beta: advanced-tool-use-2025-11-20`
   - Tool: `{"type": "code_execution_20250825", "name": "code_execution"}`
   - Regular tools with `allowed_callers: ["code_execution_20250825"]`

2. **Detailed Flow (Multi-Tool, Multi-Round):**

   ```
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ INITIAL REQUEST                                                         │
   ├─────────────────────────────────────────────────────────────────────────┤
   │ Client Request → Proxy → Bedrock (Claude)                               │
   │                              ↓                                          │
   │                    Claude returns execute_code                          │
   │                              ↓                                          │
   │                    Proxy creates Docker container                       │
   │                    Proxy runs code in sandbox                           │
   └─────────────────────────────────────────────────────────────────────────┘
                                  ↓
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ TOOL CALL LOOP (repeats for each tool call from code)                   │
   ├─────────────────────────────────────────────────────────────────────────┤
   │ Code calls tool (e.g., get_expenses) → Sandbox PAUSES                   │
   │                              ↓                                          │
   │ Proxy returns tool_use to client (with caller field)                    │
   │                              ↓                                          │
   │ Client executes tool locally → sends tool_result back                   │
   │                              ↓                                          │
   │ Proxy RESUMES sandbox with tool result                                  │
   │                              ↓                                          │
   │ Code continues... (may call more tools → repeat loop)                   │
   └─────────────────────────────────────────────────────────────────────────┘
                                  ↓
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ CODE COMPLETION                                                         │
   ├─────────────────────────────────────────────────────────────────────────┤
   │ Code finishes execution → Proxy gets stdout/stderr                      │
   │                              ↓                                          │
   │ Proxy calls Claude with code output as tool_result                      │
   │                              ↓                                          │
   │ Claude generates final response (or requests more code → repeat all)    │
   │                              ↓                                          │
   │ Proxy returns Claude's response to client                               │
   └─────────────────────────────────────────────────────────────────────────┘
   ```

3. **Response Content Types:**

   Tool call from code (client must execute):
   ```json
   {
     "type": "tool_use",
     "id": "toolu_xxx",
     "name": "get_expenses",
     "input": {"employee_id": "ENG008"},
     "caller": {
       "type": "code_execution_20250825",
       "tool_id": "srvtoolu_yyy"
     }
   }
   ```

   Direct tool call (not from code execution):
   ```json
   {
     "type": "tool_use",
     "id": "toolu_xxx",
     "name": "search_docs",
     "input": {"query": "..."},
     "caller": {"type": "direct"}
   }
   ```

4. **Session Management:**
   - Container reuse via `container` field in response/request
   - Sessions timeout after 4.5 minutes (`PTC_SESSION_TIMEOUT`)
   - Client must include `container.id` in continuation requests
   - Proxy tracks pending tool calls per session

5. **Multi-Round Code Execution:**
   - After code completes, Claude may request another `execute_code`
   - Proxy handles this recursively until Claude returns text response
   - Same container is reused across multiple code execution rounds

6. **Parallel Tool Calls (asyncio.gather support):**

   Claude can generate code that calls tools in parallel using `asyncio.gather`:
   ```python
   expense_tasks = [
       get_expenses(employee_id=emp_id, quarter='Q3')
       for emp_id in employee_ids
   ]
   results = await asyncio.gather(*expense_tasks)
   ```

   **How it works:**
   - Sandbox detects multiple tool calls within 100ms batch window
   - Proxy returns response with multiple `tool_use` blocks (one per tool)
   - Client executes all tools (can be parallel) and returns all `tool_result` blocks
   - Proxy batches results and injects them all into sandbox
   - Code resumes with all results available

   **Response format (batch):**
   ```json
   {
     "content": [
       {"type": "server_tool_use", "id": "srvtoolu_xxx", "name": "code_execution"},
       {"type": "tool_use", "id": "toolu_aaa", "name": "get_expenses", "input": {"employee_id": "ENG001"}, "caller": {}},
       {"type": "tool_use", "id": "toolu_bbb", "name": "get_expenses", "input": {"employee_id": "ENG002"}, "caller": {}},
       {"type": "tool_use", "id": "toolu_ccc", "name": "get_expenses", "input": {"employee_id": "ENG003"}, "caller": {}}
     ],
     "stop_reason": "tool_use"
   }
   ```

   **Client continuation (all results in one request):**
   ```json
   {
     "messages": ["...", {
       "role": "user",
       "content": [
         {"type": "tool_result", "tool_use_id": "toolu_aaa", "content": "..."},
         {"type": "tool_result", "tool_use_id": "toolu_bbb", "content": "..."},
         {"type": "tool_result", "tool_use_id": "toolu_ccc", "content": "..."}
       ]
     }],
     "container": {"id": "container_xxx"}
   }
   ```

   **Configuration:**
   - `tool_call_batch_window_ms`: Time window to collect parallel calls (default: 100ms)

### Key Files

- `app/services/ptc_service.py` - Main PTC orchestration, state management
- `app/services/ptc/sandbox.py` - Docker sandbox executor, socket IPC
- `app/services/ptc/exceptions.py` - PTC-specific exceptions
- `app/schemas/ptc.py` - PTC Pydantic models
- `app/schemas/anthropic.py` - Content types (ServerToolUseContent, ServerToolResultContent)

### Key Methods in PTCService

- `process_request()` - Entry point, detects PTC and orchestrates flow
- `_handle_code_execution()` - Runs code, handles tool call pauses
- `handle_tool_result_continuation()` - Resumes paused sandbox
- `_finalize_code_execution()` - Calls Claude after code completes
- `resume_execution()` - Injects tool result into sandbox, continues

### Requirements

- Docker must be running and accessible
- `python:3.11-slim` image (or configured `PTC_SANDBOX_IMAGE`)
- **For AWS ECS deployment**: Must use EC2 launch type (not Fargate)
  - Fargate doesn't provide Docker daemon access
  - EC2 launch type mounts Docker socket (`/var/run/docker.sock`)
  - Deploy with: `./scripts/deploy.sh -e dev -p arm64 -l ec2`
- **For multi-instance deployments**: ALB sticky sessions must be enabled
  - PTC sessions are stored in-memory on individual instances
  - Continuation requests (with `container.id`) must route to the same instance
  - CDK automatically enables sticky sessions with 300s duration

### Limitations

- Non-streaming only (streaming PTC not yet implemented)
- Network disabled in sandbox by default
- Tools are executed client-side (not in the sandbox)
- ECS Fargate not supported (no Docker daemon access)
- Multi-instance deployments require sticky sessions

### PTC Settings

- `PTC_SANDBOX_IMAGE=python:3.11-slim` - Docker image for sandbox
- `PTC_SESSION_TIMEOUT=270` - Session timeout in seconds (4.5 minutes)
- `PTC_EXECUTION_TIMEOUT=60` - Code execution timeout
- `PTC_MEMORY_LIMIT=256m` - Container memory limit
- `PTC_NETWORK_DISABLED=True` - Disable network in sandbox

---

## Web Search Tool

The proxy implements Anthropic's `web_search_20250305` and `web_search_20260209` server tools. Bedrock doesn't natively support these, so the proxy intercepts and executes them.

### How it Works

1. Client sends request with a web search tool (e.g., `{"type": "web_search_20250305", "name": "web_search"}`)
2. Proxy detects the tool, removes it from tools sent to Bedrock, and injects a regular tool definition
3. When Bedrock (Claude) calls the search tool, the proxy intercepts the tool_use response
4. Proxy executes the search via Tavily or Brave, builds a tool_result with search results
5. Proxy sends the tool_result back to Bedrock in a multi-turn agentic loop (up to 25 iterations)
6. Supports hybrid streaming: non-streaming Bedrock calls but emits SSE events per iteration

### Tool Types

- `web_search_20250305` - Basic search, no Docker needed
- `web_search_20260209` - Dynamic filtering (Claude writes code to filter results), requires Docker sandbox

### Key Files

- `app/services/web_search_service.py` - Main orchestration, agentic loop
- `app/services/web_search/providers.py` - Tavily and Brave search implementations
- `app/services/web_search/domain_filter.py` - Domain filtering logic
- `app/schemas/web_search.py` - Pydantic models (WebSearchToolDefinition, UserLocation, etc.)

### Configuration

- `ENABLE_WEB_SEARCH=True` - Feature flag
- `WEB_SEARCH_PROVIDER=tavily` - Provider (`tavily` or `brave`)
- `WEB_SEARCH_API_KEY` - Provider API key (Tavily or Brave)
- `WEB_SEARCH_MAX_RESULTS=5` - Max results per search
- `WEB_SEARCH_DEFAULT_MAX_USES=10` - Max searches per request

---

## Web Fetch Tool

The proxy implements Anthropic's `web_fetch_20250910` and `web_fetch_20260209` server tools. Uses httpx for direct fetching (no external API key required by default).

### How it Works

Same agentic loop pattern as web search. Proxy intercepts web_fetch tool calls, fetches the URL content, converts HTML to plain text, and returns results to Bedrock.

### Tool Types

- `web_fetch_20250910` - Basic URL fetching, no Docker needed
- `web_fetch_20260209` - Dynamic filtering (Claude writes code to process fetched content), requires Docker

### Key Files

- `app/services/web_fetch_service.py` - Main orchestration
- `app/services/web_fetch/providers.py` - HttpxFetchProvider (default, no API key), TavilyFetchProvider
- `app/schemas/web_fetch.py` - Pydantic models

### Configuration

- `ENABLE_WEB_FETCH=True` - Feature flag (enabled by default)
- `WEB_FETCH_DEFAULT_MAX_USES=20` - Max fetches per request
- `WEB_FETCH_DEFAULT_MAX_CONTENT_TOKENS=100000` - Content length limit

---

## Beta Header Mapping and Tool Input Examples

### Beta Header Mapping

When Anthropic clients send beta headers (e.g., `anthropic-beta: advanced-tool-use-2025-11-20`), the proxy maps them to corresponding Bedrock beta headers for supported models.

**Configuration (`app/core/config.py`):**
```python
# Beta header mapping (Anthropic → Bedrock)
beta_header_mapping: Dict[str, List[str]] = {
    "advanced-tool-use-2025-11-20": [
        "tool-examples-2025-10-29",
        "tool-search-tool-2025-10-19",
    ],
}

# Models that support beta header mapping
beta_header_supported_models: List[str] = [
    "claude-opus-4-5-20251101",
    "global.anthropic.claude-opus-4-5-20251101-v1:0",
]
```

**How it works:**
1. Client sends request with `anthropic-beta: advanced-tool-use-2025-11-20` header
2. Proxy checks if the model supports beta header mapping
3. If supported, maps to Bedrock beta headers: `tool-examples-2025-10-29`, `tool-search-tool-2025-10-19`
4. Mapped headers are added to `additionalModelRequestFields.anthropic_beta`

### Tool Input Examples

The `input_examples` parameter allows providing example inputs to help Claude understand how to use a tool:

```python
tools = [
    {
        "name": "get_weather",
        "description": "Get the current weather in a given location",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
            },
            "required": ["location"]
        },
        "input_examples": [
            {"location": "San Francisco, CA", "unit": "fahrenheit"},
            {"location": "Tokyo, Japan", "unit": "celsius"},
            {"location": "New York, NY"}
        ]
    }
]
```

**Key Files:**
- `app/core/config.py` - `beta_header_mapping` and `beta_header_supported_models` settings
- `app/schemas/anthropic.py` - `Tool.input_examples` field
- `app/converters/anthropic_to_bedrock.py` - `_map_beta_headers()`, `_supports_beta_header_mapping()`, `_get_tools_with_examples()`

**Implementation Note:**
Bedrock's standard `toolSpec` doesn't support `inputExamples`. When the `tool-examples-2025-10-29` beta is enabled, tools with `input_examples` are passed via `additionalModelRequestFields.tools` in Anthropic format instead of the standard `toolConfig`.

---

## Cache TTL (1-Hour Caching)

The proxy extends Anthropic's `cache_control` with configurable TTL. Bedrock Claude models default to 5-minute cache; the proxy supports extending to 1 hour.

### TTL Priority (highest to lowest)

1. API Key `cache_ttl` field (forced override for all requests from that key)
2. Client request `cache_control.ttl` value
3. `DEFAULT_CACHE_TTL` environment variable
4. Anthropic/Bedrock default (5 minutes)

### Cost Impact

5m writes cost 1.25x input price; 1h writes cost 2.0x input price. The system auto-calculates correct pricing per TTL.

### Configuration

`DEFAULT_CACHE_TTL=1h` (or `5m`)

---

## OpenTelemetry Tracing

Full OpenTelemetry tracing system for LLM observability, following the OTEL GenAI semantic conventions.

### Architecture

- Turn-based trace structure: each HTTP request becomes a "Turn" span, grouped under a session trace
- Session store (`app/tracing/session_store.py`) maps `x-session-id` headers to OTEL trace contexts with 600s TTL
- `ChatOnlySpanProcessor` filters out third-party library spans
- Supports Langfuse, Jaeger, Grafana Tempo, and any OTEL-compatible backend

### Key Files

- `app/tracing/provider.py` - TracerProvider initialization and exporter configuration
- `app/tracing/middleware.py` - TracingMiddleware (creates root request spans)
- `app/tracing/spans.py` - Span helpers for LLM calls, tool execution, PTC
- `app/tracing/attributes.py` - OTEL GenAI semantic convention constants (~51 attributes)
- `app/tracing/streaming.py` - Streaming response token accumulator
- `app/tracing/session_store.py` - Session-to-trace mapping for agent loop aggregation
- `app/tracing/context.py` - Session ID extraction and thread context propagation

### Configuration

- `ENABLE_TRACING=true` - Feature flag (disabled by default)
- `OTEL_EXPORTER_OTLP_ENDPOINT` - Export endpoint (e.g., Langfuse, Jaeger)
- `OTEL_EXPORTER_OTLP_PROTOCOL` - `http/protobuf` (default) or `grpc`
- `OTEL_EXPORTER_OTLP_HEADERS` - Auth headers (e.g., `Authorization=Basic xxx`)
- `OTEL_SERVICE_NAME` - Service name for trace identification
- `OTEL_TRACE_CONTENT=false` - Include request/response bodies (PII risk)
- `OTEL_TRACE_SAMPLING_RATIO=1.0` - Sampling ratio (0.0-1.0)

**Zero-overhead design:** When tracing is disabled, all trace functions are no-ops.

---

## Admin Portal

The admin portal is a separate FastAPI application for managing API keys, usage, pricing, and budgets.

### Architecture

- Backend: `admin_portal/backend/main.py` - Independent FastAPI server (port 8005 in dev)
- Frontend: `admin_portal/frontend/` - Static files served from `frontend/dist`
- Auth: AWS Cognito JWT validation via `admin_portal/backend/middleware/cognito_auth.py`
- Background task: Usage aggregation runs every 5 minutes (`admin_portal/backend/services/usage_aggregator.py`)

### API Routers (`admin_portal/backend/api/`)

- `auth.py` - Cognito authentication (login, token refresh)
- `api_keys.py` - API key CRUD, budget management
- `pricing.py` - Model pricing configuration
- `dashboard.py` - Usage statistics and analytics
- `model_mapping.py` - Model ID mapping management

### Production Deployment

In production (ECS), the admin portal frontend is served as static files from the main proxy at `/admin/`, with API calls proxied to the backend.
