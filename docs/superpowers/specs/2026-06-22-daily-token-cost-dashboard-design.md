# Daily Token & Cost Dashboard — Design

**Date:** 2026-06-22
**Status:** Approved (design)
**Branch:** `feat/daily-usage-dashboard` (off `main`)

## Goal

Add a panel to the admin portal dashboard showing **per-day token usage and
cost, broken down by model**, for a recent window (7 / 14 / 30 days). One chart
with a token ⇄ cost toggle.

## Requirements (confirmed)

- **Time range:** recent 7–30 days (selectable: 7 / 14 / 30).
- **Breakdown:** per model, per day (stacked).
- **Metrics:** tokens and cost, shown in one chart with a toggle.
- **Chart lib:** `recharts` (added to the frontend; none exists today).
- **Scope:** global (all API keys aggregated). Per-key breakdown is out of scope.

## Approach (chosen): real-time aggregation of raw usage records

No new table. On request, scan the existing `usage` table by timestamp range and
bucket records by `(date, model)`, reusing the existing per-record cost logic.

### Why this approach

- The raw `usage` table already stores, per request: `api_key` (PK),
  `timestamp` (SK, ms-string), `model`, `input_tokens`, `output_tokens`,
  `cached_tokens`, `cache_write_input_tokens`, `cache_ttl`, `metadata`.
- `UsageManager.aggregate_usage_for_key()` (app/db/dynamodb.py:1564) already
  computes per-record cost (pricing cache, 5m/1h cache-write pricing, OpenAI
  cache-inclusive normalization). We reuse that math, changing only the
  reduction: sum → bucket by `(date, model)`.
- For a personal/small-team volume over ≤30 days, a full scan per request is
  acceptable, especially with a 5-minute client-side cache.

### Rejected alternatives

- **New daily-aggregation table** (write-path accumulation): faster queries and
  long retention, but requires touching the write hot path, a backfill, a new
  table, and CDK changes — overkill for a 7–30 day window.
- **Reuse existing `/stats`:** it only stores cumulative totals (no per-day
  dimension), so it cannot produce a daily series.

## Data flow

```
GET /api/dashboard/daily-usage?days=30
  ↓
1. since_ts = now - days (UTC)
2. Build pricing_cache + model_mapping_cache (same as aggregate_all_usage)
3. For each api_key, query usage table for timestamp > since_ts:
     for each record:
       day   = UTC date of record_timestamp  ("YYYY-MM-DD")
       cost  = existing per-record cost formula
       buckets[day][model] += {input_tokens, output_tokens, tokens, cost, requests}
4. Emit days sorted ascending, each with per-model rows + day totals.
```

Dates are bucketed in **UTC**, consistent with `aggregate_all_usage`.

### API response shape

```jsonc
{
  "days": 30,
  "start_date": "2026-05-23",
  "end_date": "2026-06-22",
  "daily": [
    {
      "date": "2026-06-22",
      "total_tokens": 125000,
      "total_cost": 1.83,
      "models": [
        {"model": "claude-fable-5", "input_tokens": 80000, "output_tokens": 20000, "tokens": 100000, "cost": 1.50},
        {"model": "claude-opus-4-8", "input_tokens": 20000, "output_tokens": 5000, "tokens": 25000, "cost": 0.33}
      ]
    }
  ]
}
```

Days with no usage are still emitted (zero-filled) so the chart shows a
continuous axis.

## Components & boundaries

### Backend

1. **`UsageManager.aggregate_daily_usage(since_timestamp, pricing_cache, model_mapping_cache)`**
   (new, in `app/db/dynamodb.py`). Iterates all API keys, buckets by
   `(utc_date, model)`. Returns `{date: {model: {tokens, cost, ...}}}`.
   Pure data; no HTTP concerns. Reuses the per-record cost block from
   `aggregate_usage_for_key` (extract a shared helper to avoid duplicating the
   pricing math).
2. **`GET /api/dashboard/daily-usage`** (new route in
   `admin_portal/backend/api/dashboard.py`). Parses `days` (default 30, clamp
   1–90), builds caches, calls the manager method, zero-fills the date range,
   returns the response model.
3. **Pydantic models** for the response (in the dashboard schemas module
   alongside `DashboardStats`).

### Frontend (`admin_portal/frontend/`)

4. **`recharts`** added to `package.json` (npm).
5. **`useDailyUsage(days)`** hook — `useQuery`, key `['daily-usage', days]`,
   `staleTime: 5 min`.
6. **`DailyUsageChart` component** on the dashboard:
   - Range dropdown (7 / 14 / 30) + token⇄cost toggle (existing UI style).
   - `recharts` stacked `<BarChart>`: X = date, one `<Bar stackId>` per model,
     tooltip = per-model detail + day total, legend = models.
   - Token mode Y axis via existing `formatTokens`; cost mode via `$`.
7. **i18n** keys added to `en.json` / `zh.json`.

## TTL change (required side effect)

The chart can only show as far back as raw `usage` records are retained.
`USAGE_TTL_DAYS` default is **7** today.

- Change the default to **30** in `app/core/config.py`.
- Set `USAGE_TTL_DAYS=30` in the prod deploy env.
- **Note:** the change only affects *new* records; already-expired data cannot
  be recovered, so the chart will "fill in" to 30 days over the first few weeks.
  This is expected, not a bug — document it in the panel/release notes.

## Error handling

- Empty / no usage in range → return zero-filled `daily` array; chart renders an
  empty-but-valid axis (no error state).
- Missing pricing for a model → tokens still counted; cost contribution 0 for
  that model (same as existing aggregation behavior).
- Backend exception during scan → 500 with the standard error envelope; the hook
  surfaces an error card (same pattern as `useDashboardStats`).

## Testing

- **Backend unit** (`tests/unit/`): `aggregate_daily_usage` bucketing — mock
  usage records spanning multiple days/models, assert per-day/per-model token
  sums, cost (incl. 1h cache-write and OpenAI cache-inclusive cases), UTC day
  boundary, and zero-fill.
- **Frontend:** component renders with mock data; range dropdown and token⇄cost
  toggle update the chart (follow existing frontend test conventions if present).

## Out of scope

- Per-API-key daily breakdown.
- Retention beyond 30 days / long-term historical trends.
- A new persistent daily-aggregation table.
