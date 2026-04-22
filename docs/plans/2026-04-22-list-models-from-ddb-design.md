# Source `/v1/models` from DynamoDB + DEFAULT_MODEL_MAPPING

Date: 2026-04-22
Status: Design approved, ready to implement

## Problem

`/v1/models` currently calls Bedrock's `list_foundation_models` and returns
every TEXT-capable foundation model ID (e.g. `anthropic.claude-opus-4-7`).
Those raw foundation-model IDs cannot be invoked directly through this proxy —
invocation requires cross-region inference-profile IDs
(e.g. `global.anthropic.claude-opus-4-7`) or the Anthropic-SDK aliases that the
proxy itself defines in `DEFAULT_MODEL_MAPPING`.

The earlier temporary patch in `app/api/models.py`
(`_patch_anthropic_global_prefix`) rewrites `anthropic.*` → `global.anthropic.*`
for Claude entries only, but leaves the deeper problem: the list is sourced
from the wrong place and cannot surface aliases like `claude-opus-4-7[1m]` or
non-Anthropic Bedrock models onboarded via the admin portal.

## Goal

`/v1/models` should return exactly the set of model IDs that clients can
send to this proxy — i.e. the merged view of
`settings.default_model_mapping` and the `ModelMappingTable` in DynamoDB.

## Design

### 1. `DEFAULT_MODEL_MAPPING` additions (`app/core/config.py`)

Add six entries:

```python
# 1M-context aliases (same Bedrock target; 1M behaviour is driven by the
# anthropic-beta: context-1m-2025-08-07 header, not by the model ID).
"claude-opus-4-7[1m]":   "global.anthropic.claude-opus-4-7",
"claude-opus-4-6[1m]":   "global.anthropic.claude-opus-4-6-v1",
"claude-sonnet-4-6[1m]": "global.anthropic.claude-sonnet-4-6",

# Non-Claude Bedrock models (identity-mapped).
"minimax.minimax-m2.5":  "minimax.minimax-m2.5",
"zai.glm-5":             "zai.glm-5",
"moonshotai.kimi-k2.5":  "moonshotai.kimi-k2.5",
```

### 2. Rewrite `list_available_models()` (`app/services/bedrock_service.py`)

Replace the Bedrock foundation-model scan with a merge of defaults and DDB
`ModelMappingTable`. DDB wins on key conflict so that admin-portal overrides
take effect without a code deploy.

```python
def list_available_models(self) -> list[Dict[str, Any]]:
    merged: Dict[str, str] = dict(settings.default_model_mapping)

    try:
        db = DynamoDBClient()
        for row in db.model_mapping_manager.list_mappings():
            a_id = row.get("anthropic_model_id")
            b_id = row.get("bedrock_model_id")
            if a_id and b_id:
                merged[a_id] = b_id
    except Exception as e:
        logger.warning("Failed to load DDB model mappings: %s", e)
        # fall through — defaults-only list is still usable

    return [
        {
            "id": a_id,
            "bedrock_model_id": b_id,
            "provider": _derive_provider(b_id),
            "streaming_supported": True,
        }
        for a_id, b_id in merged.items()
    ]
```

`_derive_provider(bedrock_id)` returns the first dotted segment after any
known region/scope prefix (`global`, `us`, `eu`, `apac`) or inference-profile
ARN. Examples:

| Bedrock ID                                                  | provider    |
|-------------------------------------------------------------|-------------|
| `global.anthropic.claude-opus-4-7`                          | `anthropic` |
| `us.anthropic.claude-3-5-haiku-20241022-v1:0`               | `anthropic` |
| `minimax.minimax-m2.5`                                      | `minimax`   |
| `arn:aws:bedrock:us-east-1:...:inference-profile/global.anthropic.claude-…` | `anthropic` |

### 3. Response shape

```json
{
  "object": "list",
  "data": [
    {
      "id": "claude-opus-4-7",
      "bedrock_model_id": "global.anthropic.claude-opus-4-7",
      "provider": "anthropic",
      "streaming_supported": true
    },
    {
      "id": "minimax.minimax-m2.5",
      "bedrock_model_id": "minimax.minimax-m2.5",
      "provider": "minimax",
      "streaming_supported": true
    }
  ],
  "has_more": false
}
```

The previous fields (`name`, `input_modalities`, `output_modalities`) are
dropped — they required the Bedrock foundation-model scan, which we no longer
call, and no client of this proxy is known to depend on them.

### 4. Remove the temporary prefix patch (`app/api/models.py`)

Delete `_patch_anthropic_global_prefix`, `_apply_anthropic_global_prefix`, and
both call sites. IDs now come from DDB/defaults already in their final form,
so the patch is a no-op.

## Error handling

- DDB unreachable / scan fails: log a warning, return defaults-only list.
- Config empty and DDB empty: legitimate empty list — caller knows the proxy
  has no invokable models.
- Invalid DDB row (missing either field): skip silently.

## Testing

New `tests/unit/test_bedrock_service_list_models.py` (mock DDB via `moto`):

- DDB empty → returns exactly `DEFAULT_MODEL_MAPPING` rows.
- DDB key overlaps a default → DDB `bedrock_model_id` wins.
- DDB key is new → appears alongside defaults.
- DDB scan raises → defaults-only, warning logged.
- `_derive_provider` table-driven cases for each prefix pattern above.

Touch `tests/unit/test_models_api.py` (or create) to assert the response
shape and that the prefix-patch helpers are gone.

## Commit plan

Two commits on branch `feat/list-models-from-ddb`:

1. `feat(config): add [1m] + non-claude entries to DEFAULT_MODEL_MAPPING`
2. `refactor(api): source /v1/models from DDB + defaults, drop global.anthropic. patch`

## Out of scope

- Admin-portal UI changes (`ModelMappingTable` CRUD already exists).
- Pricing table updates for the new model IDs — tracked separately.
- Per-model capability flags beyond `streaming_supported`.
