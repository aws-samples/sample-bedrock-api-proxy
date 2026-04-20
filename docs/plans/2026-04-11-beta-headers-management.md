# Beta Headers Management Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Beta Headers management page to the Admin Portal so administrators can dynamically add/remove/edit beta header blocklist and mapping rules via UI, replacing hardcoded config.

**Architecture:** DynamoDB table stores beta header rules (blocklist + mapping types). Admin portal backend provides CRUD API. Proxy service loads rules from DynamoDB at startup and refreshes every 5 minutes, falling back to config.py defaults. Frontend page follows ModelMapping page patterns.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript/TanStack Query (frontend), DynamoDB, boto3

---

### Task 1: Add DynamoDB table config + table creation

**Files:**
- Modify: `app/core/config.py:68-85` (add table name field)
- Modify: `app/db/dynamodb.py:20-56` (add table name + create method)

**Step 1: Add table name to config.py**

In `app/core/config.py`, after `dynamodb_providers_table` (line 84), add:

```python
    dynamodb_beta_headers_table: str = Field(
        default="anthropic-proxy-beta-headers", alias="DYNAMODB_BETA_HEADERS_TABLE"
    )
```

**Step 2: Add table name and create method to DynamoDBClient**

In `app/db/dynamodb.py`, in `__init__` (after line 43), add:

```python
        self.beta_headers_table_name = settings.dynamodb_beta_headers_table
```

In `create_tables()` (after `self._create_providers_table()`), add:

```python
        self._create_beta_headers_table()
```

Add the create method (after `_create_providers_table`):

```python
    def _create_beta_headers_table(self):
        """Create beta headers config table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.beta_headers_table_name,
                KeySchema=[
                    {"AttributeName": "header_name", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "header_name", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.beta_headers_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.beta_headers_table_name}")
            else:
                raise
```

**Step 3: Update setup_tables.py to print the new table**

In `scripts/setup_tables.py`, add after line 35:

```python
    print(f"  - {dynamodb_client.beta_headers_table_name}")
```

**Step 4: Run setup to verify table creation**

```bash
uv run python scripts/setup_tables.py
```

Expected: Table `anthropic-proxy-beta-headers` created (or "already exists").

**Step 5: Commit**

```bash
git add app/core/config.py app/db/dynamodb.py scripts/setup_tables.py
git commit -m "feat: add DynamoDB table for beta headers management"
```

---

### Task 2: Create BetaHeaderManager (DynamoDB CRUD)

**Files:**
- Modify: `app/db/dynamodb.py` (add BetaHeaderManager class at end, after ModelMappingManager)

**Step 1: Add BetaHeaderManager class**

Add at the end of `app/db/dynamodb.py` (after the last class):

```python
class BetaHeaderManager:
    """Manager for beta header configuration."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.beta_headers_table_name)

    def list_all(self) -> List[Dict[str, Any]]:
        """List all beta header rules."""
        response = self.table.scan()
        return response.get("Items", [])

    def get(self, header_name: str) -> Optional[Dict[str, Any]]:
        """Get a beta header rule by name."""
        try:
            response = self.table.get_item(Key={"header_name": header_name})
            return response.get("Item")
        except ClientError:
            return None

    def create(self, header_name: str, header_type: str, mapped_to: List[str] = None, description: str = "") -> Dict[str, Any]:
        """Create a new beta header rule."""
        timestamp = datetime.now(timezone.utc).isoformat()
        item = {
            "header_name": header_name,
            "header_type": header_type,
            "mapped_to": mapped_to or [],
            "description": description,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        self.table.put_item(Item=item)
        return item

    def update(self, header_name: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a beta header rule."""
        existing = self.get(header_name)
        if not existing:
            return None

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        update_expr_parts = []
        expr_values = {}
        expr_names = {}
        for key, value in updates.items():
            safe_key = f"#k_{key}"
            expr_names[safe_key] = key
            expr_values[f":{key}"] = value
            update_expr_parts.append(f"{safe_key} = :{key}")

        self.table.update_item(
            Key={"header_name": header_name},
            UpdateExpression="SET " + ", ".join(update_expr_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
        return self.get(header_name)

    def delete(self, header_name: str) -> bool:
        """Delete a beta header rule."""
        existing = self.get(header_name)
        if not existing:
            return False
        self.table.delete_item(Key={"header_name": header_name})
        return True
```

**Step 2: Verify import at the module level**

Ensure `BetaHeaderManager` is importable alongside `ModelMappingManager`:

```bash
uv run python -c "from app.db.dynamodb import DynamoDBClient, BetaHeaderManager; print('OK')"
```

**Step 3: Commit**

```bash
git add app/db/dynamodb.py
git commit -m "feat: add BetaHeaderManager for beta header CRUD"
```

---

### Task 3: Create seed script for default data

**Files:**
- Create: `scripts/setup_beta_headers.py`

**Step 1: Create seed script**

```python
#!/usr/bin/env python3
"""Seed beta headers table with default data from config.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.dynamodb import DynamoDBClient, BetaHeaderManager
from app.core.config import settings


def main():
    """Seed beta headers table."""
    print("Setting up beta headers table...")

    db_client = DynamoDBClient()
    manager = BetaHeaderManager(db_client)

    # Seed blocklist entries from config defaults
    for header_name in settings.beta_headers_blocklist:
        existing = manager.get(header_name)
        if existing:
            print(f"  Already exists: {header_name}")
            continue
        manager.create(
            header_name=header_name,
            header_type="blocklist",
            description="Default blocklist entry from config",
        )
        print(f"  Created blocklist: {header_name}")

    # Seed mapping entries from config defaults
    for header_name, mapped_to in settings.beta_header_mapping.items():
        existing = manager.get(header_name)
        if existing:
            print(f"  Already exists: {header_name}")
            continue
        manager.create(
            header_name=header_name,
            header_type="mapping",
            mapped_to=mapped_to,
            description="Default mapping entry from config",
        )
        print(f"  Created mapping: {header_name} -> {mapped_to}")

    print("\nDone! Beta headers seeded.")


if __name__ == "__main__":
    main()
```

**Step 2: Run the seed script**

```bash
uv run python scripts/setup_beta_headers.py
```

Expected: 3 blocklist + 1 mapping entries created.

**Step 3: Commit**

```bash
git add scripts/setup_beta_headers.py
git commit -m "feat: add seed script for beta headers default data"
```

---

### Task 4: Create BetaHeaderConfigCache (proxy-side in-memory cache)

**Files:**
- Create: `app/db/beta_header_cache.py`

**Step 1: Create the cache module**

```python
"""In-memory cache for beta header configuration from DynamoDB."""
import logging
import threading
import time
from typing import Dict, List, Optional, Set

from app.core.config import settings

logger = logging.getLogger(__name__)


class BetaHeaderConfigCache:
    """Thread-safe in-memory cache for beta header rules.

    Loads from DynamoDB at startup, refreshes every refresh_interval seconds.
    Falls back to config.py defaults if DynamoDB is empty or unreachable.
    """

    _instance: Optional["BetaHeaderConfigCache"] = None
    _lock = threading.Lock()

    def __init__(self, refresh_interval: int = 300):
        self._refresh_interval = refresh_interval
        self._blocklist: Set[str] = set()
        self._mapping: Dict[str, List[str]] = {}
        self._data_lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._loaded = False

    @classmethod
    def instance(cls, refresh_interval: int = 300) -> "BetaHeaderConfigCache":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(refresh_interval)
                    cls._instance.start()
        return cls._instance

    def start(self):
        """Load data and start periodic refresh."""
        self._refresh()
        self._schedule_next()

    def stop(self):
        """Stop periodic refresh."""
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self):
        self._timer = threading.Timer(self._refresh_interval, self._refresh_and_reschedule)
        self._timer.daemon = True
        self._timer.start()

    def _refresh_and_reschedule(self):
        self._refresh()
        self._schedule_next()

    def _refresh(self):
        """Reload data from DynamoDB, fall back to config defaults."""
        try:
            from app.db.dynamodb import DynamoDBClient, BetaHeaderManager

            db_client = DynamoDBClient()
            manager = BetaHeaderManager(db_client)
            items = manager.list_all()

            if items:
                blocklist = set()
                mapping = {}
                for item in items:
                    name = item["header_name"]
                    htype = item["header_type"]
                    if htype == "blocklist":
                        blocklist.add(name)
                    elif htype == "mapping":
                        mapping[name] = item.get("mapped_to", [])

                with self._data_lock:
                    self._blocklist = blocklist
                    self._mapping = mapping
                    self._loaded = True
                logger.info(f"Beta header cache refreshed: {len(blocklist)} blocklist, {len(mapping)} mapping")
            else:
                self._load_defaults()
        except Exception as e:
            logger.warning(f"Failed to load beta headers from DynamoDB, using defaults: {e}")
            if not self._loaded:
                self._load_defaults()

    def _load_defaults(self):
        """Load fallback defaults from config.py."""
        with self._data_lock:
            self._blocklist = set(settings.beta_headers_blocklist)
            self._mapping = dict(settings.beta_header_mapping)
            self._loaded = True
        logger.info("Beta header cache loaded from config defaults")

    def get_blocklist(self) -> Set[str]:
        with self._data_lock:
            return set(self._blocklist)

    def get_mapping(self) -> Dict[str, List[str]]:
        with self._data_lock:
            return dict(self._mapping)
```

**Step 2: Verify import**

```bash
uv run python -c "from app.db.beta_header_cache import BetaHeaderConfigCache; print('OK')"
```

**Step 3: Commit**

```bash
git add app/db/beta_header_cache.py
git commit -m "feat: add BetaHeaderConfigCache with periodic DynamoDB refresh"
```

---

### Task 5: Integrate cache into bedrock_service.py + clean up config.py

**Files:**
- Modify: `app/services/bedrock_service.py:518-544`
- Modify: `app/core/config.py:206-217` (remove `beta_headers_passthrough`)

**Step 1: Update bedrock_service.py beta header logic**

Replace lines 518-544 in `app/services/bedrock_service.py` with:

```python
        # Add beta headers from client
        # Rules loaded from DynamoDB (blocklist → filter, mapping → translate, else → passthrough)
        bedrock_beta = []

        if anthropic_beta:
            from app.db.beta_header_cache import BetaHeaderConfigCache
            cache = BetaHeaderConfigCache.instance()
            blocklist = cache.get_blocklist()
            mapping = cache.get_mapping()

            beta_values = [b.strip() for b in anthropic_beta.split(",")]
            for beta_value in beta_values:
                if beta_value in blocklist:
                    print(f"[BEDROCK NATIVE] Filtering out unsupported beta header: {beta_value}")
                elif beta_value in mapping:
                    mapped = mapping[beta_value]
                    bedrock_beta.extend(mapped)
                    print(f"[BEDROCK NATIVE] Mapped beta header '{beta_value}' → {mapped}")
                else:
                    bedrock_beta.append(beta_value)
                    print(f"[BEDROCK NATIVE] Passing through beta header: {beta_value}")
```

Leave lines 542-544 (`if bedrock_beta: ...`) unchanged.

**Step 2: Remove `beta_headers_passthrough` from config.py**

Delete lines 206-217 in `app/core/config.py` (the `beta_headers_passthrough` field). Keep `beta_header_mapping` and `beta_headers_blocklist` as fallback defaults.

**Step 3: Run existing tests to check nothing breaks**

```bash
uv run pytest tests/ -x -q 2>&1 | head -30
```

**Step 4: Commit**

```bash
git add app/services/bedrock_service.py app/core/config.py
git commit -m "feat: use BetaHeaderConfigCache in bedrock_service, remove passthrough list"
```

---

### Task 6: Admin portal backend — schemas + router

**Files:**
- Create: `admin_portal/backend/schemas/beta_headers.py`
- Create: `admin_portal/backend/api/beta_headers.py`
- Modify: `admin_portal/backend/main.py:36-37,99`

**Step 1: Create Pydantic schemas**

Create `admin_portal/backend/schemas/beta_headers.py`:

```python
"""Beta Headers management schemas."""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class BetaHeaderCreate(BaseModel):
    header_name: str = Field(..., description="Beta header name")
    header_type: Literal["mapping", "blocklist"] = Field(..., description="Header rule type")
    mapped_to: List[str] = Field(default=[], description="Bedrock headers (mapping type only)")
    description: str = Field(default="", description="Optional admin note")


class BetaHeaderUpdate(BaseModel):
    header_type: Optional[Literal["mapping", "blocklist"]] = None
    mapped_to: Optional[List[str]] = None
    description: Optional[str] = None


class BetaHeaderResponse(BaseModel):
    header_name: str
    header_type: str
    mapped_to: List[str] = []
    description: str = ""
    created_at: str = ""
    updated_at: str = ""


class BetaHeaderListResponse(BaseModel):
    items: List[BetaHeaderResponse]
    count: int
```

**Step 2: Create API router**

Create `admin_portal/backend/api/beta_headers.py`:

```python
"""Beta Headers management routes."""
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, Query, status

from app.db.dynamodb import DynamoDBClient, BetaHeaderManager
from admin_portal.backend.schemas.beta_headers import (
    BetaHeaderCreate,
    BetaHeaderUpdate,
    BetaHeaderResponse,
    BetaHeaderListResponse,
)

router = APIRouter()


def get_manager():
    db_client = DynamoDBClient()
    return BetaHeaderManager(db_client)


@router.get("", response_model=BetaHeaderListResponse)
async def list_beta_headers(
    type: Optional[str] = Query(default=None, description="Filter by type: mapping or blocklist"),
    search: Optional[str] = Query(default=None, description="Search by header name"),
):
    """List all beta header rules."""
    manager = get_manager()
    items = manager.list_all()

    results = []
    for item in items:
        results.append(BetaHeaderResponse(
            header_name=item.get("header_name", ""),
            header_type=item.get("header_type", ""),
            mapped_to=item.get("mapped_to", []),
            description=item.get("description", ""),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
        ))

    if type:
        results = [r for r in results if r.header_type == type]

    if search:
        search_lower = search.lower()
        results = [r for r in results if search_lower in r.header_name.lower()]

    results.sort(key=lambda x: (0 if x.header_type == "blocklist" else 1, x.header_name))

    return BetaHeaderListResponse(items=results, count=len(results))


@router.get("/{header_name:path}", response_model=BetaHeaderResponse)
async def get_beta_header(header_name: str):
    """Get a specific beta header rule."""
    header_name = unquote(header_name)
    manager = get_manager()
    item = manager.get(header_name)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Beta header not found")

    return BetaHeaderResponse(
        header_name=item.get("header_name", ""),
        header_type=item.get("header_type", ""),
        mapped_to=item.get("mapped_to", []),
        description=item.get("description", ""),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
    )


@router.post("", response_model=BetaHeaderResponse, status_code=status.HTTP_201_CREATED)
async def create_beta_header(request: BetaHeaderCreate):
    """Create a new beta header rule."""
    manager = get_manager()

    existing = manager.get(request.header_name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Beta header already exists. Use PUT to update.",
        )

    if request.header_type == "mapping" and not request.mapped_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mapped_to is required for mapping type headers.",
        )

    item = manager.create(
        header_name=request.header_name,
        header_type=request.header_type,
        mapped_to=request.mapped_to,
        description=request.description,
    )

    return BetaHeaderResponse(**item)


@router.put("/{header_name:path}", response_model=BetaHeaderResponse)
async def update_beta_header(header_name: str, request: BetaHeaderUpdate):
    """Update an existing beta header rule."""
    header_name = unquote(header_name)
    manager = get_manager()

    existing = manager.get(header_name)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Beta header not found")

    updates = {}
    if request.header_type is not None:
        updates["header_type"] = request.header_type
    if request.mapped_to is not None:
        updates["mapped_to"] = request.mapped_to
    if request.description is not None:
        updates["description"] = request.description

    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    # Validate mapping type has mapped_to
    final_type = updates.get("header_type", existing.get("header_type"))
    final_mapped_to = updates.get("mapped_to", existing.get("mapped_to", []))
    if final_type == "mapping" and not final_mapped_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mapped_to is required for mapping type headers.",
        )

    updated = manager.update(header_name, updates)
    return BetaHeaderResponse(
        header_name=updated.get("header_name", ""),
        header_type=updated.get("header_type", ""),
        mapped_to=updated.get("mapped_to", []),
        description=updated.get("description", ""),
        created_at=updated.get("created_at", ""),
        updated_at=updated.get("updated_at", ""),
    )


@router.delete("/{header_name:path}")
async def delete_beta_header(header_name: str):
    """Delete a beta header rule."""
    header_name = unquote(header_name)
    manager = get_manager()

    deleted = manager.delete(header_name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Beta header not found")

    return {"message": "Beta header deleted successfully"}
```

**Step 3: Register router in main.py**

In `admin_portal/backend/main.py`:

Add to imports (line 37):
```python
from admin_portal.backend.api import beta_headers
```

Add router (after line 99):
```python
app.include_router(beta_headers.router, prefix=f"{API_PREFIX}/beta-headers", tags=["Beta Headers"])
```

**Step 4: Commit**

```bash
git add admin_portal/backend/schemas/beta_headers.py admin_portal/backend/api/beta_headers.py admin_portal/backend/main.py
git commit -m "feat: add beta headers admin API with CRUD endpoints"
```

---

### Task 7: Frontend — types, API service, hooks

**Files:**
- Create: `admin_portal/frontend/src/types/betaHeaders.ts`
- Modify: `admin_portal/frontend/src/types/index.ts`
- Modify: `admin_portal/frontend/src/services/api.ts`
- Create: `admin_portal/frontend/src/hooks/useBetaHeaders.ts`
- Modify: `admin_portal/frontend/src/hooks/index.ts`

**Step 1: Create TypeScript types**

Create `admin_portal/frontend/src/types/betaHeaders.ts`:

```typescript
export interface BetaHeader {
  header_name: string;
  header_type: 'mapping' | 'blocklist';
  mapped_to: string[];
  description: string;
  created_at: string;
  updated_at: string;
}

export interface BetaHeaderCreate {
  header_name: string;
  header_type: 'mapping' | 'blocklist';
  mapped_to?: string[];
  description?: string;
}

export interface BetaHeaderUpdate {
  header_type?: 'mapping' | 'blocklist';
  mapped_to?: string[];
  description?: string;
}

export interface BetaHeaderListResponse {
  items: BetaHeader[];
  count: number;
}
```

**Step 2: Add to types/index.ts**

```typescript
export * from './betaHeaders';
```

**Step 3: Add betaHeadersApi to services/api.ts**

Add at the end of `admin_portal/frontend/src/services/api.ts` (before the final export or at the end):

```typescript
export const betaHeadersApi = {
  list: async (params?: { type?: string; search?: string }): Promise<BetaHeaderListResponse> => {
    const searchParams = new URLSearchParams();
    if (params?.type) searchParams.set('type', params.type);
    if (params?.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    return apiFetch(`/beta-headers${query ? `?${query}` : ''}`);
  },

  get: async (headerName: string): Promise<BetaHeader> => {
    return apiFetch(`/beta-headers/${encodeURIComponent(headerName)}`);
  },

  create: async (data: BetaHeaderCreate): Promise<BetaHeader> => {
    return apiFetch('/beta-headers', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update: async (headerName: string, data: BetaHeaderUpdate): Promise<BetaHeader> => {
    return apiFetch(`/beta-headers/${encodeURIComponent(headerName)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete: async (headerName: string): Promise<{ message: string }> => {
    return apiFetch(`/beta-headers/${encodeURIComponent(headerName)}`, {
      method: 'DELETE',
    });
  },
};
```

Add the import for types at the top of api.ts (alongside existing type imports):
```typescript
import type { BetaHeader, BetaHeaderCreate, BetaHeaderUpdate, BetaHeaderListResponse } from '../types';
```

**Step 4: Create React Query hooks**

Create `admin_portal/frontend/src/hooks/useBetaHeaders.ts`:

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { betaHeadersApi } from '../services/api';
import type { BetaHeaderCreate, BetaHeaderUpdate } from '../types';

export function useBetaHeaders(params?: { type?: string; search?: string }) {
  return useQuery({
    queryKey: ['betaHeaders', params],
    queryFn: () => betaHeadersApi.list(params),
  });
}

export function useCreateBetaHeader() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: BetaHeaderCreate) => betaHeadersApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['betaHeaders'] });
    },
  });
}

export function useUpdateBetaHeader() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ headerName, data }: { headerName: string; data: BetaHeaderUpdate }) =>
      betaHeadersApi.update(headerName, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['betaHeaders'] });
    },
  });
}

export function useDeleteBetaHeader() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (headerName: string) => betaHeadersApi.delete(headerName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['betaHeaders'] });
    },
  });
}
```

**Step 5: Add to hooks/index.ts**

```typescript
export * from './useBetaHeaders';
```

**Step 6: Commit**

```bash
git add admin_portal/frontend/src/types/betaHeaders.ts admin_portal/frontend/src/types/index.ts admin_portal/frontend/src/services/api.ts admin_portal/frontend/src/hooks/useBetaHeaders.ts admin_portal/frontend/src/hooks/index.ts
git commit -m "feat: add frontend types, API service, and hooks for beta headers"
```

---

### Task 8: Frontend — i18n translations

**Files:**
- Modify: `admin_portal/frontend/src/i18n/en.json`
- Modify: `admin_portal/frontend/src/i18n/zh.json`

**Step 1: Add English translations**

In `en.json`, add `"betaHeaders"` key in the `nav` section (after `"providers"`):

```json
"betaHeaders": "Beta Headers"
```

Add a new top-level `"betaHeaders"` section (after the `"modelMapping"` section):

```json
"betaHeaders": {
  "title": "Beta Headers",
  "subtitle": "Manage how Anthropic beta headers are handled when proxied to Bedrock",
  "addHeader": "Add Header",
  "searchPlaceholder": "Search by header name...",
  "headerName": "Header Name",
  "type": "Type",
  "types": {
    "mapping": "Mapping",
    "blocklist": "Blocklist"
  },
  "mappedTo": "Mapped To",
  "description": "Description",
  "allTypes": "All Types",
  "form": {
    "createTitle": "Add Beta Header",
    "editTitle": "Edit Beta Header",
    "headerName": "Header Name",
    "headerNamePlaceholder": "e.g., my-feature-2026-01-01",
    "type": "Type",
    "mappedTo": "Mapped To (Bedrock Headers)",
    "mappedToPlaceholder": "Enter header name and press Enter",
    "description": "Description",
    "descriptionPlaceholder": "Optional note about this header rule",
    "save": "Save"
  },
  "confirmDelete": "Are you sure you want to delete this beta header rule? If it was a blocklist entry, the header will pass through to Bedrock.",
  "headerCreated": "Beta header created successfully",
  "headerUpdated": "Beta header updated successfully",
  "headerDeleted": "Beta header deleted successfully"
}
```

**Step 2: Add Chinese translations**

In `zh.json`, add `"betaHeaders"` in `nav`:

```json
"betaHeaders": "Beta Headers"
```

Add top-level `"betaHeaders"` section:

```json
"betaHeaders": {
  "title": "Beta Headers",
  "subtitle": "管理 Anthropic beta header 到 Bedrock 的代理处理规则",
  "addHeader": "添加 Header",
  "searchPlaceholder": "按 header 名称搜索...",
  "headerName": "Header 名称",
  "type": "类型",
  "types": {
    "mapping": "映射",
    "blocklist": "屏蔽"
  },
  "mappedTo": "映射到",
  "description": "描述",
  "allTypes": "所有类型",
  "form": {
    "createTitle": "添加 Beta Header",
    "editTitle": "编辑 Beta Header",
    "headerName": "Header 名称",
    "headerNamePlaceholder": "例如：my-feature-2026-01-01",
    "type": "类型",
    "mappedTo": "映射到（Bedrock Headers）",
    "mappedToPlaceholder": "输入 header 名称后按回车",
    "description": "描述",
    "descriptionPlaceholder": "关于此规则的可选备注",
    "save": "保存"
  },
  "confirmDelete": "确定要删除此 beta header 规则吗？如果是屏蔽项，删除后该 header 将会被透传到 Bedrock。",
  "headerCreated": "Beta header 创建成功",
  "headerUpdated": "Beta header 更新成功",
  "headerDeleted": "Beta header 删除成功"
}
```

**Step 3: Commit**

```bash
git add admin_portal/frontend/src/i18n/en.json admin_portal/frontend/src/i18n/zh.json
git commit -m "feat: add i18n translations for beta headers page"
```

---

### Task 9: Frontend — BetaHeaders page component

**Files:**
- Create: `admin_portal/frontend/src/pages/BetaHeaders.tsx`

**Step 1: Create the page component**

Create `admin_portal/frontend/src/pages/BetaHeaders.tsx`. Follow the `ModelMapping.tsx` pattern exactly — SlideOver panel for create/edit, delete confirmation modal, search + type filter. Key differences:

- Type filter dropdown (All / Mapping / Blocklist) next to search
- Type column with colored badges (blue for mapping, red for blocklist)
- Mapped To column showing tags/badges; "—" for blocklist
- Form has: header_name (text, readonly on edit), header_type (dropdown), mapped_to (tag input, only shown for mapping), description (textarea)
- Tag input for mapped_to: text input + Enter key to add, X button on each tag to remove

The component should use:
- `useBetaHeaders({ type: typeFilter || undefined, search: searchQuery || undefined })`
- `useCreateBetaHeader()`, `useUpdateBetaHeader()`, `useDeleteBetaHeader()`
- `useTranslation()` for all strings
- Same Tailwind dark theme classes as ModelMapping

**Step 2: Commit**

```bash
git add admin_portal/frontend/src/pages/BetaHeaders.tsx
git commit -m "feat: add BetaHeaders page component"
```

---

### Task 10: Frontend — routing, sidebar, exports

**Files:**
- Modify: `admin_portal/frontend/src/App.tsx`
- Modify: `admin_portal/frontend/src/components/Layout/Sidebar.tsx`
- Modify: `admin_portal/frontend/src/pages/index.ts`

**Step 1: Add route in App.tsx**

Import BetaHeaders:
```typescript
import BetaHeaders from './pages/BetaHeaders';
```

Add route after the model-mapping route (line 64):
```tsx
<Route path="/beta-headers" element={<BetaHeaders />} />
```

**Step 2: Add nav item in Sidebar.tsx**

In the `navItems` array, after the providers item (line 22), add:
```typescript
{ path: '/beta-headers', icon: 'tune', label: t('nav.betaHeaders'), section: 'config' },
```

**Step 3: Add to pages/index.ts**

```typescript
export { default as BetaHeaders } from './BetaHeaders';
```

**Step 4: Build frontend to verify**

```bash
cd admin_portal/frontend && npm run build 2>&1 | tail -5
```

Expected: Build succeeds with no TypeScript errors.

**Step 5: Commit**

```bash
git add admin_portal/frontend/src/App.tsx admin_portal/frontend/src/components/Layout/Sidebar.tsx admin_portal/frontend/src/pages/index.ts
git commit -m "feat: wire up BetaHeaders page in routing and sidebar"
```

---

### Task 11: Verification

**Step 1: Run all tests**

```bash
uv run pytest tests/ -x -q
```

Expected: All tests pass.

**Step 2: Start admin portal and test manually**

```bash
# Start admin portal backend
uv run uvicorn admin_portal.backend.main:app --host 0.0.0.0 --port 8005 --reload &

# Test API endpoints
curl -s http://localhost:8005/api/beta-headers | python -m json.tool
curl -s -X POST http://localhost:8005/api/beta-headers -H 'Content-Type: application/json' -d '{"header_name":"test-header-2026-01-01","header_type":"blocklist","description":"test"}' | python -m json.tool
curl -s -X DELETE http://localhost:8005/api/beta-headers/test-header-2026-01-01 | python -m json.tool
```

**Step 3: Start frontend dev server and test UI**

```bash
cd admin_portal/frontend && npm run dev
```

Verify in browser:
- Beta Headers page appears in sidebar under Configuration
- List shows seeded data (3 blocklist + 1 mapping)
- Can create new blocklist entry
- Can create new mapping entry with tag input
- Can edit existing entry
- Can delete entry with confirmation
- Type filter works
- Search works

**Step 4: Final commit if any fixes needed**
