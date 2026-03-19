# Model Mapping Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Model Mapping configuration page to admin_portal for managing Anthropic-to-Bedrock model ID mappings.

**Architecture:** Backend FastAPI router reusing existing `ModelMappingManager` from `app/db/dynamodb.py`. Frontend React page following Pricing page patterns with TanStack Query hooks.

**Tech Stack:** FastAPI, Pydantic, React 18, TypeScript, TanStack Query, TailwindCSS

---

## Task 1: Backend Pydantic Schemas

**Files:**
- Create: `admin_portal/backend/schemas/model_mapping.py`

**Step 1: Create the schema file**

```python
"""Model Mapping schemas."""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class ModelMappingCreate(BaseModel):
    """Schema for creating a model mapping."""

    anthropic_model_id: str = Field(..., description="Anthropic model ID (e.g., 'opus', 'claude-opus-4-5-20251101')")
    bedrock_model_id: str = Field(..., description="Bedrock model ARN (e.g., 'global.anthropic.claude-opus-4-5-20251101-v1:0')")


class ModelMappingUpdate(BaseModel):
    """Schema for updating a model mapping."""

    bedrock_model_id: str = Field(..., description="New Bedrock model ARN")


class ModelMappingResponse(BaseModel):
    """Schema for model mapping response."""

    anthropic_model_id: str
    bedrock_model_id: str
    source: Literal["default", "custom"]
    updated_at: Optional[int] = None

    class Config:
        extra = "allow"


class ModelMappingListResponse(BaseModel):
    """Schema for model mapping list response."""

    items: List[ModelMappingResponse]
    count: int
```

**Step 2: Verify file created**

Run: `cat admin_portal/backend/schemas/model_mapping.py | head -20`
Expected: Shows the schema file content

**Step 3: Commit**

```bash
git add admin_portal/backend/schemas/model_mapping.py
git commit -m "feat(admin): add model mapping Pydantic schemas"
```

---

## Task 2: Backend API Router

**Files:**
- Create: `admin_portal/backend/api/model_mapping.py`

**Step 1: Create the API router file**

```python
"""Model Mapping management routes."""
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, HTTPException, Query, status

from app.db.dynamodb import DynamoDBClient, ModelMappingManager
from app.core.config import settings
from admin_portal.backend.schemas.model_mapping import (
    ModelMappingCreate,
    ModelMappingUpdate,
    ModelMappingResponse,
    ModelMappingListResponse,
)

router = APIRouter()


def get_manager():
    """Get ModelMappingManager instance."""
    db_client = DynamoDBClient()
    return ModelMappingManager(db_client)


@router.get("", response_model=ModelMappingListResponse)
async def list_model_mappings(
    search: Optional[str] = Query(default=None),
):
    """
    List all model mappings (default + custom).

    Default mappings come from config.py, custom mappings from DynamoDB.
    If same anthropic_model_id exists in both, custom takes priority.
    """
    mapping_manager = get_manager()

    # Get custom mappings from DynamoDB
    custom_mappings = mapping_manager.list_mappings()
    custom_ids = {m.get("anthropic_model_id") for m in custom_mappings}

    # Build combined list
    items = []

    # Add default mappings (only if not overridden by custom)
    for anthropic_id, bedrock_id in settings.default_model_mapping.items():
        if anthropic_id not in custom_ids:
            items.append(ModelMappingResponse(
                anthropic_model_id=anthropic_id,
                bedrock_model_id=bedrock_id,
                source="default",
            ))

    # Add custom mappings
    for mapping in custom_mappings:
        items.append(ModelMappingResponse(
            anthropic_model_id=mapping.get("anthropic_model_id", ""),
            bedrock_model_id=mapping.get("bedrock_model_id", ""),
            source="custom",
            updated_at=mapping.get("updated_at"),
        ))

    # Apply search filter if provided
    if search:
        search_lower = search.lower()
        items = [
            item for item in items
            if search_lower in item.anthropic_model_id.lower()
            or search_lower in item.bedrock_model_id.lower()
        ]

    # Sort by source (default first) then by anthropic_model_id
    items.sort(key=lambda x: (0 if x.source == "default" else 1, x.anthropic_model_id))

    return ModelMappingListResponse(items=items, count=len(items))


@router.get("/{anthropic_model_id:path}", response_model=ModelMappingResponse)
async def get_model_mapping(anthropic_model_id: str):
    """
    Get a specific model mapping.
    """
    anthropic_model_id = unquote(anthropic_model_id)
    mapping_manager = get_manager()

    # Check custom mapping first
    bedrock_id = mapping_manager.get_mapping(anthropic_model_id)
    if bedrock_id:
        # Get full item for updated_at
        mappings = mapping_manager.list_mappings()
        for m in mappings:
            if m.get("anthropic_model_id") == anthropic_model_id:
                return ModelMappingResponse(
                    anthropic_model_id=anthropic_model_id,
                    bedrock_model_id=bedrock_id,
                    source="custom",
                    updated_at=m.get("updated_at"),
                )

    # Check default mapping
    if anthropic_model_id in settings.default_model_mapping:
        return ModelMappingResponse(
            anthropic_model_id=anthropic_model_id,
            bedrock_model_id=settings.default_model_mapping[anthropic_model_id],
            source="default",
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Model mapping not found",
    )


@router.post("", response_model=ModelMappingResponse, status_code=status.HTTP_201_CREATED)
async def create_model_mapping(request: ModelMappingCreate):
    """
    Create a new custom model mapping.

    Can override a default mapping by using the same anthropic_model_id.
    """
    mapping_manager = get_manager()

    # Check if custom mapping already exists
    existing = mapping_manager.get_mapping(request.anthropic_model_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Custom mapping for this model already exists. Use PUT to update.",
        )

    # Create the mapping
    mapping_manager.set_mapping(request.anthropic_model_id, request.bedrock_model_id)

    # Get the created item
    mappings = mapping_manager.list_mappings()
    for m in mappings:
        if m.get("anthropic_model_id") == request.anthropic_model_id:
            return ModelMappingResponse(
                anthropic_model_id=request.anthropic_model_id,
                bedrock_model_id=request.bedrock_model_id,
                source="custom",
                updated_at=m.get("updated_at"),
            )

    return ModelMappingResponse(
        anthropic_model_id=request.anthropic_model_id,
        bedrock_model_id=request.bedrock_model_id,
        source="custom",
    )


@router.put("/{anthropic_model_id:path}", response_model=ModelMappingResponse)
async def update_model_mapping(anthropic_model_id: str, request: ModelMappingUpdate):
    """
    Update an existing custom model mapping.

    Cannot update default mappings - create a custom override instead.
    """
    anthropic_model_id = unquote(anthropic_model_id)
    mapping_manager = get_manager()

    # Check if custom mapping exists
    existing = mapping_manager.get_mapping(anthropic_model_id)
    if not existing:
        # Check if it's a default mapping
        if anthropic_model_id in settings.default_model_mapping:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update default mapping. Create a custom override with POST instead.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom mapping not found",
        )

    # Update the mapping
    mapping_manager.set_mapping(anthropic_model_id, request.bedrock_model_id)

    # Get updated item
    mappings = mapping_manager.list_mappings()
    for m in mappings:
        if m.get("anthropic_model_id") == anthropic_model_id:
            return ModelMappingResponse(
                anthropic_model_id=anthropic_model_id,
                bedrock_model_id=request.bedrock_model_id,
                source="custom",
                updated_at=m.get("updated_at"),
            )

    return ModelMappingResponse(
        anthropic_model_id=anthropic_model_id,
        bedrock_model_id=request.bedrock_model_id,
        source="custom",
    )


@router.delete("/{anthropic_model_id:path}")
async def delete_model_mapping(anthropic_model_id: str):
    """
    Delete a custom model mapping.

    Cannot delete default mappings.
    """
    anthropic_model_id = unquote(anthropic_model_id)
    mapping_manager = get_manager()

    # Check if custom mapping exists
    existing = mapping_manager.get_mapping(anthropic_model_id)
    if not existing:
        # Check if it's a default mapping
        if anthropic_model_id in settings.default_model_mapping:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete default mapping",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom mapping not found",
        )

    mapping_manager.delete_mapping(anthropic_model_id)
    return {"message": "Mapping deleted successfully"}
```

**Step 2: Verify file created**

Run: `wc -l admin_portal/backend/api/model_mapping.py`
Expected: ~180 lines

**Step 3: Commit**

```bash
git add admin_portal/backend/api/model_mapping.py
git commit -m "feat(admin): add model mapping API router"
```

---

## Task 3: Register Backend Router

**Files:**
- Modify: `admin_portal/backend/main.py:35` and `admin_portal/backend/main.py:92`

**Step 1: Add import**

Add `model_mapping` to the import line at line 35:

```python
from admin_portal.backend.api import auth, api_keys, pricing, dashboard, model_mapping
```

**Step 2: Register router**

Add after line 92 (after the pricing router):

```python
app.include_router(model_mapping.router, prefix=f"{API_PREFIX}/model-mapping", tags=["Model Mapping"])
```

**Step 3: Verify changes**

Run: `grep -n "model_mapping" admin_portal/backend/main.py`
Expected: Shows import and router registration lines

**Step 4: Commit**

```bash
git add admin_portal/backend/main.py
git commit -m "feat(admin): register model mapping router in main.py"
```

---

## Task 4: Frontend TypeScript Types

**Files:**
- Create: `admin_portal/frontend/src/types/modelMapping.ts`
- Modify: `admin_portal/frontend/src/types/index.ts`

**Step 1: Create types file**

```typescript
export interface ModelMapping {
  anthropic_model_id: string;
  bedrock_model_id: string;
  source: 'default' | 'custom';
  updated_at?: number;
}

export interface ModelMappingCreate {
  anthropic_model_id: string;
  bedrock_model_id: string;
}

export interface ModelMappingUpdate {
  bedrock_model_id: string;
}

export interface ModelMappingListResponse {
  items: ModelMapping[];
  count: number;
}
```

**Step 2: Export from index**

Add to `admin_portal/frontend/src/types/index.ts`:

```typescript
export * from './modelMapping';
```

**Step 3: Verify**

Run: `cat admin_portal/frontend/src/types/index.ts`
Expected: Shows 4 export lines including modelMapping

**Step 4: Commit**

```bash
git add admin_portal/frontend/src/types/modelMapping.ts admin_portal/frontend/src/types/index.ts
git commit -m "feat(admin): add model mapping TypeScript types"
```

---

## Task 5: Frontend API Service

**Files:**
- Modify: `admin_portal/frontend/src/services/api.ts`

**Step 1: Add import types**

Add to the import at line 9-20:

```typescript
import type {
  // ... existing imports ...
  ModelMapping,
  ModelMappingCreate,
  ModelMappingUpdate,
  ModelMappingListResponse,
} from '../types';
```

**Step 2: Add modelMappingApi**

Add after `pricingApi` (after line 273):

```typescript
// Model Mapping API
export const modelMappingApi = {
  list: async (params?: { search?: string }): Promise<ModelMappingListResponse> => {
    const searchParams = new URLSearchParams();
    if (params?.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    return apiFetch(`/model-mapping${query ? `?${query}` : ''}`);
  },

  get: async (anthropicModelId: string): Promise<ModelMapping> => {
    return apiFetch(`/model-mapping/${encodeURIComponent(anthropicModelId)}`);
  },

  create: async (data: ModelMappingCreate): Promise<ModelMapping> => {
    return apiFetch('/model-mapping', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update: async (anthropicModelId: string, data: ModelMappingUpdate): Promise<ModelMapping> => {
    return apiFetch(`/model-mapping/${encodeURIComponent(anthropicModelId)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete: async (anthropicModelId: string): Promise<{ message: string }> => {
    return apiFetch(`/model-mapping/${encodeURIComponent(anthropicModelId)}`, {
      method: 'DELETE',
    });
  },
};
```

**Step 3: Verify**

Run: `grep -n "modelMappingApi" admin_portal/frontend/src/services/api.ts`
Expected: Shows the modelMappingApi definition

**Step 4: Commit**

```bash
git add admin_portal/frontend/src/services/api.ts
git commit -m "feat(admin): add model mapping API service"
```

---

## Task 6: Frontend React Query Hooks

**Files:**
- Create: `admin_portal/frontend/src/hooks/useModelMapping.ts`

**Step 1: Create hooks file**

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { modelMappingApi } from '../services/api';
import type { ModelMappingCreate, ModelMappingUpdate } from '../types';

export function useModelMappings(params?: { search?: string }) {
  return useQuery({
    queryKey: ['modelMappings', params],
    queryFn: () => modelMappingApi.list(params),
  });
}

export function useModelMapping(anthropicModelId: string) {
  return useQuery({
    queryKey: ['modelMapping', anthropicModelId],
    queryFn: () => modelMappingApi.get(anthropicModelId),
    enabled: !!anthropicModelId,
  });
}

export function useCreateModelMapping() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: ModelMappingCreate) => modelMappingApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelMappings'] });
    },
  });
}

export function useUpdateModelMapping() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ anthropicModelId, data }: { anthropicModelId: string; data: ModelMappingUpdate }) =>
      modelMappingApi.update(anthropicModelId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelMappings'] });
    },
  });
}

export function useDeleteModelMapping() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (anthropicModelId: string) => modelMappingApi.delete(anthropicModelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelMappings'] });
    },
  });
}
```

**Step 2: Verify**

Run: `wc -l admin_portal/frontend/src/hooks/useModelMapping.ts`
Expected: ~50 lines

**Step 3: Commit**

```bash
git add admin_portal/frontend/src/hooks/useModelMapping.ts
git commit -m "feat(admin): add model mapping React Query hooks"
```

---

## Task 7: i18n Translations

**Files:**
- Modify: `admin_portal/frontend/src/i18n/en.json`
- Modify: `admin_portal/frontend/src/i18n/zh.json`

**Step 1: Add English translations**

Add `"modelMapping"` key to nav section and new `"modelMapping"` section after `"pricing"`:

In nav section (around line 69-78), add:
```json
"modelMapping": "Model Mapping"
```

After the `"pricing"` section (after line 204), add:
```json
"modelMapping": {
  "title": "Model Mapping",
  "subtitle": "Manage Anthropic to Bedrock model ID mappings",
  "addMapping": "Add Mapping",
  "searchPlaceholder": "Search by model ID...",
  "anthropicModelId": "Anthropic Model ID",
  "bedrockModelId": "Bedrock Model ID",
  "source": "Source",
  "sources": {
    "default": "Default",
    "custom": "Custom"
  },
  "form": {
    "createTitle": "Add New Mapping",
    "editTitle": "Edit Mapping",
    "anthropicModelId": "Anthropic Model ID",
    "anthropicModelIdPlaceholder": "e.g., opus or claude-opus-4-5-20251101",
    "bedrockModelId": "Bedrock Model ID",
    "bedrockModelIdPlaceholder": "e.g., global.anthropic.claude-opus-4-5-20251101-v1:0",
    "save": "Save Mapping"
  },
  "confirmDelete": "Are you sure you want to delete this mapping?",
  "mappingCreated": "Model mapping created successfully",
  "mappingUpdated": "Model mapping updated successfully",
  "mappingDeleted": "Model mapping deleted successfully",
  "cannotEditDefault": "Default mappings cannot be edited. Create a custom override instead.",
  "cannotDeleteDefault": "Default mappings cannot be deleted."
}
```

**Step 2: Add Chinese translations**

In nav section, add:
```json
"modelMapping": "模型映射"
```

After the `"pricing"` section, add:
```json
"modelMapping": {
  "title": "模型映射",
  "subtitle": "管理 Anthropic 到 Bedrock 模型 ID 的映射关系",
  "addMapping": "添加映射",
  "searchPlaceholder": "按模型 ID 搜索...",
  "anthropicModelId": "Anthropic 模型 ID",
  "bedrockModelId": "Bedrock 模型 ID",
  "source": "来源",
  "sources": {
    "default": "默认",
    "custom": "自定义"
  },
  "form": {
    "createTitle": "添加新映射",
    "editTitle": "编辑映射",
    "anthropicModelId": "Anthropic 模型 ID",
    "anthropicModelIdPlaceholder": "例如：opus 或 claude-opus-4-5-20251101",
    "bedrockModelId": "Bedrock 模型 ID",
    "bedrockModelIdPlaceholder": "例如：global.anthropic.claude-opus-4-5-20251101-v1:0",
    "save": "保存映射"
  },
  "confirmDelete": "确定要删除此映射吗？",
  "mappingCreated": "模型映射创建成功",
  "mappingUpdated": "模型映射更新成功",
  "mappingDeleted": "模型映射删除成功",
  "cannotEditDefault": "无法编辑默认映射。请创建自定义覆盖。",
  "cannotDeleteDefault": "无法删除默认映射。"
}
```

**Step 3: Verify**

Run: `grep -c "modelMapping" admin_portal/frontend/src/i18n/en.json`
Expected: Multiple matches (nav + section)

**Step 4: Commit**

```bash
git add admin_portal/frontend/src/i18n/en.json admin_portal/frontend/src/i18n/zh.json
git commit -m "feat(admin): add model mapping i18n translations"
```

---

## Task 8: Frontend Page Component

**Files:**
- Create: `admin_portal/frontend/src/pages/ModelMapping.tsx`

**Step 1: Create the page component**

```tsx
import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  useModelMappings,
  useCreateModelMapping,
  useUpdateModelMapping,
  useDeleteModelMapping,
} from '../hooks/useModelMapping';
import { SlideOver } from '../components/SlideOver';
import type { ModelMapping, ModelMappingCreate } from '../types';

export default function ModelMappingPage() {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState('');
  const [showCreatePanel, setShowCreatePanel] = useState(false);
  const [editingMapping, setEditingMapping] = useState<ModelMapping | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const { data, isLoading, error } = useModelMappings();
  const createMutation = useCreateModelMapping();
  const updateMutation = useUpdateModelMapping();
  const deleteMutation = useDeleteModelMapping();

  // Filter items based on search
  const filteredItems = useMemo(() => {
    if (!data?.items) return [];
    if (!searchQuery) return data.items;

    const query = searchQuery.toLowerCase();
    return data.items.filter(
      (item) =>
        item.anthropic_model_id.toLowerCase().includes(query) ||
        item.bedrock_model_id.toLowerCase().includes(query)
    );
  }, [data?.items, searchQuery]);

  const handleCreate = async (formData: ModelMappingCreate) => {
    try {
      await createMutation.mutateAsync(formData);
      setShowCreatePanel(false);
    } catch (err) {
      console.error('Failed to create mapping:', err);
    }
  };

  const handleUpdate = async (anthropicModelId: string, bedrockModelId: string) => {
    try {
      await updateMutation.mutateAsync({
        anthropicModelId,
        data: { bedrock_model_id: bedrockModelId },
      });
      setEditingMapping(null);
    } catch (err) {
      console.error('Failed to update mapping:', err);
    }
  };

  const handleDelete = async (anthropicModelId: string) => {
    try {
      await deleteMutation.mutateAsync(anthropicModelId);
      setDeleteConfirm(null);
    } catch (err) {
      console.error('Failed to delete mapping:', err);
    }
  };

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-red-400">
          {t('common.error')}: {(error as Error).message}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">{t('modelMapping.title')}</h1>
          <p className="text-slate-400 mt-1">{t('modelMapping.subtitle')}</p>
        </div>
        <button
          onClick={() => setShowCreatePanel(true)}
          className="flex items-center gap-2 px-4 py-2.5 bg-primary hover:bg-primary/90 text-white rounded-lg font-medium transition-colors shadow-lg shadow-blue-500/30"
        >
          <span className="material-symbols-outlined text-[20px]">add</span>
          {t('modelMapping.addMapping')}
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">
          search
        </span>
        <input
          type="text"
          placeholder={t('modelMapping.searchPlaceholder')}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
        />
      </div>

      {/* Table */}
      <div className="bg-surface-dark border border-border-dark rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border-dark">
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('modelMapping.anthropicModelId')}
              </th>
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('modelMapping.bedrockModelId')}
              </th>
              <th className="text-left px-6 py-4 text-sm font-semibold text-slate-300">
                {t('modelMapping.source')}
              </th>
              <th className="text-right px-6 py-4 text-sm font-semibold text-slate-300">
                {t('common.actions')}
              </th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={4} className="px-6 py-12 text-center text-slate-400">
                  {t('common.loading')}
                </td>
              </tr>
            ) : filteredItems.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-6 py-12 text-center text-slate-400">
                  No mappings found
                </td>
              </tr>
            ) : (
              filteredItems.map((item) => (
                <tr
                  key={item.anthropic_model_id}
                  className="border-b border-border-dark last:border-0 hover:bg-slate-800/50 group"
                >
                  <td className="px-6 py-4">
                    <code className="text-sm text-white bg-slate-800 px-2 py-1 rounded">
                      {item.anthropic_model_id}
                    </code>
                  </td>
                  <td className="px-6 py-4">
                    <code className="text-sm text-slate-300 bg-slate-800/50 px-2 py-1 rounded">
                      {item.bedrock_model_id}
                    </code>
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium ${
                        item.source === 'default'
                          ? 'bg-slate-700 text-slate-300'
                          : 'bg-blue-500/20 text-blue-400'
                      }`}
                    >
                      {t(`modelMapping.sources.${item.source}`)}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-right">
                    {item.source === 'custom' ? (
                      <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={() => setEditingMapping(item)}
                          className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
                          title={t('common.edit')}
                        >
                          <span className="material-symbols-outlined text-[18px]">edit</span>
                        </button>
                        <button
                          onClick={() => setDeleteConfirm(item.anthropic_model_id)}
                          className="p-2 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                          title={t('common.delete')}
                        >
                          <span className="material-symbols-outlined text-[18px]">delete</span>
                        </button>
                      </div>
                    ) : (
                      <span className="text-xs text-slate-500">—</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Create SlideOver */}
      <SlideOver
        isOpen={showCreatePanel}
        onClose={() => setShowCreatePanel(false)}
        title={t('modelMapping.form.createTitle')}
      >
        <MappingForm
          onSubmit={handleCreate}
          onCancel={() => setShowCreatePanel(false)}
          isLoading={createMutation.isPending}
        />
      </SlideOver>

      {/* Edit SlideOver */}
      <SlideOver
        isOpen={!!editingMapping}
        onClose={() => setEditingMapping(null)}
        title={t('modelMapping.form.editTitle')}
      >
        {editingMapping && (
          <MappingForm
            initialData={editingMapping}
            onSubmit={(data) => handleUpdate(editingMapping.anthropic_model_id, data.bedrock_model_id)}
            onCancel={() => setEditingMapping(null)}
            isLoading={updateMutation.isPending}
            isEdit
          />
        )}
      </SlideOver>

      {/* Delete Confirmation */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-surface-dark border border-border-dark rounded-xl p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold text-white mb-2">{t('common.confirm')}</h3>
            <p className="text-slate-400 mb-6">{t('modelMapping.confirmDelete')}</p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteConfirm(null)}
                className="px-4 py-2 border border-border-dark text-slate-300 rounded-lg hover:bg-slate-800 transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={() => handleDelete(deleteConfirm)}
                disabled={deleteMutation.isPending}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors disabled:opacity-50"
              >
                {deleteMutation.isPending ? t('common.loading') : t('common.delete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Form Component
interface MappingFormProps {
  initialData?: ModelMapping;
  onSubmit: (data: ModelMappingCreate) => void;
  onCancel: () => void;
  isLoading: boolean;
  isEdit?: boolean;
}

function MappingForm({ initialData, onSubmit, onCancel, isLoading, isEdit }: MappingFormProps) {
  const { t } = useTranslation();
  const [anthropicModelId, setAnthropicModelId] = useState(initialData?.anthropic_model_id || '');
  const [bedrockModelId, setBedrockModelId] = useState(initialData?.bedrock_model_id || '');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      anthropic_model_id: anthropicModelId,
      bedrock_model_id: bedrockModelId,
    });
  };

  const isValid = anthropicModelId.trim() && bedrockModelId.trim();

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div>
        <label className="block text-sm font-medium text-slate-300 mb-2">
          {t('modelMapping.form.anthropicModelId')} *
        </label>
        <input
          type="text"
          value={anthropicModelId}
          onChange={(e) => setAnthropicModelId(e.target.value)}
          placeholder={t('modelMapping.form.anthropicModelIdPlaceholder')}
          disabled={isEdit}
          className="w-full px-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary disabled:opacity-50 disabled:cursor-not-allowed"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-slate-300 mb-2">
          {t('modelMapping.form.bedrockModelId')} *
        </label>
        <input
          type="text"
          value={bedrockModelId}
          onChange={(e) => setBedrockModelId(e.target.value)}
          placeholder={t('modelMapping.form.bedrockModelIdPlaceholder')}
          className="w-full px-4 py-2.5 bg-input-bg border border-border-dark rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
        />
      </div>

      <div className="flex justify-end gap-3 pt-4 border-t border-border-dark">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2.5 border border-border-dark text-slate-300 rounded-lg hover:bg-slate-800 transition-colors"
        >
          {t('common.cancel')}
        </button>
        <button
          type="submit"
          disabled={!isValid || isLoading}
          className="px-4 py-2.5 bg-primary hover:bg-primary/90 text-white rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? t('common.loading') : t('modelMapping.form.save')}
        </button>
      </div>
    </form>
  );
}
```

**Step 2: Verify**

Run: `wc -l admin_portal/frontend/src/pages/ModelMapping.tsx`
Expected: ~280 lines

**Step 3: Commit**

```bash
git add admin_portal/frontend/src/pages/ModelMapping.tsx
git commit -m "feat(admin): add model mapping page component"
```

---

## Task 9: Register Page Route and Navigation

**Files:**
- Modify: `admin_portal/frontend/src/pages/index.ts`
- Modify: `admin_portal/frontend/src/App.tsx`
- Modify: `admin_portal/frontend/src/components/Layout/Sidebar.tsx`

**Step 1: Export page from index**

Add to `admin_portal/frontend/src/pages/index.ts`:

```typescript
export { default as ModelMapping } from './ModelMapping';
```

**Step 2: Add route to App.tsx**

Update import at line 3:

```typescript
import { Login, Dashboard, ApiKeys, Pricing, ModelMapping } from './pages';
```

Add route after `Pricing` route (around line 59):

```tsx
<Route path="/model-mapping" element={<ModelMapping />} />
```

**Step 3: Add navigation item to Sidebar.tsx**

Add to `navItems` array (around line 20, after pricing):

```typescript
{ path: '/model-mapping', icon: 'swap_horiz', label: t('nav.modelMapping'), section: 'config' },
```

**Step 4: Verify routes**

Run: `grep -n "model-mapping\|ModelMapping" admin_portal/frontend/src/App.tsx admin_portal/frontend/src/pages/index.ts admin_portal/frontend/src/components/Layout/Sidebar.tsx`
Expected: Shows imports and route definitions

**Step 5: Commit**

```bash
git add admin_portal/frontend/src/pages/index.ts admin_portal/frontend/src/App.tsx admin_portal/frontend/src/components/Layout/Sidebar.tsx
git commit -m "feat(admin): register model mapping route and navigation"
```

---

## Task 10: Test Backend API

**Step 1: Start the backend**

Run: `cd admin_portal/backend && python -m uvicorn main:app --reload --port 8005 &`
Expected: Server starts on port 8005

**Step 2: Test list endpoint**

Run: `curl -s http://localhost:8005/api/model-mapping | python -m json.tool | head -30`
Expected: JSON with items array containing default mappings

**Step 3: Test create endpoint**

Run: `curl -s -X POST http://localhost:8005/api/model-mapping -H "Content-Type: application/json" -d '{"anthropic_model_id":"test-alias","bedrock_model_id":"test.bedrock.model-v1:0"}' | python -m json.tool`
Expected: JSON with created mapping, source="custom"

**Step 4: Test delete endpoint**

Run: `curl -s -X DELETE http://localhost:8005/api/model-mapping/test-alias | python -m json.tool`
Expected: `{"message": "Mapping deleted successfully"}`

**Step 5: Stop test server**

Run: `pkill -f "uvicorn main:app"`

**Step 6: Commit (no changes, just verification)**

No commit needed - this was a verification step.

---

## Task 11: Test Frontend Build

**Step 1: Install dependencies if needed**

Run: `cd admin_portal/frontend && npm install`
Expected: Dependencies installed

**Step 2: Type check**

Run: `cd admin_portal/frontend && npm run type-check 2>&1 || npx tsc --noEmit`
Expected: No TypeScript errors

**Step 3: Build**

Run: `cd admin_portal/frontend && npm run build`
Expected: Build succeeds, output in dist/

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat(admin): complete model mapping page implementation" --allow-empty
```

---

## Summary

This plan implements:

1. **Backend**: Pydantic schemas + FastAPI router reusing `ModelMappingManager`
2. **Frontend**: TypeScript types, API service, React Query hooks, page component
3. **Integration**: Router registration, route/navigation setup, i18n

All follows existing patterns from Pricing page for consistency.
