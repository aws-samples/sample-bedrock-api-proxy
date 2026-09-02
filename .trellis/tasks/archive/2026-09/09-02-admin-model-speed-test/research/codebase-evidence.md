# Research: codebase evidence for the speed-test feature (2026-09-02)

All anchors verified against the working tree on 2026-09-02.

## Proxy behaviour relied upon
- Model ID resolution falls through to pass-through: `app/converters/anthropic_to_bedrock.py:338-359`.
  A Bedrock model ID sent as `model` reaches Bedrock unchanged on every path.
- Streaming SSE events emitted to clients: `message_start`, `content_block_start`,
  `content_block_delta` (`thinking_delta` at `bedrock_to_anthropic.py:373`, `text_delta` at `:385`),
  `message_delta` with `usage.output_tokens` (`:509`), `message_stop` (`:429`).
- `usage.output_tokens` is total output incl. thinking; OpenAI-compat maps `completion_tokens`
  (`app/converters/openai_to_anthropic.py:104`).
- Thinking config handling per path:
  - InvokeModel: `app/services/bedrock_service.py:540-541` forwards `request.thinking` as-is.
  - OpenAI-compat: `app/converters/anthropic_to_openai.py:120` `if request.thinking:` → `reasoning_effort="high"`.
  - Converse: `app/converters/anthropic_to_bedrock.py:112-146` (Nova 2 `reasoningConfig`, Kimi `reasoning_effort`).
  - `_convert_thinking_to_effort` (`anthropic_to_openai.py:363-380`) already checks `type == "enabled"` but is dead code.
- Reasoning-effort values accepted by passthrough clamp include `none`
  (`app/api/openai_passthrough/chat_responses_adapter.py:~456`) — Mantle support for `none` on chat/completions unverified.

## Admin backend patterns
- Routes are thin; `model_mapping.py` already imports `httpx`, `DynamoDBClient`, `settings`
  (`admin_portal/backend/api/model_mapping.py:10-14`); `get_manager()` at `:29`.
- Path-catch-all routes `GET/PUT/DELETE /{anthropic_model_id:path}` at `:122/:205/:252`;
  fixed sub-paths (`/sync/status`, `/sync`) are declared before them (`:92-99`).
- Router prefix `/api/model-mapping` (`admin_portal/backend/main.py:109`).
- Schemas: pydantic `BaseModel` + `Field` descriptions, `Config.extra="allow"` on responses
  (`admin_portal/backend/schemas/model_mapping.py`).
- Env loading: root `.env` then `admin_portal/backend/.env` override (`main.py:19-27`).
- Tests: `tests/unit/test_admin_model_mapping_override.py` — `moto` `mock_aws`, create table with
  `settings.dynamodb_*_table` name, import route module inside fixture, `await` route functions.

## API key manager
- `ApiKeyManager.create_api_key(...)` `app/db/dynamodb.py:434-497` (fields incl. `is_active`, `user_id`, `metadata`).
- `list_api_keys_for_user(user_id)` `:645` uses GSI `user_id-index` (`:140`).

## DynamoDB table wiring checklist (mirror BetaHeaders)
- `app/core/config.py:102-123` Settings fields.
- `app/db/dynamodb.py:98-124` names + `create_tables()`; `_create_beta_headers_table` `:381-400`; manager class `:2483`.
- `scripts/setup_tables.py` prints table names.
- `cdk/lib/dynamodb-stack.ts:273` public field, `:495` table, TTL example `:531` (`timeToLiveAttribute: 'expires_at'`), tags `:546`, output `:601`.
- `cdk/lib/ecs-stack.ts` props `:20-35`, destructure `:54`, grant `:147`, proxy env `:250`, pass to admin `:356`, admin param type `:918-930`, admin env `:968`, admin grant `:1089`.
- `cdk/bin/app.ts:73` passes tables into ECSStack.
- CloudFront optional: `cdk/config/config.ts:117` `enableCloudFront` (default false `:226`); distribution created at `ecs-stack.ts:365-438`, **after** admin service creation at `:340`.

## Frontend patterns
- `UsageHoverChart.tsx` (portal popover, OPEN_DELAY, viewport clamping, lazy query `enabled=open`).
- `hooks/useApiKeys.ts:37` lazy query shape; `hooks/useModelMapping.ts` existing query keys.
- `services/api.ts:319-362` `modelMappingApi` using `apiFetch`.
- `pages/ModelMapping.tsx:254-300` table header/body, `colSpan={4}` at `:272,:278`.
- i18n: `i18n/en.json:242`, `i18n/zh.json:242` `modelMapping` block; app uses `react-i18next`.
- Deps: recharts ^3.9, @tanstack/react-query ^5.60; `npm run build` = `tsc && vite build`.
