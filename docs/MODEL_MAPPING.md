# Model ID Mapping Guide

This guide explains how to add and manage model ID mappings between Anthropic's model identifiers and AWS Bedrock model ARNs.

## Overview

The proxy service translates Anthropic model IDs (like `claude-sonnet-4-5-20250929`) to Bedrock model ARNs (like `global.anthropic.claude-sonnet-4-5-20250929-v1:0`). There are three levels of model mapping:

1. **Default mappings** - Built into the code (`app/core/config.py`)
2. **Custom mappings** - Stored in DynamoDB (highest priority)
3. **Pass-through** - If no mapping found, the ID is used as-is

## Methods to Add Model Mappings

### Method 1: Using the Management Script (Recommended)

The easiest way to manage model mappings is using the provided script:

```bash
# List all mappings (default + custom)
uv run python scripts/manage_model_mapping.py list

# Add a new custom mapping
uv run python scripts/manage_model_mapping.py add \
    --anthropic-id "claude-sonnet-4-5-20250929" \
    --bedrock-id "qwen.qwen3-coder-480b-a35b-v1:0"

# Add a new custom mapping
uv run python scripts/manage_model_mapping.py add \
    --anthropic-id "claude-haiku-4-5-20251001" \
    --bedrock-id "qwen.qwen3-235b-a22b-2507-v1:0"

# Test how a model ID will be resolved
uv run python scripts/manage_model_mapping.py test \
    --anthropic-id "claude-sonnet-4-5-20250929"

# Delete a custom mapping
uv run python scripts/manage_model_mapping.py delete \
    --anthropic-id "claude-haiku-4-5-20251001"
```

**With uv:**
```bash
uv run python scripts/manage_model_mapping.py list
```

### Method 2: Programmatically via Python

You can add mappings programmatically in your code:

```python
from app.db.dynamodb import DynamoDBClient, ModelMappingManager

# Initialize clients
dynamodb_client = DynamoDBClient()
mapping_manager = ModelMappingManager(dynamodb_client)

# Add a mapping
mapping_manager.set_mapping(
    anthropic_model_id="claude-3-5-sonnet-20241022",
    bedrock_model_id="anthropic.claude-3-5-sonnet-20241022-v2:0"
)

# Get a mapping
bedrock_id = mapping_manager.get_mapping("claude-3-5-sonnet-20241022")
print(f"Bedrock ID: {bedrock_id}")

# List all custom mappings
mappings = mapping_manager.list_mappings()
for mapping in mappings:
    print(f"{mapping['anthropic_model_id']} → {mapping['bedrock_model_id']}")

# Delete a mapping
mapping_manager.delete_mapping("claude-3-5-sonnet-20241022")
```

### Method 3: Update Default Mappings in Code

For permanent default mappings, edit `app/core/config.py`:

```python
# In app/core/config.py
default_model_mapping: Dict[str, str] = Field(
    default={
        # Add your new mapping here
        "claude-3-5-sonnet-20241022": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "claude-3-opus-20240229": "anthropic.claude-3-opus-20240229-v1:0",
        # ... other mappings
    },
    alias="DEFAULT_MODEL_MAPPING",
)
```

**Note:** This requires restarting the service.

### Method 4: Environment Variable (JSON)

You can override default mappings via environment variable:

```bash
# In .env file
DEFAULT_MODEL_MAPPING='{"claude-3-5-sonnet-20241022":"anthropic.claude-3-5-sonnet-20241022-v2:0"}'
```

**Note:** This replaces ALL default mappings, so include all models you need.

### Method 5: Direct DynamoDB Access

You can add mappings directly to DynamoDB:

```bash
# Using AWS CLI
aws dynamodb put-item \
    --table-name anthropic-proxy-model-mapping \
    --item '{
        "anthropic_model_id": {"S": "claude-3-5-sonnet-20241022"},
        "bedrock_model_id": {"S": "anthropic.claude-3-5-sonnet-20241022-v2:0"},
        "updated_at": {"N": "1234567890"}
    }'
```

## Model ID Resolution Priority

The service resolves model IDs in this order:

1. **Custom DynamoDB mapping** (highest priority)
2. **Default config mapping**
3. **Pass-through** (use the ID as-is, assuming it's a valid Bedrock ARN)

### Example Resolution Flow

```
Request: "claude-3-5-sonnet-20241022"
    ↓
Check DynamoDB custom mappings
    ↓ (not found)
Check default config mappings
    ↓ (found!)
Use: "anthropic.claude-3-5-sonnet-20241022-v2:0"
```

## Common Bedrock Model ARNs

### Anthropic Claude Models

| Anthropic ID | Bedrock ARN |
|--------------|-------------|
| `claude-3-5-sonnet-20241022` | `anthropic.claude-3-5-sonnet-20241022-v2:0` |
| `claude-3-5-sonnet-20240620` | `anthropic.claude-3-5-sonnet-20240620-v1:0` |
| `claude-3-opus-20240229` | `anthropic.claude-3-opus-20240229-v1:0` |
| `claude-3-sonnet-20240229` | `anthropic.claude-3-sonnet-20240229-v1:0` |
| `claude-3-haiku-20240307` | `anthropic.claude-3-haiku-20240307-v1:0` |
| `claude-2.1` | `anthropic.claude-v2:1` |
| `claude-2.0` | `anthropic.claude-v2` |
| `claude-instant-1.2` | `anthropic.claude-instant-v1` |

### Other Bedrock Models

You can also map to other Bedrock foundation models:

| Custom ID | Bedrock ARN |
|-----------|-------------|
| `llama3-70b` | `meta.llama3-70b-instruct-v1:0` |
| `mistral-7b` | `mistral.mistral-7b-instruct-v0:2` |
| `titan-express` | `amazon.titan-text-express-v1` |

## Verifying Mappings

### Check What Mapping Will Be Used

```bash
# Test resolution
python scripts/manage_model_mapping.py test \
    --anthropic-id "claude-3-5-sonnet-20241022"
```

Output:
```
🔍 Testing model ID resolution for: claude-3-5-sonnet-20241022
================================================================================

  No custom mapping in DynamoDB

✓ Found in default config:
  claude-3-5-sonnet-20241022 → anthropic.claude-3-5-sonnet-20241022-v2:0

🎯 Final resolved ID (what will be used):
  anthropic.claude-3-5-sonnet-20241022-v2:0

================================================================================
```

### List All Mappings

```bash
python scripts/manage_model_mapping.py list
```

## Troubleshooting

### Model Not Found Error

If you get an error like "Model not found" when making a request:

1. Check if the Anthropic model ID has a mapping:
   ```bash
   python scripts/manage_model_mapping.py test --anthropic-id "your-model-id"
   ```

2. Verify the Bedrock model ARN is correct and available in your region:
   ```bash
   aws bedrock list-foundation-models --region us-east-1
   ```

3. Add the mapping if missing:
   ```bash
   python scripts/manage_model_mapping.py add \
       --anthropic-id "your-model-id" \
       --bedrock-id "bedrock.model-arn"
   ```

### Custom Mapping Not Working

- Ensure DynamoDB tables are created: `python scripts/setup_tables.py`
- Check AWS credentials have DynamoDB access
- Verify the table name in `.env` matches: `DYNAMODB_MODEL_MAPPING_TABLE`

### Pass-Through Not Working

If you want to use a Bedrock ARN directly without mapping:

```python
# This should work automatically
{
    "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",  # Full Bedrock ARN
    "messages": [...]
}
```

The service will use it as-is if no mapping is found.

## Best Practices

1. **Use custom mappings for temporary overrides** - Don't modify default config for testing
2. **Document custom mappings** - Keep track of why custom mappings were added
3. **Test resolution before deploying** - Use the test command to verify mappings
4. **Use pass-through for ad-hoc testing** - Directly use Bedrock ARNs when experimenting
5. **Keep default mappings updated** - Update `config.py` when new models are released

## Examples

### Example 1: Add Support for New Claude Model

```bash
# New model just released
python scripts/manage_model_mapping.py add \
    --anthropic-id "claude-3-5-sonnet-20250101" \
    --bedrock-id "anthropic.claude-3-5-sonnet-20250101-v1:0"

# Test it
python scripts/manage_model_mapping.py test \
    --anthropic-id "claude-3-5-sonnet-20250101"

# Use it in API request
curl -X POST http://localhost:8000/v1/messages \
  -H "x-api-key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20250101",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### Example 2: Override Default Mapping for Testing

```bash
# Temporarily point to a different Bedrock version
python scripts/manage_model_mapping.py add \
    --anthropic-id "claude-3-5-sonnet-20241022" \
    --bedrock-id "anthropic.claude-3-5-sonnet-20241022-v1:0"

# Test your application...

# Remove override when done
python scripts/manage_model_mapping.py delete \
    --anthropic-id "claude-3-5-sonnet-20241022"
```

### Example 3: Use Non-Anthropic Model

```bash
# Map a friendly name to a Llama model
python scripts/manage_model_mapping.py add \
    --anthropic-id "llama-3-70b" \
    --bedrock-id "meta.llama3-70b-instruct-v1:0"

# Now you can use it with the Anthropic SDK format
curl -X POST http://localhost:8000/v1/messages \
  -H "x-api-key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3-70b",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

## See Also

- [Architecture Documentation](../ARCHITECTURE.md)
- [AWS Bedrock Models Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html)
- [DynamoDB Table Schema](../README.md#dynamodb-schema)
