# Cache TTL Support for Claude Models

**Date:** 2026-03-02
**Scope:** InvokeModel path (Claude models) + proxy-level default + per-API-key override

## Problem

The Anthropic API supports 1-hour prompt cache TTL (`cache_control.ttl: "1h"`), but the proxy's `CacheControl` schema only accepts `type: "ephemeral"` with no TTL field. Users cannot configure cache duration, and admins cannot enforce TTL policies per API key.

## Design

### Priority Logic

TTL is resolved with this priority (highest first):

1. **API key `cache_ttl`** (DynamoDB) — forced override, rewrites ALL cache_control blocks
2. **Client request `cache_control.ttl`** — used if no API key override
3. **`DEFAULT_CACHE_TTL` env var** — proxy-level fallback
4. **No TTL** — omit field, Anthropic defaults to 5m

When API key has `cache_ttl` set, client-specified TTL is always overwritten.

### Data Model Changes

**CacheControl schema** (`app/schemas/anthropic.py`):
```python
class CacheControl(BaseModel):
    type: Literal["ephemeral"] = "ephemeral"
    ttl: Optional[Literal["5m", "1h"]] = None
```

**Config** (`app/core/config.py`):
```python
default_cache_ttl: Optional[str] = Field(default=None, alias="DEFAULT_CACHE_TTL")
```

**DynamoDB API Keys table** — new optional field:
- `cache_ttl`: String, `"5m"` | `"1h"` | absent (no override)

### Code Changes

1. **Schema** — Add `ttl` to `CacheControl`
2. **Config** — Add `DEFAULT_CACHE_TTL` setting
3. **bedrock_service.py** — Add `_apply_cache_ttl()` method in InvokeModel path that walks all `cache_control` blocks and applies priority logic
4. **Auth middleware** — Pass `cache_ttl` from API key data to request state
5. **messages.py** — Thread `cache_ttl` from request state to bedrock service
6. **DynamoDB APIKeyManager** — Support `cache_ttl` in create/update/read
7. **Admin Portal Backend** — Add `cache_ttl` to API key schemas
8. **Admin Portal Frontend** — Add Cache TTL dropdown in form + column in table

### Scope Limitation

- **InvokeModel path only** (Claude models) — native Anthropic format pass-through
- **Converse path untouched** — Bedrock `cachePoint` blocks don't support TTL

### Admin Portal UI

**Form:** Dropdown with options: "Use proxy default" / "5 minutes" / "1 hour"

**Table:** New "Cache TTL" column with badges:
- `1h` → blue badge
- `5m` → gray badge
- `Default` → light gray text
