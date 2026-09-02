# Design — Admin portal model speed test

## 1. Boundaries

```
Browser (ModelMapping.tsx)
   │  POST /api/model-mapping/speed-test          {bedrock_model_id}
   │  GET  /api/model-mapping/speed-test/latest
   │  GET  /api/model-mapping/speed-test/history/{bedrock_model_id}?limit=10
   ▼
Admin backend (admin_portal/backend)
   ├─ api/model_mapping.py         thin routes → SpeedTestService
   ├─ services/speed_test.py       run_test(), SSE timing, key provisioning
   └─ app.db.dynamodb              SpeedTestManager (new) + ApiKeyManager (existing)
        │  httpx streaming, x-api-key = admin-speedtest key
        ▼
Proxy  {PROXY_BASE_URL}/v1/messages  (stream=true, thinking disabled)
        ▼
Bedrock (InvokeModel / Converse / Mantle — chosen by the proxy as usual)
```

The admin backend never calls Bedrock itself and never learns routing rules; it is a plain
client of the proxy. The only proxy change is the R7 defect fix in two converters.

## 2. Contracts

### 2.1 Speed-test record (DynamoDB item == API JSON)

| field | type | notes |
|---|---|---|
| `bedrock_model_id` | S (PK) | exactly what was sent as `model` |
| `tested_at` | N (SK) | epoch **milliseconds** at request send |
| `status` | S | `ok` \| `error` |
| `ttft_ms` | N \| null | first `content_block_delta` |
| `total_ms` | N \| null | `message_stop` or stream close |
| `output_tokens` | N \| null | `message_delta.usage.output_tokens` |
| `otps` | N \| null | `output_tokens / ((total_ms-ttft_ms)/1000)`, 2 dp |
| `has_reasoning` | BOOL | saw `thinking_delta` |
| `error` | S \| null | short message, ≤ 500 chars |
| `proxy_base_url` | S | for later interpretation |
| `expires_at` | N | epoch **seconds**, `tested_at/1000 + 90d` (table TTL attr) |

Pydantic: `SpeedTestRecord` (response), `SpeedTestRequest {bedrock_model_id: str}`,
`SpeedTestHistoryResponse {items: list[SpeedTestRecord], count}`,
`SpeedTestLatestResponse {items: dict[str, SpeedTestRecord]}` in
`admin_portal/backend/schemas/model_mapping.py`. Decimal → float conversion happens in the
manager (same approach as pricing/api_keys managers).

### 2.2 Proxy request emitted by the test

```json
POST {PROXY_BASE_URL}/v1/messages
headers: x-api-key, anthropic-version: 2023-06-01, content-type: application/json
{
  "model": "<bedrock_model_id>",
  "max_tokens": <SPEED_TEST_MAX_TOKENS>,
  "stream": true,
  "thinking": {"type": "disabled"},
  "messages": [{"role": "user",
    "content": "Write a plain paragraph of about 150 words describing how rivers form. No headings, no lists."}]
}
```

The prompt asks for prose so that the model reliably fills most of `max_tokens`
(a stable denominator). Text is a module constant `SPEED_TEST_PROMPT`.

### 2.3 SSE timing algorithm (`services/speed_test.py`)

```
t0 = perf_counter() just before client.send()
for each "event:/data:" pair from httpx aiter_lines():
    parse data JSON (skip non-JSON / ping)
    type == content_block_delta:
        if first_delta_at is None: first_delta_at = now
        if delta.type == thinking_delta: has_reasoning = True
    type == message_delta: output_tokens = usage.output_tokens (if present)
    type == message_stop: stop_at = now; break
    type == error: raise SpeedTestError(data.error.message)
stop_at = stop_at or now
ttft_ms  = (first_delta_at - t0)*1000   -> error "no content_block_delta received" if None
total_ms = (stop_at - t0)*1000
otps     = output_tokens / ((total_ms - ttft_ms)/1000)  if output_tokens>0 and total_ms>ttft_ms else None
```

Wrapped in `asyncio.timeout(SPEED_TEST_TIMEOUT_SECONDS)`; `httpx.HTTPStatusError`,
`httpx.TransportError`, `TimeoutError`, `SpeedTestError` → `status=error` record.
The parser is a pure function `parse_stream(lines, t0, clock)` so tests can feed a list of
lines and a fake clock without network.

### 2.4 Internal key provisioning (`services/speed_test.py`)

```
_cached_key: str | None
get_key():
    if _cached_key: return it
    rows = ApiKeyManager.list_api_keys_for_user("admin-speedtest")   # user_id-index GSI, app/db/dynamodb.py:645
    active = first row with is_active True
    key = active["api_key"] if active else create_api_key(user_id="admin-speedtest", name="admin-speedtest",
              rate_limit=10, tpm_limit=20000, monthly_budget=5.0, metadata={"purpose":"admin-speedtest"})
    cache and return
on proxy 401/403: _cached_key=None, retry once (re-provisions if the row was deleted)
```

Lookup reuses the existing `user_id-index` GSI (no schema change). Concurrency: an
`asyncio.Lock` around provisioning prevents two concurrent first tests from creating two keys.

### 2.5 Storage manager (`app/db/dynamodb.py`)

`SpeedTestManager(table)`:
- `put_result(record: dict)` — `put_item`.
- `get_history(bedrock_model_id, limit=10)` — `query(KeyConditionExpression=Key("bedrock_model_id").eq(...), ScanIndexForward=False, Limit=limit)`.
- `get_latest(bedrock_model_ids: Iterable[str])` — one `query(..., Limit=1)` per distinct
  ID, run via `asyncio.gather(to_thread(...))` in the service; mapping lists are tens of
  rows, acceptable. (A GSI is not worth it for this volume.)
- `_create_speed_tests_table()` with PK/SK above, `BillingMode=PAY_PER_REQUEST`, then
  `update_time_to_live(expires_at)`; ignore `ResourceInUseException` like the others.
- `DynamoDBClient.speed_tests_table_name`, `.speed_test_manager`, added to `create_tables()`.

### 2.6 Routes (`admin_portal/backend/api/model_mapping.py`)

Declared **before** the existing `GET /{anthropic_model_id:path}` catch-all so
`/speed-test/...` is not swallowed by it (same reason `/sync/status` is declared first).

| route | behaviour |
|---|---|
| `POST /speed-test` | 503 if `PROXY_BASE_URL` empty or provisioning fails; else run, persist, return record (200 even when `status=error`) |
| `GET /speed-test/latest` | collect distinct Bedrock IDs from the same merged list `list_model_mappings` builds (factor that merge into a helper `_merged_mappings()`), return latest map |
| `GET /speed-test/history/{bedrock_model_id:path}` | `limit` query 1–50 default 10 |

### 2.7 Frontend

- `types/modelMapping.ts`: `SpeedTestRecord`, `SpeedTestLatestResponse`, `SpeedTestHistoryResponse`.
- `services/api.ts` `modelMappingApi`: `runSpeedTest(bedrockModelId)`, `speedTestLatest()`,
  `speedTestHistory(bedrockModelId, limit=10)`.
- `hooks/useModelMapping.ts`: `useSpeedTestLatest()` (staleTime 60 s),
  `useSpeedTestHistory(bedrockModelId, enabled)` (lazy, like `useApiKeyDailyUsage`),
  `useRunSpeedTest()` mutation → on settle invalidate `['speedTestLatest']` and
  `['speedTestHistory', bedrockModelId]`. Running state is tracked per Bedrock ID in the page
  via `mutation.variables` set (a `Set<string>` in component state) so several rows can spin.
- `components/SpeedTestHoverChart.tsx`: copy the portal/positioning/timer logic from
  `UsageHoverChart.tsx` into a shared `HoverPopover` helper **only if** the extraction is
  mechanical; otherwise duplicate the ~40 lines and note it. Chart: recharts `ComposedChart`
  with `Bar` TTFT (left axis) + `Line` OTPS (right axis), x = run index oldest→newest,
  custom tooltip; failed runs excluded from series and listed as red text under the chart.
- `pages/ModelMapping.tsx`: add `<th>` Speed, `colSpan` 4→5, cell component
  `SpeedCell({bedrockModelId, latest, running, onRun})` wrapped in `SpeedTestHoverChart`.
- i18n keys `modelMapping.speed.*`: `column`, `test`, `testing`, `never`, `ttft`, `otps`,
  `tokens`, `reasoning`, `failed`, `hoverTitle`, `noData`, `misconfigured`.

### 2.8 Configuration

`app/core/config.py` (Settings):
```
proxy_base_url: str = "http://localhost:8000"      # PROXY_BASE_URL
dynamodb_speed_tests_table: str = "anthropic-proxy-speed-tests"
speed_test_max_tokens: int = 200
speed_test_timeout_seconds: int = 90
```
`env.example` gets the four entries under a "Admin portal speed test" block.

CDK: `dynamodb-stack.ts` new `speedTestsTable` (PK/SK, TTL `expires_at`, RETAIN, CfnOutput);
`ecs-stack.ts` props + destructure + `grantReadWriteData` for both roles +
`DYNAMODB_SPEED_TESTS_TABLE` in both env maps; admin env adds
`PROXY_BASE_URL: config.enableCloudFront ? \`https://${distribution.distributionDomainName}\` : \`http://${this.alb.loadBalancerDnsName}\``.
The distribution is created after the admin service today (`ecs-stack.ts:365-438` vs `:340`),
so either move `createAdminPortalService` after the CloudFront block or pass the URL via a
lazy token (`cdk.Lazy.string`). Prefer reordering; verify with `cdk synth` diff that nothing
else changes. `bin/` app wiring passes the new table through like `betaHeadersTable`.

## 3. R7 converter fix

```python
def _thinking_enabled(thinking) -> bool:
    return isinstance(thinking, dict) and thinking.get("type") == "enabled"
```
- `anthropic_to_openai.py:120`: `if _thinking_enabled(request.thinking):`
- `anthropic_to_bedrock.py:112`: `if _thinking_enabled(request.thinking) and settings.enable_extended_thinking:`
- InvokeModel path unchanged (forwards the dict; `disabled` is valid Anthropic API).
Behaviour for `None` and `{"type":"enabled",...}` is identical to today.

## 4. Trade-offs

- **HTTP hop through CloudFront** adds tens of ms to every measurement in deployed envs. Accepted:
  that is the client path, and `proxy_base_url` is stored with each record for context.
- **Usage pollution**: tests are billed to `admin-speedtest`; visible and filterable on the
  API Keys page. Excluding it from dashboard totals is out of scope.
- **Latest-per-model via N queries** instead of a GSI: simple, N ≈ number of mappings (<100).
- **Duplicated popover logic** vs. extracting a shared component: decide during implementation
  based on how mechanical the extraction is; correctness first.

## 5. Compatibility / rollout / rollback

- New table + new env vars only; all existing behaviour unchanged except R7 (a bug fix that
  only affects clients sending `thinking.type != "enabled"`, which previously and wrongly
  turned reasoning on).
- Deploy order: CDK (table + env) → admin image. Old admin images ignore the new env.
- Rollback: redeploy previous images; the table can stay (RETAIN) or be deleted manually.
- Local dev: run `uv run scripts/setup_tables.py` once to create the table.
