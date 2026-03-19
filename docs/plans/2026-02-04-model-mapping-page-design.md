# Model Mapping Page Design

**Date**: 2026-02-04
**Status**: Approved

## Overview

Add a **Model Mapping** page under the Configuration navigation in admin_portal to manage Anthropic Model ID to Bedrock Model ID mappings.

### Use Cases

1. **New Model Onboarding** - Quickly add mappings when Anthropic releases new models
2. **Custom Aliases** - Allow short aliases (e.g., `opus` → `global.anthropic.claude-opus-4-5-20251101-v1:0`)

### Key Decisions

- **UI Style**: Simple table (consistent with Pricing page)
- **Data Display**: Show both default (from config.py) and custom (from DynamoDB) mappings with source labels
- **No Test Connection**: Keep it simple, no Bedrock API validation

---

## Data Model

### DynamoDB Table (existing)

Table: `anthropic-proxy-model-mapping`

| Field | Type | Description |
|-------|------|-------------|
| `anthropic_model_id` (PK) | String | Anthropic model ID |
| `bedrock_model_id` | String | Bedrock model ARN |
| `updated_at` | Number | Update timestamp |

### Frontend Type

```typescript
interface ModelMapping {
  anthropic_model_id: string;
  bedrock_model_id: string;
  source: 'default' | 'custom';
  updated_at?: number;
}
```

---

## API Design

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/model-mapping` | Get all mappings (merged default + custom) |
| POST | `/api/model-mapping` | Create custom mapping |
| PUT | `/api/model-mapping/{anthropic_model_id}` | Update custom mapping |
| DELETE | `/api/model-mapping/{anthropic_model_id}` | Delete custom mapping |

### GET Response Example

```json
{
  "items": [
    {
      "anthropic_model_id": "claude-opus-4-5-20251101",
      "bedrock_model_id": "global.anthropic.claude-opus-4-5-20251101-v1:0",
      "source": "default"
    },
    {
      "anthropic_model_id": "opus",
      "bedrock_model_id": "global.anthropic.claude-opus-4-5-20251101-v1:0",
      "source": "custom",
      "updated_at": 1706745600
    }
  ],
  "count": 2
}
```

### Business Logic

- GET merges two sources: `config.default_model_mapping` + DynamoDB table
- If same `anthropic_model_id` exists in both, DynamoDB (custom) takes priority
- POST/PUT/DELETE only operate on DynamoDB, do not affect config.py defaults

---

## Frontend Components

### File Structure

```
admin_portal/frontend/src/
├── pages/
│   └── ModelMapping.tsx        # Main page (reference Pricing.tsx)
├── hooks/
│   └── useModelMapping.ts      # React Query hooks
├── types/
│   └── modelMapping.ts         # TypeScript types
└── services/
    └── api.ts                  # Add modelMappingApi
```

### Page Layout

```
┌─────────────────────────────────────────────────────────┐
│  Model Mapping                           [+ Add Mapping]│
│  Manage Anthropic to Bedrock model ID mappings          │
├─────────────────────────────────────────────────────────┤
│  [🔍 Search...]                                         │
├─────────────────────────────────────────────────────────┤
│  Anthropic Model ID    Bedrock Model ID       Source    │
│  ─────────────────────────────────────────────────────  │
│  claude-opus-4-5...    global.anthropic...   [Default]  │
│  claude-sonnet-4-5...  global.anthropic...   [Default]  │
│  opus                  global.anthropic...   [Custom] ✏️🗑️│
│  sonnet                global.anthropic...   [Custom] ✏️🗑️│
└─────────────────────────────────────────────────────────┘
```

### Interaction

- **Default label**: Gray background, no edit/delete buttons
- **Custom label**: Blue background, hover shows edit/delete buttons
- **Add Mapping**: Opens SlideOver panel with two input fields
- **Search**: Frontend filtering, matches both anthropic_model_id and bedrock_model_id

### SlideOver Form

```
┌─────────────────────────────────────┐
│  Add New Mapping              [✕]  │
├─────────────────────────────────────┤
│                                     │
│  Anthropic Model ID *               │
│  ┌─────────────────────────────┐   │
│  │ e.g., opus                  │   │
│  └─────────────────────────────┘   │
│                                     │
│  Bedrock Model ID *                 │
│  ┌─────────────────────────────┐   │
│  │ e.g., global.anthropic...   │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────┐  ┌─────────────────┐  │
│  │ Cancel  │  │  Save Mapping   │  │
│  └─────────┘  └─────────────────┘  │
└─────────────────────────────────────┘
```

### Form Validation

- Both fields required
- Anthropic Model ID cannot duplicate existing custom mappings (except when editing)
- Allowed to override default mappings (creates same-name custom mapping)

---

## Navigation & Routing

### App.tsx

```typescript
<Route path="/model-mapping" element={<ModelMapping />} />
```

### Sidebar.tsx

```typescript
{ path: '/model-mapping', icon: 'swap_horiz', label: 'nav.modelMapping', section: 'config' }
```

Position: Under Configuration section, after Pricing.

---

## i18n Keys

### English (en.json)

```json
{
  "nav": {
    "modelMapping": "Model Mapping"
  },
  "modelMapping": {
    "title": "Model Mapping",
    "subtitle": "Manage Anthropic to Bedrock model ID mappings",
    "addMapping": "Add Mapping",
    "form": {
      "createTitle": "Add New Mapping",
      "editTitle": "Edit Mapping",
      "anthropicModelId": "Anthropic Model ID",
      "bedrockModelId": "Bedrock Model ID",
      "save": "Save Mapping"
    },
    "source": {
      "default": "Default",
      "custom": "Custom"
    },
    "deleteConfirm": "Are you sure you want to delete this mapping?"
  }
}
```

---

## Implementation Checklist

### New Files

| File | Description |
|------|-------------|
| `admin_portal/backend/api/model_mapping.py` | Backend API routes |
| `admin_portal/backend/schemas/model_mapping.py` | Pydantic request/response models |
| `admin_portal/frontend/src/pages/ModelMapping.tsx` | Main page component |
| `admin_portal/frontend/src/hooks/useModelMapping.ts` | React Query hooks |
| `admin_portal/frontend/src/types/modelMapping.ts` | TypeScript types |

### Modified Files

| File | Change |
|------|--------|
| `admin_portal/backend/main.py` | Register model_mapping router |
| `admin_portal/frontend/src/App.tsx` | Add /model-mapping route |
| `admin_portal/frontend/src/components/Layout/Sidebar.tsx` | Add navigation item |
| `admin_portal/frontend/src/services/api.ts` | Add modelMappingApi |
| `admin_portal/frontend/src/i18n/en.json` | Add English translations |
| `admin_portal/frontend/src/i18n/zh.json` | Add Chinese translations |

### Dependencies (Reuse)

- **Backend**: Reuse `app/db/dynamodb.py` `ModelMappingManager`
- **Frontend**: Reuse `SlideOver` component, table styles, button styles
