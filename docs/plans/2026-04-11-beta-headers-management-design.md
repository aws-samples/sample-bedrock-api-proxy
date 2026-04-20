# Beta Headers Management Page — Design

**Date**: 2026-04-11
**Status**: Approved

## Overview

Add a Beta Headers management page to the Admin Portal, allowing administrators to dynamically manage how Anthropic beta headers are handled when proxied to Bedrock. This replaces the current hardcoded configuration in `config.py` with a DynamoDB-backed, UI-manageable system.

## Core Logic

Two types of beta header rules:

- **blocklist**: Header is filtered out and NOT sent to Bedrock.
- **mapping**: Header is translated to one or more Bedrock-equivalent headers.

**Default behavior**: Any header NOT in blocklist or mapping is passed through to Bedrock as-is. This eliminates the need for an explicit passthrough list.

## Data Model

### DynamoDB Table: `anthropic-proxy-beta-headers`

| Field | Type | Description |
|-------|------|-------------|
| `header_name` (PK) | String | Beta header name, e.g. `"advisor-tool-2026-03-01"` |
| `header_type` | String | `"mapping"` or `"blocklist"` |
| `mapped_to` | List[String] | Bedrock headers (only for `mapping` type) |
| `description` | String | Optional admin note |
| `created_at` | String | ISO 8601 timestamp |
| `updated_at` | String | ISO 8601 timestamp |

No GSI needed — total records expected to be small (tens of items), full table scan is acceptable.

### Default Data (migrated from config.py)

**blocklist** entries:
- `prompt-caching-scope-2026-01-05`
- `redact-thinking-2026-02-12`
- `advisor-tool-2026-03-01`

**mapping** entries:
- `advanced-tool-use-2025-11-20` → `["tool-examples-2025-10-29", "tool-search-tool-2025-10-19"]`

## Backend API

### Admin Portal Router: `/api/beta-headers`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/beta-headers` | GET | List all, supports `?type=blocklist&search=xxx` |
| `/api/beta-headers/{header_name}` | GET | Get single record |
| `/api/beta-headers` | POST | Create new header rule |
| `/api/beta-headers/{header_name}` | PUT | Update (type, mapped_to, description) |
| `/api/beta-headers/{header_name}` | DELETE | Delete record |

### Pydantic Schemas (`admin_portal/backend/schemas/beta_headers.py`)

```python
class BetaHeaderCreate(BaseModel):
    header_name: str
    header_type: Literal["mapping", "blocklist"]
    mapped_to: List[str] = []  # required when header_type == "mapping"
    description: str = ""

class BetaHeaderUpdate(BaseModel):
    header_type: Optional[Literal["mapping", "blocklist"]] = None
    mapped_to: Optional[List[str]] = None
    description: Optional[str] = None

class BetaHeaderResponse(BaseModel):
    header_name: str
    header_type: str
    mapped_to: List[str]
    description: str
    created_at: str
    updated_at: str

class BetaHeaderListResponse(BaseModel):
    items: List[BetaHeaderResponse]
    count: int
```

### DynamoDB Manager (`app/db/beta_header_manager.py`)

New `BetaHeaderManager` class following the same pattern as `ModelMappingManager`:
- `list_all()` — scan entire table
- `get(header_name)` — get item by PK
- `create(data)` — put item with timestamps
- `update(header_name, data)` — update item, refresh `updated_at`
- `delete(header_name)` — delete item

### Admin Portal Router (`admin_portal/backend/api/beta_headers.py`)

Standard CRUD router using `BetaHeaderManager`. Register in `admin_portal/backend/main.py`:
```python
app.include_router(beta_headers.router, prefix="/api/beta-headers", tags=["Beta Headers"])
```

## Proxy Integration

### BetaHeaderConfigCache (`app/db/beta_header_cache.py`)

Caches DynamoDB data in memory for the proxy service:

- **Startup**: Load all records from DynamoDB, build `blocklist: Set[str]` and `mapping: Dict[str, List[str]]`.
- **Periodic refresh**: Background thread refreshes every 5 minutes.
- **Fallback**: If DynamoDB table is empty or unreachable, fall back to defaults in `config.py`.
- **Thread-safe**: Use `threading.Lock` for cache updates.

### bedrock_service.py Changes

Replace the current multi-list check with:

```python
cache = BetaHeaderConfigCache.instance()

for beta_value in beta_values:
    if beta_value in cache.get_blocklist():
        # Filter out — not supported by Bedrock
        print(f"[BEDROCK NATIVE] Filtering out unsupported beta header: {beta_value}")
    elif beta_value in cache.get_mapping():
        # Translate to Bedrock headers
        bedrock_beta.extend(cache.get_mapping()[beta_value])
        print(f"[BEDROCK NATIVE] Mapped beta header: {beta_value} -> {cache.get_mapping()[beta_value]}")
    else:
        # Default: pass through as-is
        bedrock_beta.append(beta_value)
        print(f"[BEDROCK NATIVE] Passing through beta header: {beta_value}")
```

### config.py Changes

- Keep `beta_header_mapping` and `beta_headers_blocklist` as fallback defaults.
- Remove `beta_headers_passthrough` (no longer needed — unlisted headers auto-passthrough).
- Keep `beta_header_supported_models` and `beta_headers_requiring_invoke_model` unchanged (not managed via UI).

## Frontend

### Page: `BetaHeaders.tsx`

Location in Sidebar: Configuration section, alongside Model Mapping and Routing.

**List View**:
- Search box (filters by header name)
- Type filter dropdown: All / Mapping / Blocklist
- "Add Header" button
- Table columns:
  - **Header Name** — the beta header string
  - **Type** — colored badge (`mapping` = blue, `blocklist` = red)
  - **Mapped To** — tag/badge list of target Bedrock headers; shows "—" for blocklist
  - **Description** — admin note
  - **Actions** — Edit / Delete buttons

**Create/Edit Form** (SlideOver, right panel):
- Header Name — text input (read-only on edit)
- Type — dropdown (mapping / blocklist)
- Mapped To — tag input, add/remove target headers (visible only when type = mapping)
- Description — text area

**Delete Confirmation**: Dialog warning that deleting a blocklist entry will cause the header to pass through to Bedrock.

### Data Layer

**`services/api.ts`**:
```typescript
export const betaHeadersApi = {
  list: (params?) => apiFetch(`/beta-headers?${queryString}`),
  get: (name) => apiFetch(`/beta-headers/${encodeURIComponent(name)}`),
  create: (data) => apiFetch('/beta-headers', { method: 'POST', body }),
  update: (name, data) => apiFetch(`/beta-headers/${encodeURIComponent(name)}`, { method: 'PUT', body }),
  delete: (name) => apiFetch(`/beta-headers/${encodeURIComponent(name)}`, { method: 'DELETE' }),
};
```

**`hooks/useBetaHeaders.ts`**: Standard query/mutation hooks following existing pattern.

**i18n**: Add keys to both `en.json` and `zh.json` translation files.

## Migration Script

`scripts/setup_beta_headers_table.py`:

1. Create `anthropic-proxy-beta-headers` DynamoDB table.
2. Seed default data from current `config.py` values:
   - 3 blocklist entries
   - 1 mapping entry

Run alongside existing `scripts/setup_tables.py` or integrate into it.

## Files to Create/Modify

### New Files
- `app/db/beta_header_manager.py` — DynamoDB CRUD manager
- `app/db/beta_header_cache.py` — In-memory cache with periodic refresh
- `admin_portal/backend/api/beta_headers.py` — Admin API router
- `admin_portal/backend/schemas/beta_headers.py` — Pydantic schemas
- `admin_portal/frontend/src/pages/BetaHeaders.tsx` — Page component
- `admin_portal/frontend/src/hooks/useBetaHeaders.ts` — React Query hooks
- `scripts/setup_beta_headers_table.py` — Table creation + seed data

### Modified Files
- `admin_portal/backend/main.py` — Register new router
- `admin_portal/frontend/src/App.tsx` — Add route
- `admin_portal/frontend/src/components/Sidebar.tsx` — Add nav item
- `admin_portal/frontend/src/services/api.ts` — Add `betaHeadersApi`
- `admin_portal/frontend/src/i18n/en.json` — English translations
- `admin_portal/frontend/src/i18n/zh.json` — Chinese translations
- `app/core/config.py` — Remove `beta_headers_passthrough`, keep others as fallback
- `app/services/bedrock_service.py` — Use cache instead of static config
- `app/db/dynamodb.py` — Add `BetaHeaderManager` to `DynamoDBClient`
- `scripts/setup_tables.py` — Optionally integrate beta headers table creation
