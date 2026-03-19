# Architecture Documentation

## System Overview

The Anthropic-Bedrock API Proxy is a translation layer that enables clients using the Anthropic Python SDK to seamlessly access AWS Bedrock models. The service performs bidirectional format conversion between Anthropic's Messages API and AWS Bedrock's Converse API.

## Design Principles

1. **API Compatibility**: Full compatibility with Anthropic Messages API format
2. **Performance**: Low-latency conversion with minimal overhead
3. **Scalability**: Horizontally scalable stateless design
4. **Reliability**: Comprehensive error handling and retry logic
5. **Observability**: Detailed logging, metrics, and tracing
6. **Security**: Defense-in-depth with authentication, rate limiting, and encryption

## Component Architecture

### 1. API Layer (FastAPI)

**Responsibilities:**
- HTTP request/response handling
- Request validation using Pydantic models
- Route handling and endpoint registration
- SSE (Server-Sent Events) streaming support
- OpenAPI documentation generation

**Key Components:**
- `/v1/messages`: Message creation endpoint (streaming & non-streaming)
- `/v1/models`: Model listing endpoint
- `/health`, `/ready`, `/liveness`: Health check endpoints

### 2. Middleware Layer

#### Authentication Middleware (`app/middleware/auth.py`)

**Flow:**
```
1. Extract API key from x-api-key header
2. Check master API key (if configured)
3. Validate API key against DynamoDB
4. Attach API key info to request state
5. Pass request to next handler
```

**Features:**
- DynamoDB-backed API key validation
- Master key support for administrative access
- Configurable bypass for development
- Detailed error messages

#### Rate Limiting Middleware (`app/middleware/rate_limit.py`)

**Algorithm:** Token Bucket

**Flow:**
```
1. Extract API key from request state
2. Get or create token bucket for API key
3. Try to consume 1 token
4. If successful: pass request to handler
5. If failed: return 429 with Retry-After header
```

**Features:**
- Per-API-key rate limiting
- Configurable capacity and refill rate
- Rate limit headers in responses
- Token bucket algorithm for smooth rate limiting

### 3. Conversion Layer

#### Anthropic to Bedrock Converter (`app/converters/anthropic_to_bedrock.py`)

**Converts:**
- Model IDs (Anthropic → Bedrock ARN)
- Message format (role + content blocks)
- Content types (text, images, documents, tool use)
- System messages
- Inference configuration (temperature, top_p, etc.)
- Tool definitions and tool choice
- Thinking configuration (when supported)
- Thinking/Redacted Thinking content blocks (for multi-turn conversations)

**Model-Specific Handling:**
- **Amazon Nova 2 Models**: Uses `reasoningConfig` with `maxReasoningEffort` (low/medium/high) instead of standard thinking configuration. Automatically removes `temperature` and `maxTokens` when reasoning is enabled.
- **Claude Models**: Uses standard `additionalModelRequestFields` for thinking blocks with `budget_tokens`.

**Key Methods:**
```python
convert_request(request: MessageRequest) -> Dict[str, Any]
_convert_messages(messages: List[Message]) -> List[Dict]
_convert_content_blocks(content: Union[str, List]) -> List[Dict]
_convert_tool_config(tools: List[Tool]) -> Dict
_convert_system(system: Union[str, List]) -> List[Dict]
_is_nova_2_model() -> bool  # Detect Amazon Nova 2 models
```

#### Bedrock to Anthropic Converter (`app/converters/bedrock_to_anthropic.py`)

**Converts:**
- Response messages back to Anthropic format
- Content blocks (text, images, tool use)
- Reasoning content blocks (`reasoningContent` → `ThinkingContent` / `RedactedThinkingContent`)
- Usage statistics (token counts)
- Stop reasons
- Streaming events (messageStart, contentBlockDelta, thinkingBlockStart, etc.)

**Content Block Types:**
- `text` → `TextContent`
- `toolUse` → `ToolUseContent`
- `reasoningContent.reasoningText` → `ThinkingContent` (with optional `signature` for multi-turn)
- `reasoningContent.redactedContent` → `RedactedThinkingContent`

**Key Methods:**
```python
convert_response(response: Dict, model: str) -> MessageResponse
convert_stream_event(event: Dict) -> List[Dict]
_convert_content_blocks(content: List[Dict]) -> List[ContentBlock]
_convert_usage(usage: Dict) -> Usage
_convert_stop_reason(reason: str) -> str
```

### 4. Service Layer

#### Bedrock Service (`app/services/bedrock_service.py`)

**Responsibilities:**
- AWS Bedrock API interaction
- Request/response conversion orchestration
- Streaming event handling
- Service tier management
- Error handling and retries

**Methods:**
```python
invoke_model(request: MessageRequest, service_tier: str = None) -> MessageResponse
invoke_model_stream(request: MessageRequest, service_tier: str = None) -> AsyncGenerator[str]
list_available_models() -> List[Dict]
get_model_info(model_id: str) -> Dict
```

**Features:**
- Synchronous and streaming invocation
- Automatic format conversion
- SSE event formatting
- Service tier support with automatic fallback
- Comprehensive error handling

**Service Tier Handling:**
- Supports Bedrock service tiers: `default`, `flex`, `priority`, `reserved`
- Automatically falls back to `default` tier if the specified tier is not supported by the model
- Claude models only support `default` and `reserved` tiers (not `flex`)
- Service tier is passed via `serviceTier` field in the Bedrock Converse API request

### 5. Data Layer

#### DynamoDB Client (`app/db/dynamodb.py`)

**Tables:**

1. **API Keys Table** (`anthropic-proxy-api-keys`)
   - Partition Key: `api_key` (String)
   - GSI: `user_id-index`
   - Attributes: `user_id`, `name`, `created_at`, `is_active`, `rate_limit`, `service_tier`, `metadata`

2. **Usage Table** (`anthropic-proxy-usage`)
   - Partition Key: `api_key` (String)
   - Sort Key: `timestamp` (Number)
   - GSI: `request_id-index`
   - Attributes: `request_id`, `model`, `input_tokens`, `output_tokens`, `success`, `error_message`

3. **Model Mapping Table** (`anthropic-proxy-model-mapping`)
   - Partition Key: `anthropic_model_id` (String)
   - Attributes: `bedrock_model_id`, `updated_at`

**Managers:**
- `APIKeyManager`: CRUD operations for API keys
- `UsageTracker`: Record and query usage statistics
- `ModelMappingManager`: Custom model ID mappings

### 6. Configuration Management

#### Settings (`app/core/config.py`)

**Configuration Sources:**
1. Environment variables
2. `.env` file (development)
3. AWS Systems Manager Parameter Store (production)
4. Default values

**Categories:**
- Application settings (name, version, environment)
- Server settings (host, port, workers)
- AWS settings (region, credentials, endpoints)
- DynamoDB settings (table names, endpoints)
- Authentication settings (API key header, master key)
- Rate limiting settings (requests, window)
- Feature flags (tool use, thinking, documents)
- Service tier settings (default service tier)
- Monitoring settings (metrics, tracing, Sentry)

### 7. Observability

#### Logging (`app/core/logging.py`)

**Format:** Structured key-value pairs
```
timestamp=2024-01-15 10:30:00 level=INFO logger=app.api.messages message="Request received" request_id=msg_abc123 api_key=sk-dev-...xyz user_id=user123 model=claude-3-5-sonnet-20241022
```

**Context:**
- Request ID (correlation)
- API key (masked)
- User ID
- Model used
- Token usage
- Latency

#### Metrics (`app/core/metrics.py`)

**Prometheus Metrics:**

1. **Request Metrics**
   - `api_requests_total`: Counter by method, endpoint, status_code
   - `api_request_duration_seconds`: Histogram by method, endpoint

2. **Bedrock Metrics**
   - `bedrock_requests_total`: Counter by model, success
   - `bedrock_request_duration_seconds`: Histogram by model

3. **Token Usage**
   - `input_tokens_total`: Counter by model, api_key
   - `output_tokens_total`: Counter by model, api_key
   - `cached_tokens_total`: Counter by model, api_key

4. **Rate Limiting**
   - `rate_limit_exceeded_total`: Counter by api_key

5. **Authentication**
   - `auth_failures_total`: Counter by reason

6. **Application Info**
   - `api_proxy_app_info`: Info gauge with version, environment, region

## Data Flow

### Non-Streaming Request Flow

```
1. Client → POST /v1/messages (Anthropic format)
2. AuthMiddleware → Validate API key
3. RateLimitMiddleware → Check rate limit
4. MessageHandler → Validate request body
5. AnthropicToBedrockConverter → Convert request
6. BedrockService → Invoke Bedrock Converse API
7. BedrockToAnthropicConverter → Convert response
8. UsageTracker → Record usage
9. Response → Client (Anthropic format)
```

### Streaming Request Flow

```
1. Client → POST /v1/messages (stream=true)
2. AuthMiddleware → Validate API key
3. RateLimitMiddleware → Check rate limit
4. MessageHandler → Validate request body
5. AnthropicToBedrockConverter → Convert request
6. BedrockService → Invoke Bedrock ConverseStream API
7. For each event:
   a. Receive Bedrock event
   b. BedrockToAnthropicConverter → Convert to Anthropic event
   c. Format as SSE (event: type\ndata: json\n\n)
   d. Yield to client
8. UsageTracker → Record final usage
9. Stream complete
```

## Security Architecture

### Defense in Depth

**Layer 1: Network**
- HTTPS/TLS encryption
- CORS configuration
- VPC endpoints for AWS services

**Layer 2: Authentication**
- API key validation
- Master key for admin operations
- DynamoDB-backed key storage

**Layer 3: Authorization**
- Per-key rate limits
- User ID tracking
- Usage quotas (future)

**Layer 4: Application**
- Input validation (Pydantic)
- Request size limits
- Timeout configuration

**Layer 5: Data**
- Encrypted at rest (DynamoDB)
- Encrypted in transit (TLS)
- API key masking in logs

### Threat Mitigation

| Threat | Mitigation |
|--------|-----------|
| API key theft | Key rotation, rate limiting, usage monitoring |
| DDoS | Rate limiting, auto-scaling, ALB |
| Injection attacks | Input validation, parameterized queries |
| Credential exposure | Environment variables, secret managers |
| Data leakage | API key masking, structured logging |

## Extended Thinking Support

### Overview

Extended thinking allows models to perform step-by-step reasoning before producing a final response. The proxy supports extended thinking for both Claude models and Amazon Nova 2 models, with different backend implementations.

### Content Block Types

**ThinkingContent:**
```python
class ThinkingContent(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str                    # The thinking/reasoning text
    signature: Optional[str] = None  # Signature for multi-turn conversations
```

**RedactedThinkingContent:**
```python
class RedactedThinkingContent(BaseModel):
    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str  # Base64 encoded redacted data
```

### Model-Specific Implementation

**Claude Models:**
- Uses `additionalModelRequestFields` with `thinking.type` and `thinking.budget_tokens`
- Thinking blocks returned as `reasoningContent.reasoningText`
- Supports multi-turn conversations with `signature` field

**Amazon Nova 2 Models:**
- Uses `additionalModelRequestFields` with `reasoningConfig`
- Configuration: `{"type": "enabled", "maxReasoningEffort": "low|medium|high"}`
- Budget tokens mapped to effort levels: <1000 → low, 1000-10000 → medium, >10000 → high
- **Important**: `temperature` and `maxTokens` must be unset when reasoning is enabled

### Multi-Turn Conversations

When using extended thinking in multi-turn conversations:
1. First response includes thinking blocks with a `signature`
2. Subsequent requests must include thinking blocks in the message history
3. The `signature` field must be preserved for continuity
4. Redacted thinking blocks are returned when content is hidden for safety/policy reasons

## Bedrock Service Tier Support

### Overview

Bedrock service tiers allow you to optimize for cost vs. latency. The proxy supports configuring service tiers at both the global and per-API-key level.

### Available Tiers

| Tier | Description | Claude Support |
|------|-------------|----------------|
| `default` | Standard service tier | ✅ Yes |
| `flex` | Lower cost, higher latency (up to 24 hours) | ❌ No |
| `priority` | Lower latency, higher cost | ✅ Yes |
| `reserved` | Reserved capacity tier | ✅ Yes |

### Configuration

**Global Default:**
```bash
DEFAULT_SERVICE_TIER=default  # Environment variable
```

**Per-API-Key:**
```python
# When creating API key
client.create_api_key(
    user_id="user123",
    name="My Key",
    service_tier="priority"
)
```

### Automatic Fallback

When a service tier is not supported by the model (e.g., `flex` with Claude):
1. The proxy attempts the request with the specified tier
2. If Bedrock returns an error about unsupported service tier
3. The proxy automatically retries with `default` tier
4. The request succeeds transparently to the client

### Priority Order

1. API key `service_tier` attribute (highest priority)
2. Global `DEFAULT_SERVICE_TIER` setting
3. Default value: `default`

## Scalability Architecture

### Horizontal Scaling

**Stateless Design:**
- No session state in application
- All state in DynamoDB
- Can scale to N instances

**Auto-Scaling Triggers:**
- CPU utilization > 70%
- Request latency p99 > 5s
- Active connections > 1000

### Performance Optimization

**Bedrock Prompt Caching:**
- Uses Bedrock's native `cachePoint` feature in requests
- Reduces latency for repeated prompts with same prefix
- Controlled by `PROMPT_CACHING_ENABLED` setting

**Connection Pooling:**
- Reuse boto3 clients
- HTTP connection pooling in httpx
- DynamoDB connection pooling

**Async Processing:**
- FastAPI async handlers
- Async streaming with generators
- Non-blocking I/O operations

## Deployment Architecture

### Development
```
Docker Compose Stack:
- API Proxy (port 8000)
- DynamoDB Local (port 8001)
- DynamoDB Admin UI (port 8002)
- Prometheus (port 9090)
- Grafana (port 3000)
```

### Production (AWS ECS)

The proxy supports two ECS launch types with different capabilities:

#### Launch Type Comparison

| Feature | Fargate | EC2 |
|---------|---------|-----|
| **Management** | Serverless, zero management | Some instance management |
| **Scaling** | Fast (seconds) | Slower (minutes) |
| **Cost Model** | Pay per vCPU/memory/second | Pay per EC2 instance |
| **Docker Socket** | Not available | Available |
| **PTC Support** | ❌ No | ✅ Yes |
| **Use Case** | General workloads | PTC-enabled deployments |

#### Fargate Launch Type (Default)
```
Load Balancer (ALB)
    ↓
ECS Fargate Service (Multi-AZ)
    ├── Task 1 (us-east-1a)
    ├── Task 2 (us-east-1b)
    └── Task 3 (us-east-1c)
    ↓
AWS Services:
    ├── Bedrock Runtime
    ├── DynamoDB Tables
    ├── CloudWatch Logs
    └── CloudWatch Metrics
```

**Deploy with:**
```bash
./scripts/deploy.sh -e prod -p arm64 -l fargate
```

#### EC2 Launch Type (For PTC Support)
```
Load Balancer (ALB)
    ↓
ECS EC2 Service (Multi-AZ)
    ├── EC2 Instance (us-east-1a)
    │   ├── ECS Agent
    │   ├── API Proxy Container
    │   │   └── Docker Socket Mount (/var/run/docker.sock)
    │   └── PTC Sandbox Containers (spawned on demand)
    ├── EC2 Instance (us-east-1b)
    │   └── ... (same structure)
    └── Auto Scaling Group
    ↓
AWS Services:
    ├── Bedrock Runtime
    ├── DynamoDB Tables
    ├── CloudWatch Logs
    └── CloudWatch Metrics
```

**Deploy with:**
```bash
./scripts/deploy.sh -e prod -p arm64 -l ec2
```

**EC2 Configuration Details:**
- **Instance Types**: t4g.medium/large (ARM64) or t3.medium/large (AMD64)
- **Spot Instances**: Enabled for dev (cost savings), disabled for prod (stability)
- **Docker Socket**: Mounted to containers for PTC sandbox execution
- **Auto Scaling**: EC2 Auto Scaling Group with ECS Capacity Provider

### PTC (Programmatic Tool Calling) on ECS EC2

When deploying with EC2 launch type, PTC is automatically enabled:

```
┌─────────────────────────────────────────────────────────────────┐
│  EC2 Instance                                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  API Proxy Container                                      │   │
│  │  ┌────────────────┐    ┌─────────────────────────────┐   │   │
│  │  │ PTCService     │───→│ Docker Socket               │   │   │
│  │  │ - Execute code │    │ /var/run/docker.sock        │   │   │
│  │  │ - Manage       │    └──────────────┬──────────────┘   │   │
│  │  │   sessions     │                   │                   │   │
│  │  └────────────────┘                   │                   │   │
│  └───────────────────────────────────────│──────────────────┘   │
│                                          │                       │
│  ┌───────────────────────────────────────▼──────────────────┐   │
│  │  PTC Sandbox Containers (spawned on demand)              │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │   │
│  │  │ Sandbox 1   │  │ Sandbox 2   │  │ Sandbox N   │      │   │
│  │  │ (session A) │  │ (session B) │  │ (session X) │      │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Key Components:**
- **Docker Socket Mount**: Allows API proxy to create sandbox containers
- **PTC Sandbox Containers**: Isolated Python environments for code execution
- **Session Management**: Container reuse with configurable timeout (4.5 min default)

### Production (Kubernetes)
```
Ingress (with TLS)
    ↓
Service (ClusterIP)
    ↓
Deployment (3 replicas)
    ├── Pod 1
    ├── Pod 2
    └── Pod 3
    ↓
AWS Services via IRSA:
    ├── Bedrock Runtime
    ├── DynamoDB Tables
    └── CloudWatch
```

## Error Handling

### Error Categories

1. **Client Errors (4xx)**
   - 400: Invalid request format
   - 401: Authentication failed
   - 429: Rate limit exceeded
   - 404: Model/resource not found

2. **Server Errors (5xx)**
   - 500: Internal error
   - 502: Bedrock API error
   - 503: Service unavailable
   - 504: Request timeout

### Retry Strategy

**Bedrock API Calls:**
- Max attempts: 3
- Mode: Adaptive
- Backoff: Exponential (1s, 2s, 4s)
- Retry on: Throttling, 5xx errors

**DynamoDB Operations:**
- Max attempts: 3
- Mode: Standard
- Backoff: Exponential
- Retry on: ProvisionedThroughputExceeded, 5xx

## Future Enhancements

### Recently Implemented

1. **Extended Thinking Support** ✅
   - ThinkingContent and RedactedThinkingContent blocks
   - Multi-turn conversation support with signatures
   - Amazon Nova 2 reasoning configuration

2. **Bedrock Service Tier** ✅
   - Per-API-key service tier configuration
   - Automatic fallback for unsupported tiers
   - Support for default, flex, priority, reserved tiers

### Planned Features

1. **Advanced Caching**
   - Redis integration for high-speed response caching
   - Intelligent cache warming

2. **Enhanced Authentication**
   - JWT token support
   - OAuth2 integration
   - RBAC (Role-Based Access Control)

3. **Advanced Rate Limiting**
   - Token-based rate limiting
   - Per-model rate limits
   - Quota management

4. **Multi-Region Support**
   - Active-active deployment
   - Cross-region failover
   - Global DynamoDB tables

5. **Enhanced Monitoring**
   - Distributed tracing (X-Ray, Jaeger)
   - Custom CloudWatch dashboards
   - Anomaly detection

6. **Cost Optimization**
   - Model routing based on cost
   - Budget alerts
   - Cost allocation tagging

## Performance Benchmarks

### Target Metrics

| Metric | Target |
|--------|--------|
| P50 latency (non-streaming) | < 500ms |
| P95 latency (non-streaming) | < 2s |
| P99 latency (non-streaming) | < 5s |
| Time to first token (streaming) | < 500ms |
| Requests per second (per instance) | > 100 |
| Error rate | < 0.1% |
| Availability | 99.9% |

### Load Testing

**Test Scenarios:**
1. Sustained load: 100 req/s for 1 hour
2. Spike: 0 → 500 req/s in 1 minute
3. Gradual ramp: 0 → 1000 req/s over 10 minutes

**Success Criteria:**
- Zero errors under sustained load
- P95 latency < 5s during spike
- No memory leaks over time
