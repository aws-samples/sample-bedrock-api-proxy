# Implementation plan — Admin portal model speed test

Order is chosen so each step is independently testable and the proxy fix (R7) lands first
because every later manual test depends on it.

## Step 0 — Branch
- [ ] `git checkout -b feat/admin-model-speed-test` from `main` (do not touch the pre-existing
      dirty `.trellis/` and untracked files; they are not part of this task).

## Step 1 — R7 proxy converter fix (prerequisite)
- [ ] Add `_thinking_enabled()` helper (or inline check) in
      `app/converters/anthropic_to_openai.py` (~line 120) and
      `app/converters/anthropic_to_bedrock.py` (~line 112).
- [ ] Tests in `tests/unit/test_converters.py` (or a new `test_thinking_disabled.py`):
      disabled → no `reasoning_effort` (OpenAI-compat); disabled → no `reasoningConfig` for a
      Nova 2 model and no `reasoning_effort` for a Kimi model (Converse); `None` and `enabled`
      unchanged.
- Validate: `uv run pytest tests/unit -k "thinking or reasoning or converter" -q`.
- Rollback point: this commit is independently revertible.

## Step 2 — Config + storage
- [ ] `app/core/config.py`: `proxy_base_url`, `dynamodb_speed_tests_table`,
      `speed_test_max_tokens`, `speed_test_timeout_seconds`.
- [ ] `app/db/dynamodb.py`: `speed_tests_table_name`, `_create_speed_tests_table()` (PK/SK +
      TTL on `expires_at`), `SpeedTestManager` (`put_result`, `get_history`, `get_latest_one`),
      `DynamoDBClient.speed_test_manager`, add to `create_tables()`.
- [ ] `scripts/setup_tables.py`: print the new table.
- [ ] `env.example`: new block with the four variables.
- [ ] Tests (moto): `tests/unit/test_speed_test_manager.py` — put + history ordering/limit,
      latest-one, TTL attribute present.
- Validate: `uv run pytest tests/unit/test_speed_test_manager.py -q`.

## Step 3 — Admin backend service + routes
- [ ] `admin_portal/backend/services/speed_test.py`:
      `SPEED_TEST_PROMPT`, `SpeedTestError`, pure `parse_stream(lines, t0, clock)`,
      `async run_speed_test(bedrock_model_id) -> dict` (httpx `AsyncClient.stream`, timeout,
      error → record), `get_internal_api_key()` with lock + cache + 401 invalidation,
      `get_latest_for(ids)` using `asyncio.gather(asyncio.to_thread(...))`.
- [ ] `admin_portal/backend/schemas/model_mapping.py`: `SpeedTestRequest`, `SpeedTestRecord`,
      `SpeedTestHistoryResponse`, `SpeedTestLatestResponse`.
- [ ] `admin_portal/backend/api/model_mapping.py`: factor the default+DynamoDB merge from
      `list_model_mappings` into `_merged_mappings()`; add the three routes **above**
      `GET /{anthropic_model_id:path}`.
- [ ] Tests: `tests/unit/test_admin_speed_test.py` —
      `parse_stream` cases (text only; thinking then text → `has_reasoning`, TTFT at thinking;
      `error` event; no delta; missing usage → `otps=None`);
      `run_speed_test` with `httpx.MockTransport` streaming a canned SSE body (ok, 400, 401→
      re-provision once); key provisioning idempotency (moto keys table with `user_id-index`);
      routes: POST persists and returns record, 503 when `proxy_base_url=""`, history `limit`
      clamp, latest map.
- Validate: `uv run pytest tests/unit/test_admin_speed_test.py -q`.

## Step 4 — Frontend
- [ ] `types/modelMapping.ts`, `services/api.ts`, `hooks/useModelMapping.ts` additions.
- [ ] `components/SpeedTestHoverChart.tsx` (decide: extract shared `HoverPopover` from
      `UsageHoverChart.tsx` if mechanical, else duplicate and comment).
- [ ] `pages/ModelMapping.tsx`: Speed column, `SpeedCell`, per-row running set, `colSpan` 5.
- [ ] `i18n/en.json` + `i18n/zh.json`: `modelMapping.speed.*`.
- Validate: `cd admin_portal/frontend && npm run lint && npm run build`.

## Step 5 — CDK
- [ ] `cdk/lib/dynamodb-stack.ts`: `speedTestsTable` (PK `bedrock_model_id` S, SK `tested_at` N,
      `timeToLiveAttribute: 'expires_at'`, RETAIN, tags, CfnOutput).
- [ ] `cdk/lib/ecs-stack.ts`: props + destructure, `grantReadWriteData` (proxy + admin),
      `DYNAMODB_SPEED_TESTS_TABLE` in both env maps, `PROXY_BASE_URL` in admin env. Move
      `createAdminPortalService(...)` after the CloudFront block (or use `cdk.Lazy`) so the
      distribution domain is available; confirm with `cdk diff` that only the intended
      resources change.
- [ ] `cdk/bin/app.ts`: pass `speedTestsTable`.
- Validate: `cd cdk && npm run build && npx cdk synth -c env=dev > /dev/null`.

## Step 6 — Docs
- [ ] `CLAUDE.md`: DynamoDB Tables row, Features bullet ("Model Speed Test"), Env Vars line.
- [ ] `docs/architecture/features.md`: short section (metric definitions, thinking disabled,
      internal key, retention).

## Step 7 — Full validation (2.2 last iteration)
```bash
uv run pytest -q
black app tests admin_portal/backend && ruff check app tests admin_portal/backend && mypy app
cd admin_portal/frontend && npm run lint && npm run build
cd cdk && npm run build && npx cdk synth -c env=dev > /dev/null
```
Manual (local): start proxy (`uv run uvicorn app.main:app`), `uv run scripts/setup_tables.py`,
start admin (`uv run uvicorn admin_portal.backend.main:app --port 8005`), open Model Mapping,
run Test on a Claude row (AC1), on a bogus mapping (AC2), run 12 times and hover (AC3),
check API Keys page shows `admin-speedtest` once (AC5).

## Risky files / rollback points
- `cdk/lib/ecs-stack.ts` reordering of admin-service vs CloudFront creation — verify with
  `cdk diff`; if resource logical IDs change, prefer `cdk.Lazy.string` instead of moving code.
- `app/converters/*` R7 change affects real client traffic that sends `thinking.type=disabled`;
  behaviour becomes correct, but keep the commit separate for easy revert.
- `admin_portal/backend/api/model_mapping.py` route ordering vs `:path` catch-all — covered
  by a route test hitting `/speed-test/latest`.

## Before `task.py start`
- [ ] User has approved the final planning summary in a message after it was presented.
- [ ] `implement.jsonl` / `check.jsonl` contain real entries (done 2026-09-02).
