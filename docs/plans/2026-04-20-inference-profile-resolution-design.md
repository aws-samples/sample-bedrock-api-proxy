# Inference Profile Resolution Design

**Date**: 2026-04-20
**Branch**: `feat/inference-profile-resolution`

## Problem

Clients can pass a Bedrock **application inference profile** ARN as the model ID, e.g.:

```
arn:aws:bedrock:us-east-1:434444145045:application-inference-profile/k5jycjzpuzbv
```

The ARN is an opaque identifier; the underlying foundation model (Claude, Nova, etc.) is only known by querying the Bedrock control plane. Current code relies on substring matching against the model ID (`"claude"` / `"anthropic"` keywords) to decide:

1. **Routing** (`_is_claude_model`) — InvokeModel vs Converse / OpenAI-compat
2. **Beta header mapping** (`_supports_beta_header_mapping`) — whether to translate Anthropic beta headers to Bedrock equivalents
3. **Billing** (`get_cost` / `record_usage`) — which pricing row to use, how usage aggregates

For an application inference profile backed by Claude, the ARN matches none of these checks, so:

- Requests are routed to Converse instead of InvokeModel → beta features broken, prompt caching disabled
- Beta header mapping silently passes through unmapped values → Bedrock rejects the call
- Pricing lookup returns nothing → usage recorded at $0
- Usage table aggregates by ARN, not by underlying model → admin reports misattribute

**System-defined / cross-region profiles** like `us.anthropic.claude-sonnet-4-5-...` already carry the model name in the identifier and work correctly. Only **application** inference profiles with opaque IDs are affected.

## Goals

- Correctly identify the underlying foundation model of an application inference profile
- Preserve InvokeModel routing, beta header translation, and accurate billing for Claude-backed profiles
- Zero impact on requests that use plain model IDs or system-defined inference profiles
- No DynamoDB schema changes

## Non-Goals

- Extending resolution to Nova2 / Kimi / prompt caching detection (follow-up if needed)
- Persisting resolution results across workers / instances (in-memory cache is sufficient)
- Auto-discovering all inference profiles in the account

## Architecture

Introduce a singleton `InferenceProfileResolver` that maps an application inference profile ARN to its underlying foundation model ARN. The resolver is called at three points (routing, beta header check, billing) before the existing substring-match logic runs.

```
Request.model
    │
    ▼
┌─────────────────────────────────────┐
│ InferenceProfileResolver            │
│   regex match ARN pattern?          │
│     no  → return as-is              │
│     yes → cache lookup              │
│            miss → bedrock.          │
│              get_inference_profile  │
│              cache with TTL         │
│              return models[0].Arn   │
└─────────────────────────────────────┘
    │
    ▼
underlying_model_id
    │
    ├──► _is_claude_model(underlying_model_id)
    ├──► _supports_beta_header_mapping(resolved=underlying_model_id)
    └──► pricing_manager.get_pricing(underlying_model_id)
```

### Resolver contract

```python
class InferenceProfileResolver:
    _ARN_PATTERN = re.compile(
        r"^arn:aws:bedrock:[\w-]+:\d+:application-inference-profile/.+$"
    )

    def __init__(self, bedrock_client, ttl_seconds: int = 3600):
        self._client = bedrock_client
        self._ttl = ttl_seconds
        self._cache: Dict[str, Tuple[str, float]] = {}
        self._lock = threading.Lock()

    def resolve(self, model_id: str) -> str:
        """Return underlying model ID; non-application-profile IDs pass through."""
```

- Non-matching IDs return immediately — zero overhead on the hot path.
- Cache: per-process `dict` keyed by ARN, value `(underlying_model_id, expires_at)`.
- TTL: `INFERENCE_PROFILE_CACHE_TTL_SECONDS` env var, default `3600`.
- Thread-safe via `threading.Lock` (consistent with `_provider_clients` pattern).
- On API failure (ClientError, missing `models[]`, etc.) raise `InferenceProfileResolutionError`.

## Integration Points

| File | Location | Change |
|------|----------|--------|
| `app/services/inference_profile_resolver.py` | new | Resolver class + exception |
| `app/services/bedrock_service.py:118` | `_is_claude_model(model_id)` | `resolver.resolve(model_id)` → string match |
| `app/converters/anthropic_to_bedrock.py:207` | `_is_claude_model()` | Same, against `self._resolved_model_id` |
| `app/converters/anthropic_to_bedrock.py:263` | `_supports_beta_header_mapping` | Substring-match also against `resolver.resolve(self._resolved_model_id)` |
| `app/services/bedrock_provider.py:88` | `get_cost()` | `pricing.get_pricing(resolver.resolve(model_id))` |
| `app/api/messages.py` (record_usage call) | — | `metadata["resolved_model"] = resolver.resolve(request.model)` when different from request.model |
| `app/core/config.py` | add | `INFERENCE_PROFILE_CACHE_TTL_SECONDS: int = 3600` |
| `app/main.py` | exception handler | Map `InferenceProfileResolutionError` → 400/502 |

### Beta header mapping (L263)

Current logic matches keywords against `original_model_id` and `self._resolved_model_id` (mapping-table resolved). Extend by adding a third candidate: `resolver.resolve(self._resolved_model_id)`. This preserves existing behavior for all non-ARN inputs while fixing application profiles.

### Billing (`anthropic-proxy-usage` table)

- `model` column: **keeps the original ARN** the client sent — zero migration, admin reports stay stable.
- `metadata.resolved_model`: populated when ARN resolution happens. Admin portal can later add a dimension keyed on this.
- `pricing_manager.get_pricing(...)` receives the resolved ID. Pricing table entries for foundation models (e.g. `global.anthropic.claude-sonnet-4-5-...`) get hit correctly. No new rows needed for profiles.

## Error Handling

`InferenceProfileResolutionError` → API layer returns:

- `400 Bad Request` when Bedrock returns `ValidationException` / `ResourceNotFoundException` (caller-supplied a bad ARN)
- `502 Bad Gateway` when Bedrock returns `AccessDeniedException` / timeouts (configuration / transient infra issue)

Strict mode: no fallback to "assume Claude" or "assume non-Claude". If we can't determine the underlying model, we refuse the request rather than silently misroute it.

## Caching

- In-memory `dict` per worker process
- TTL: 1 hour default (inference profile bindings are stable; TTL bounds staleness if a profile is re-bound)
- No eager warm-up; lazy on first use per profile per worker

A misbinding detected after deploy auto-heals within TTL. For immediate recovery, restart workers.

## IAM

Task role (`cdk/lib/ecs-stack.ts`) needs `bedrock:GetInferenceProfile` for the account's inference profile resource ARN pattern. Document in deployment notes.

## Testing

### Unit tests (`tests/unit/test_inference_profile_resolver.py`)

| Case | Expectation |
|------|-------------|
| Non-ARN passes through | No API call, returns input |
| System profile (`us.anthropic...`) passes through | Regex does not match, no API call |
| Application ARN, cold cache | One `get_inference_profile` call, returns `models[0].modelArn` |
| Application ARN, warm cache | Zero API calls |
| TTL expiry | Second call after mocked clock advance hits API again |
| `ClientError` raised | Resolver raises `InferenceProfileResolutionError` with cause |
| Empty `models[]` in response | Raises `InferenceProfileResolutionError` |
| Concurrent resolution of same ARN | No data race; result stable |

### Integration tests (`tests/integration/test_inference_profile_routing.py`)

- Claude application profile → routes to InvokeModel, `anthropic_beta` appended
- Non-Claude application profile → routes to Converse (or OpenAI-compat)
- Pricing: cost computed against underlying model; usage record has `model=ARN`, `metadata.resolved_model=<underlying>`
- Resolution failure → 400/502 response with actionable error message

## Observability

Log on first resolution per ARN:
```
[RESOLVER] Resolved arn:...:application-inference-profile/xxx -> global.anthropic.claude-sonnet-4-5-... (ttl=3600s)
```

Future metrics (when tracing is enabled):
- `inference_profile_cache_hit_rate`
- `inference_profile_resolve_errors`

## Rollout & Rollback

**Rollout**:
1. Merge `feat/inference-profile-resolution` after tests pass
2. Deploy with updated task role (includes `bedrock:GetInferenceProfile`)
3. Smoke-test with a real Claude-backed application inference profile in dev
4. Promote to prod; monitor error rate and resolve latency

**Rollback**: Purely additive. Non-ARN and system-profile paths unchanged. Revert the 5 edited files + remove new module. IAM permission added is harmless if unused.

## Out-of-Scope Follow-ups

- Nova2 / Kimi / prompt caching detection for application profiles — extend the resolver's use sites when demand appears.
- Persisting resolution results in DynamoDB for cross-worker sharing.
- Admin portal UI: report usage by `metadata.resolved_model` dimension.
