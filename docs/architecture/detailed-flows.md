# Detailed Architecture Flows

## Request/Response Conversion Flows

### Converse API Flow (non-Claude models)

1. `app/api/messages.py` - Receives Anthropic-formatted request
2. `app/middleware/auth.py` - Validates API key from DynamoDB
3. `app/middleware/rate_limit.py` - Enforces token bucket rate limiting
4. `app/converters/anthropic_to_bedrock.py` - **Converts request to Bedrock format**
5. `app/services/bedrock_service.py` - Calls AWS Bedrock Converse API
6. `app/converters/bedrock_to_anthropic.py` - **Converts response back to Anthropic format**
7. Response returned to client

### InvokeModel API Flow (Claude models)

1. `app/api/messages.py` - Receives Anthropic-formatted request
2. `app/middleware/auth.py` - Validates API key from DynamoDB
3. `app/middleware/rate_limit.py` - Enforces token bucket rate limiting
4. `app/services/bedrock_service.py` - Converts to native Anthropic format with Bedrock versioning
5. `app/services/bedrock_service.py` - Calls AWS Bedrock InvokeModel API
6. Response is already in Anthropic format (minimal conversion to MessageResponse)
7. Response returned to client

### Streaming Flow

Same as above, using streaming API variants (`invoke_model_with_response_stream` or `converse_stream`).

## Content Block Conversion Reference

**Anthropic → Bedrock (Converse API):**

| Anthropic Format | Bedrock Format |
|-----------------|----------------|
| `TextContent` | `{"text": "..."}` |
| `ImageContent` | `{"image": {"format": "png", "source": {"bytes": b"..."}}}` |
| `ToolUseContent` | `{"toolUse": {"toolUseId": "...", "name": "...", "input": {...}}}` |
| `ToolResultContent` | `{"toolResult": {"toolUseId": "...", "content": [...], "status": "success"}}` |

**Streaming Event Conversion:**

| Bedrock Event | Anthropic Event |
|--------------|-----------------|
| `contentBlockDelta` | `content_block_delta` |
| `messageStart` | `message_start` |

SSE format: `event: <type>\ndata: <json>\n\n`

## Model ID Mapping

Configured in `app/core/config.py`:
- Anthropic model IDs (e.g., `claude-3-5-sonnet-20241022`) → Bedrock ARNs (e.g., `anthropic.claude-3-5-sonnet-20241022-v2:0`)
- Custom mappings stored in DynamoDB `model-mapping` table
- Falls back to treating unknown IDs as valid Bedrock ARNs

## DynamoDB Schema

### API Keys Table (`anthropic-proxy-api-keys`)

- **PK:** `api_key` - The actual API key string
- **Attributes:** `user_id`, `name`, `is_active`, `rate_limit`, `service_tier`, `metadata`
- **Budget fields:** `monthly_budget`, `budget_used` (total), `budget_used_mtd` (month-to-date), `budget_mtd_month` (YYYY-MM)
- **Deactivation:** `deactivated_reason` ("budget_exceeded" when MTD exceeds monthly limit)
- **GSI:** `user_id-index` for querying by user

### Usage Tracking Table (`anthropic-proxy-usage`)

- **PK:** `api_key`, **SK:** `timestamp`
- **Attributes:** `request_id`, `model`, `input_tokens`, `output_tokens`, `success`
- **GSI:** `request_id-index` for request lookup

### Usage Stats Table (`anthropic-proxy-usage-stats`)

- **PK:** `api_key`
- **Attributes:** `total_input_tokens`, `total_output_tokens`, `total_cached_tokens`, `total_cache_write_tokens`, `total_requests`, `last_aggregated_timestamp`
- Used for incremental usage aggregation

### Model Pricing Table (`anthropic-proxy-model-pricing`)

- **PK:** `model_id` - Bedrock model ID
- **Attributes:** `provider`, `display_name`, `input_price`, `output_price`, `cache_read_price`, `cache_write_price`, `status`

### Model Mapping Table (`anthropic-proxy-model-mapping`)

- **PK:** `anthropic_model_id`
- **Attributes:** `bedrock_model_id`

## Budget Usage Computation

The system calculates budget usage through incremental aggregation, running every 5 minutes via `UsageAggregator`.

### Data Flow

```
anthropic-proxy-usage          (raw request logs per API call)
        │
        ▼ (aggregation every 5 min)
anthropic-proxy-usage-stats    (aggregated token counts + last_aggregated_timestamp)
        │
        ▼ (cost calculation with service tier multiplier)
anthropic-proxy-api-keys       (budget_used + budget_used_mtd - displayed in UI)
```

### Budget Tracking Fields

| Field | Description |
|-------|-------------|
| `budget_used` | Total cumulative budget used (never resets) |
| `budget_used_mtd` | Month-to-date budget used (resets at start of each month) |
| `budget_mtd_month` | Month being tracked for MTD (YYYY-MM format) |
| `monthly_budget` | Monthly budget limit (compared against `budget_used_mtd`) |

### Automatic Budget Enforcement

- When `budget_used_mtd >= monthly_budget`, the API key is automatically deactivated
- `deactivated_reason` is set to `"budget_exceeded"`
- Key automatically reactivates at the start of the next month
- Admin can manually reactivate keys via the portal

### Incremental Aggregation

- First run: Processes ALL usage records, sets `last_aggregated_timestamp`
- Subsequent runs: Only processes records WHERE `timestamp > last_aggregated_timestamp`
- Uses atomic `INCREMENT` operations to avoid race conditions
- Month rollover: When aggregating in a new month, `budget_used_mtd` resets to 0

### Service Tier Pricing Multipliers

| Tier | Multiplier | Description |
|------|------------|-------------|
| `default` | 1.0 | Standard pricing |
| `flex` | 0.5 | 50% discount |
| `priority` | 1.75 | 75% markup |

### Cost Calculation

```
base_cost = (input_tokens × input_price / 1M)
          + (output_tokens × output_price / 1M)
          + (cached_tokens × cache_read_price / 1M)
          + (cache_write_tokens × cache_write_price / 1M)

adjusted_cost = base_cost × service_tier_multiplier
```

### Key Files

- `app/db/dynamodb.py` → `UsageStatsManager.aggregate_all_usage()` - Main aggregation logic
- `app/db/dynamodb.py` → `APIKeyManager.increment_budget_used()` - MTD tracking + budget enforcement
- `app/db/dynamodb.py` → `APIKeyManager.validate_api_key()` - Auto-reactivation on new month
- `admin_portal/backend/services/usage_aggregator.py` - Scheduler (runs every 5 min)

### Resetting Budget Data

To fully reset budget calculations:
1. Delete all items from `anthropic-proxy-usage-stats` table
2. Reset budget fields on all API keys:
```bash
# Reset both total and MTD budget for all keys
aws dynamodb scan --table-name anthropic-proxy-api-keys \
  --projection-expression "api_key" \
  --query "Items[*].api_key.S" --output text | \
  tr '\t' '\n' | while read key; do
    aws dynamodb update-item --table-name anthropic-proxy-api-keys \
      --key "{\"api_key\": {\"S\": \"$key\"}}" \
      --update-expression "SET budget_used = :zero, budget_used_mtd = :zero" \
      --expression-attribute-values "{\":zero\": {\"N\": \"0\"}}"
  done
```
