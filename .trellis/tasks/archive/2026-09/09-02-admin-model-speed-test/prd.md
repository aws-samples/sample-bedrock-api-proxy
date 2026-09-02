# Admin portal model speed test (TTFT/OTPS) via proxy endpoint

## Goal

Let an admin measure and track the real end-to-end latency of every mapped Bedrock model
from the admin portal Model Mapping page: click a button per row to run one streaming
test through the proxy, persist the result, and show the last 10 results on hover.

User value: a quick, historical view of TTFT (time to first token) and OTPS (output tokens
per second) per Bedrock model ID, measured through the same path real clients use, with
thinking explicitly disabled so numbers are comparable across models and over time.

## Background (repository evidence)

- Admin portal is a separate FastAPI app (`admin_portal/backend/main.py`, port 8005) and a
  separate Fargate service in CDK (`cdk/lib/ecs-stack.ts:904` `createAdminPortalService`).
  Its env (`cdk/lib/ecs-stack.ts:951-976`) has DynamoDB table names + Cognito only: **no
  proxy URL and no API key**.
- Behind CloudFront the ALB rejects requests without `X-CloudFront-Secret`
  (`cdk/lib/ecs-stack.ts:440-491`), so the admin container cannot call the ALB directly;
  it must use the CloudFront HTTPS URL (`ProxyURL` output at `cdk/lib/ecs-stack.ts:506`).
  CloudFront is optional (`enableCloudFront`, `cdk/config/config.ts:117`, default `false`);
  without it the ALB `http://` URL is reachable directly.
- Proxy `/v1/messages` resolves `model` as: DynamoDB mapping → default mapping → **pass-through
  as-is** (`app/converters/anthropic_to_bedrock.py:338-359`). Sending a Bedrock model ID
  directly therefore works for InvokeModel, Converse and OpenAI-compat paths alike.
- Proxy streaming emits Anthropic SSE events: `message_start`, `content_block_delta` with
  `thinking_delta` / `text_delta` (`app/converters/bedrock_to_anthropic.py:365-385`),
  `message_delta` whose `usage.output_tokens` is filled from Bedrock metadata
  (`app/converters/bedrock_to_anthropic.py:509`), then `message_stop`.
- `usage.output_tokens` includes thinking tokens (Anthropic) / reasoning tokens
  (`completion_tokens`, `app/converters/openai_to_anthropic.py:104`). No separate thinking
  token count is exposed.
- **Thinking `{"type": "disabled"}` handling today** (defect this task must fix, see R7):
  - InvokeModel (Claude): `thinking` dict is forwarded as-is
    (`app/services/bedrock_service.py:540-541`) → correct, disables thinking.
  - OpenAI-compat: `if request.thinking:` (`app/converters/anthropic_to_openai.py:120`) is
    truthy for `{"type":"disabled"}` → sets `reasoning_effort="high"` (**enables** reasoning).
  - Converse: `if request.thinking and settings.enable_extended_thinking:`
    (`app/converters/anthropic_to_bedrock.py:112`) → for Nova 2 sets
    `reasoningConfig.type=enabled`, for Kimi sets `reasoning_effort="high"` (**enables**).
- `ApiKeyManager.create_api_key(user_id, name, rate_limit, monthly_budget, tpm_limit, …)`
  (`app/db/dynamodb.py:434`) returns a `sk-…` key; keys table PK is `api_key`.
- Existing hover widget: `admin_portal/frontend/src/components/UsageHoverChart.tsx` — portal
  popover positioned from the anchor rect, lazy react-query fetch enabled on open
  (`useApiKeyDailyUsage(apiKey, DAYS, open)` in `hooks/useApiKeys.ts:37`), recharts inside.
- Model Mapping page table: `admin_portal/frontend/src/pages/ModelMapping.tsx:254-265`
  (columns: Anthropic ID, Bedrock ID, Source, actions; `colSpan={4}` placeholders at `:272,:278`).
  API client `modelMappingApi` in `services/api.ts:319` (uses `apiFetch`); hooks in
  `hooks/useModelMapping.ts`; types in `types/modelMapping.ts`; i18n keys under
  `modelMapping` in `i18n/en.json:242` and `i18n/zh.json:242`.
- New DynamoDB tables follow the pattern: `Settings.dynamodb_*_table` (`app/core/config.py:102-123`),
  `DynamoDBClient.*_table_name` + `_create_*_table()` (`app/db/dynamodb.py:98-124`, example
  `:381-400`), CDK table in `cdk/lib/dynamodb-stack.ts` (e.g. `BetaHeadersTable` at `:495`,
  TTL example `timeToLiveAttribute: 'expires_at'` at `:531`) wired into both task roles and
  env in `ecs-stack.ts` (`:20-35` props, `:147`, `:250`, `:918-930`, `:968`, `:1089`),
  printed by `scripts/setup_tables.py`.
- Admin backend unit tests use `moto` `mock_aws` and call route functions directly
  (`tests/unit/test_admin_model_mapping_override.py`).

## Decisions (user-confirmed 2026-09-02)

1. **Test path = proxy HTTP endpoint** (`POST {PROXY_BASE_URL}/v1/messages`, `stream: true`).
   Covers InvokeModel / Converse / Mantle routing, beta headers and multi-provider without
   duplicating logic in the admin backend; the number measured is what clients see.
2. **Auth = auto-provisioned internal key.** Admin backend lazily creates (or reuses) one
   API key named `admin-speedtest` (user_id `admin-speedtest`, small monthly budget, low
   rate/TPM limits) in the keys table. No `MASTER_API_KEY` in the admin container. Its usage
   is visible under that key in the dashboard.
3. **Proxy address = new `PROXY_BASE_URL` setting.** Local default `http://localhost:8000`;
   CDK sets the CloudFront HTTPS URL (or the ALB `http://` URL when CloudFront is off).
4. **Reasoning metric semantics.** TTFT = first `content_block_delta` of any type; OTPS =
   total `output_tokens` (incl. any reasoning) / (first delta → `message_stop`); each record
   carries `has_reasoning`. Hidden-reasoning models (e.g. gpt-5.6 on Mantle) will show large
   TTFT with `has_reasoning=false`; recorded as-is because that is the real client experience.
5. **Thinking is explicitly disabled** in every test request (`"thinking": {"type": "disabled"}`),
   which requires fixing the proxy so a disabled thinking config never enables reasoning (R7).

## Requirements

### R1 — Run a speed test (admin backend)
- `POST /api/model-mapping/speed-test` with body `{ "bedrock_model_id": "…" }`.
- Sends one streaming Anthropic-format request to `{PROXY_BASE_URL}/v1/messages` with
  `model = bedrock_model_id` (pass-through), a fixed short prompt, `max_tokens` =
  `SPEED_TEST_MAX_TOKENS` (default 200), `"thinking": {"type": "disabled"}`,
  `x-api-key` = internal key (R4). Hard timeout `SPEED_TEST_TIMEOUT_SECONDS` (default 90).
- Measures from the SSE stream:
  - `ttft_ms`: request send → first `content_block_delta` (`thinking_delta` or `text_delta`).
  - `total_ms`: request send → `message_stop` (or stream end).
  - `output_tokens`: `usage.output_tokens` from `message_delta`.
  - `otps`: `output_tokens / ((total_ms − ttft_ms) / 1000)`; `null` if denominator ≤ 0 or
    `output_tokens` is 0.
  - `has_reasoning`: true if any `thinking_delta` was seen.
- Persists one record per run: `bedrock_model_id` (PK), `tested_at` epoch ms (SK), `ttft_ms`,
  `total_ms`, `output_tokens`, `otps`, `has_reasoning`, `status` (`ok` | `error`),
  `error` (string, on failure), `proxy_base_url`, `expires_at` (epoch s, +90 days).
- Failures (non-2xx, timeout, malformed stream, no `content_block_delta`) are persisted as
  `status=error` with the message and returned as HTTP 200 with `status: "error"` so the UI
  shows them in history. Misconfiguration (empty `PROXY_BASE_URL`, key provisioning failed)
  returns 503 and stores nothing.
- Response body = the persisted record.

### R2 — Read history (admin backend)
- `GET /api/model-mapping/speed-test/history/{bedrock_model_id:path}?limit=10` returns the
  latest N records (default 10, max 50), newest first.
- `GET /api/model-mapping/speed-test/latest` returns `{ "items": { "<bedrock_model_id>": <record> } }`
  with the most recent record for every Bedrock model ID present in the current mapping list
  (defaults + DynamoDB), so the table renders summary cells with one request.

### R3 — Storage
- New DynamoDB table `anthropic-proxy-speed-tests`: PK `bedrock_model_id` (S),
  SK `tested_at` (N), on-demand billing, TTL attribute `expires_at`.
- Configurable via `DYNAMODB_SPEED_TESTS_TABLE`; created by `DynamoDBClient.create_tables()`,
  listed in `scripts/setup_tables.py`, defined in CDK with `RETAIN` removal policy and granted
  read/write to both proxy and admin task roles (same wiring as the other tables).

### R4 — Internal API key provisioning
- On first speed-test request the admin backend looks up an active key with
  `user_id == "admin-speedtest"`; if none exists it calls `create_api_key(...)` with
  `name="admin-speedtest"`, `rate_limit=10`, `tpm_limit=20000`, `monthly_budget=5.0`,
  `metadata={"purpose": "admin-speedtest"}`. The key value is cached in-process; a 401 from
  the proxy invalidates the cache and re-provisions once.
- The key is an ordinary row: it shows up on the API Keys page and can be edited or deleted
  by an admin (deleting it just triggers re-provisioning next time).

### R5 — Model Mapping page UI
- New column **Speed** between "Source" and the actions column, one cell per row:
  - Shows the latest `ttft_ms` and `otps` for that row's Bedrock model ID (from R2 latest),
    or a dash when never tested; shows an error icon with the message as tooltip when the
    latest run failed.
  - A "Test" (⚡) button in the cell runs R1 for that Bedrock model ID; while running the
    button is disabled and shows a spinner; on completion the cell and hover history refresh
    (react-query invalidation of the `speedTests` keys). Multiple rows may run concurrently.
  - Hovering the cell opens a popover (same portal/positioning/lazy-fetch pattern as
    `UsageHoverChart`) with the last 10 runs: a dual-axis recharts chart of TTFT (ms, left)
    and OTPS (tok/s, right) over run order; point tooltip shows time, TTFT, OTPS, output
    tokens and a "reasoning" marker; failed runs render as gaps with the error listed below.
- Rows sharing the same Bedrock model ID share history.
- All new strings are added to both `en.json` and `zh.json`.

### R6 — Configuration & docs
- `PROXY_BASE_URL` (default `http://localhost:8000`), `DYNAMODB_SPEED_TESTS_TABLE`,
  `SPEED_TEST_MAX_TOKENS` (200), `SPEED_TEST_TIMEOUT_SECONDS` (90) added to
  `app/core/config.py`, `env.example`, CDK admin env, and CLAUDE.md (DynamoDB Tables table,
  Features list, Env Vars).

### R7 — Proxy: disabled thinking must not enable reasoning (defect fix, prerequisite for D5)
- `anthropic_to_openai.py:120` and `anthropic_to_bedrock.py:112` must treat
  `thinking.get("type") != "enabled"` as "no thinking": no `reasoning_effort`, no Nova 2
  `reasoningConfig`, no Kimi `reasoning_effort`. Behaviour for `{"type":"enabled"}` and for
  `thinking=None` is unchanged. Unit tests cover disabled on all three branches.

## Acceptance Criteria

- [ ] AC1 With the proxy running locally and `PROXY_BASE_URL=http://localhost:8000`, clicking
      Test on a Claude mapping row stores a record with `status=ok`, `ttft_ms > 0`,
      `output_tokens > 0`, `otps > 0`, `has_reasoning=false`, and the cell updates without
      page reload. (R1, R5)
- [ ] AC2 Clicking Test on a row whose Bedrock ID is invalid stores a `status=error` record
      with the proxy's error message; the cell shows the error state and history shows it. (R1, R5)
- [ ] AC3 Hovering the Speed cell shows the last 10 runs (newest included); with 12 runs
      stored, exactly 10 are displayed. (R2, R5)
- [ ] AC4 Two Anthropic IDs mapped to the same Bedrock ID show identical Speed cells and
      history. (R2, R5)
- [ ] AC5 First test on a fresh deployment creates exactly one `admin-speedtest` key; a second
      test reuses it (keys table row count unchanged). Deleting the key and testing again
      recreates it. (R4)
- [ ] AC6 Unit test: a mocked SSE stream containing `thinking_delta` before `text_delta`
      yields `has_reasoning=true`, `ttft_ms` measured to the thinking delta, and `otps`
      computed from total `output_tokens`. (R1, D4)
- [ ] AC7 Unit tests: the request body sent to the proxy contains
      `"thinking": {"type": "disabled"}`; with that body, `AnthropicToOpenAIConverter`
      emits no `reasoning_effort`, and `AnthropicToBedrockConverter` emits no
      `reasoningConfig` (Nova 2) / `reasoning_effort` (Kimi). Existing enabled-thinking
      tests still pass. (R7)
- [ ] AC8 Unit tests (moto) cover record persistence + `limit`, latest-per-model query, key
      provisioning idempotency, SSE parsing (ok / error / timeout / no delta). `uv run pytest`,
      `black`, `ruff`, `mypy app` pass; `npm run build` in `admin_portal/frontend` passes. (R1–R4)
- [ ] AC9 `cdk synth` succeeds with the new table, IAM grants, and `PROXY_BASE_URL` env. (R3, R6)

## Out of scope

- "Test all models" batch button, scheduled/background tests, alerting.
- Configurable prompt / max_tokens / thinking-on per test from the UI (env-level only).
- Comparing thinking-on vs thinking-off; per-provider (multi-provider key pool) breakdown.
- Testing via `/openai/v1/*` passthrough (models that only work on the Responses API, e.g.
  `openai.gpt-5.5`, will record an error through `/v1/messages`; acceptable for MVP).
- Mapping `thinking.disabled` to OpenAI `reasoning_effort: "none"` for reasoning-native models
  (Mantle support unverified; deferred — disabled simply omits `reasoning_effort`).
- Excluding the internal key's usage from dashboard totals.
