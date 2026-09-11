# Quality Guidelines

> Code quality standards for backend development (proxy `app/` and `admin_portal/backend/`).

---

## Overview

- Tooling: `black`, `ruff`, `mypy app`, `uv run pytest`; frontend `npm run build` (tsc + vite);
  CDK `npm run build && npx cdk synth -c environment=dev`.
- **Baseline is not clean** (as of 2026-09-02): repo-wide `black` would reformat ~130 files,
  `ruff` ~1.5k findings, `mypy app` ~290 errors; `tests/integration/test_openai_passthrough`
  has 17 failing tests at `main`; `admin_portal/frontend` `npm run lint` fails because ESLint 9
  has no flat config. **Do not "fix" the baseline as a side effect of a feature.**
- Gate for a task therefore is: *no new findings on files you touched*, all unit tests pass,
  integration failures are exactly the known baseline set, frontend build and CDK synth pass.

---

## Forbidden Patterns

### Don't: truthiness checks on `thinking` / typed config dicts
**Problem**
```python
if request.thinking:                       # {"type": "disabled"} is truthy → enables reasoning
    result["reasoning_effort"] = "high"
```
**Why it's bad**: Anthropic clients legitimately send `{"type": "disabled"}`; this turned
reasoning ON on the OpenAI-compat and Converse paths (fixed in task 09-02).
**Instead**
```python
from app.converters.thinking import is_thinking_enabled
if is_thinking_enabled(request.thinking):  # True only for {"type": "enabled", ...}
```
Apply the same rule to any `{type: ...}` discriminated dict: branch on the discriminator.

### Don't: run repo-wide formatters in a feature branch
`black app tests` / `ruff --fix` over the tree rewrites dozens of unrelated files. Run them on
the files you changed: `black --check <files> && ruff check <files>`.

### Don't: declare fixed sub-routes after a `{param:path}` catch-all
In `admin_portal/backend/api/*.py`, routes like `GET /{anthropic_model_id:path}` swallow
`/speed-test/latest`. Declare fixed paths (`/sync`, `/sync/status`, `/speed-test/...`) **above**
the catch-all and add a router-order test
(`tests/unit/test_admin_speed_test.py::test_speed_test_routes_are_declared_before_catch_all`).

---

## Required Patterns

- OpenAI-compat Chat Completions requests (`AnthropicToOpenAIConverter`) send
  `max_completion_tokens`, never `max_tokens`: OpenAI deprecated the latter and gpt-5.6 on
  Bedrock Mantle rejects it with 400 `unsupported_parameter` (verified 2026-09-02; the new key
  is accepted by gpt-5.4, grok-4.3, kimi-k2.5, glm-5, minimax-m2.5, gpt-oss). Responses API
  keeps `max_output_tokens`.
- Admin backend calls the proxy over HTTP (`settings.proxy_base_url`) with a provisioned API
  key; it never calls Bedrock directly and never re-implements routing.
- Long-running external calls in async routes: `httpx.AsyncClient` + `asyncio.timeout(...)`;
  sync boto3 work that fans out: `asyncio.gather(asyncio.to_thread(...))`.
- Network-touching services expose a pure parsing core (e.g. `parse_stream(lines, t0, clock)`)
  so tests inject lines and a fake clock; transports are injected via a monkeypatchable factory
  (`speed_test._default_transport()`) and tested with `httpx.MockTransport`.
- Failed operations that the UI must show as history are persisted with `status="error"` and
  returned as HTTP 200; only misconfiguration returns 5xx.

---

## Testing Requirements

- New DynamoDB code: `moto` `mock_aws` with the real `_create_*_table()`; GSIs the code queries
  must exist in the test table (copy the schema from `app/db/dynamodb.py`).
- Admin routes: call the route function directly **and** at least one `TestClient` request
  through `admin_portal.backend.main.app` when route ordering matters.
- Converter behaviour changes: cover `None`, `{"type":"enabled"}`, and `{"type":"disabled"}`.

---

## Scenario: request-scoped actual-model authorization

### 1. Scope / Trigger
- Anthropic messages/count/models enforcement uses `app/services/model_access.py`.
  OpenAI passthrough/stateful authorization remains a separate task slice.

### 2. Signatures
- `BedrockService.prepare_model(model, policy) -> PreparedModel(target, api)`;
  `prepare_count_model(model, policy)` respects CountTokens' distinct dispatch.
- `ModelAccessService(service, policy, provider_id=None)` is created per admitted
  request. `.prepare(name)` memoizes a checked target; `.bind(name, prepared)`
  checks then pins saved/routed targets. Pass this facade as tool `bedrock_service`.
- Outbound Bedrock methods accept `access_policy=UNRESTRICTED_POLICY` and
  `prepared_model=None`. OpenAICompat methods accept `access_policy` explicitly.
  Executor workers receive these arguments or capture them in a per-call closure.
- Routing/failover accept `candidate_allowed(provider, model)` before selecting or
  acquiring keys. `BedrockProvider.invoke[_stream](..., model_access=facade)` is
  the restricted-only repair for its historically ignored `model_id` argument.

### 3. Contracts
- Never put request policy or pinned targets on shared services/providers. Never
  rely on ContextVar alone across `run_in_executor`.
- Native/Converse/Runtime Responses send resolved IDs. Old OpenAI compatibility
  sends the original name; do not authorize its mapped ID instead.
- Restricted Converse conversion uses a fresh converter plus `resolved_model_id`
  to avoid both re-resolution and its shared mutable `_resolved_model_id` cache.
- PTC state stores `original_target` and `original_api`, not an admitted policy;
  the next admitted policy checks the saved target before sandbox resume.
- Restricted smart routing does NOT call RouteLLM `Controller.completion`: that
  is upstream inference, not a pure classifier. It locally chooses permitted
  strong/weak fallback. Unrestricted behavior is unchanged.
- Pricing row `provider` is a vendor label (`Anthropic`), not an adapter name.
  Restricted cost/quality routing obtains executable names from
  `ProviderRegistry.get_providers_for_model(target)` and checks each `.name`.
  Never pass a pricing vendor to `ModelAccessService.allows_candidate`.
- Before `_is_claude_model` can resolve an application-profile ARN through
  `GetInferenceProfile`, reject if no possible wire ID is allowed: mapped ID,
  plus original name only when legacy compat can send it. Keep the final exact
  wire guard after API selection; allowing a possible original name cannot
  authorize a profile that ultimately selects native InvokeModel.

### 4. Validation & Error Matrix
- Known forbidden target -> 403 `permission_error` before images/tools/headers.
- New forbidden target in stream -> error event, no forbidden wire call or success.
- No permitted routing candidates -> 403; permitted but unavailable -> 503.
- `AccessPolicyDenied` must escape count estimation and generic retry fallbacks.

### 5. Good/Base/Bad Cases
- Good: pin A, refresh mapping to B, invoke A with the admitted snapshot.
- Base: missing successfully-admitted policy remains unrestricted.
- Bad: checking provider `model_id` while invoking unchanged original request.

### 6. Tests Required
- `test_model_access_enforcement.py` asserts actual boto3 `modelId` and SDK HTTP
  JSON `model` across all adapters, threads, refreshes, retries and tool loops.
- Assert no image/sandbox/upstream call on denial, saved PTC current-policy checks,
  routing/filtering before key acquisition, and error without successful SSE end.
- Use real registry/BedrockProvider plus vendor-shaped pricing rows; verify both
  stream modes send the authorized ID, and allowed/no-provider returns 503.
  Forbidden profile tests must assert zero `get_inference_profile` calls, 403
  messages, successful filtered discovery, and candidate skipping.
- Dynamic `web_search_20260209` / `web_fetch_20260209` sandbox boundary is
  `tool_service.standalone_service._get_or_create_session`, not an attribute on
  the outer tool service. Mock execution at that nested sandbox executor and
  prove multiple iterations, cleanup, early denial, and late SSE termination.
- PTC recursion tests must replace stored state (single/batch and immediately
  completed recursive code), then resume with a new facade to prove target/API
  survival and current-policy denial before generator `asend`.

### 7. Wrong vs Correct
```python
# Wrong: refresh can change the target; threads may lose context.
require_model(policy, service._get_bedrock_model_id(request.model))
await service.invoke_model(request)

# Correct: explicit request-owned facade pins the checked wire value.
access = ModelAccessService(service, policy)
access.prepare(request.model)  # before image downloads / tool side effects
await access.invoke_model(request)
```

## Scenario: passthrough Responses stream and search boundaries

### 1. Scope / Trigger
- Changes to `app/api/openai_passthrough/` metadata gating, restricted retrieval,
  or proxy-search accounting must preserve legacy routing and incremental IO.

### 2. Signatures
- `registered_sse_lines(resp, on_response_id, usage_callback, *, normalize=False)`
  gates complete individual SSE frames, not whole streams.
- `open_upstream_stream(..., resolved_url=None, params: str | None=None)` pins a
  verified URL and forwards query parameters; `stream_retrieved_response(resp)`
  closes the response but never records usage (creation already did).
- `SearchUsageAccess.wrap(access)` retains the request's model/provider pins;
  `.observed_usage` accumulates completed nested input/output token counts.
  `record_search_usage_on_failure(service, record)` is scoped to orchestration,
  not metadata registration after successful aggregate accounting.

### 3. Contracts
- SSE fields remove exactly one leading space. The LAST `event` field wins;
  a bare `event` or empty value resets it. Named-event and JSON-type clients can
  disagree: gate response IDs from both interpretations before delivering any
  part of a restricted frame. Preserve original Responses passthrough framing.
- Restricted GET `/responses/{id}?stream=true` verifies owner/model/backend first,
  then uses `send(stream=True)` through the shared opener. Forward repeated query
  keys and pinned credentials; never reselect based on a new body model.
- Legacy boto proxy-search records the default backend, so its facade must use
  `provider_id=None` for native AND Converse. Nonempty key associations must not
  silently change the client used for the invocation being attributed.
- Proxy search currently runs its non-streaming agent loop before constructing
  SSE even for `stream=true`. Both modes preserve observed usage on a late denial;
  success uses only the aggregate once. No new real-time search streaming here.

### 4. Validation & Error Matrix
- Restricted metadata write failure -> entire ID-bearing frame withheld, stream
  error without a success terminal. Available later usage is drained incrementally
  for at most five seconds; unreported future usage cannot be reconstructed.
- Retrieval permission/backend failure -> 403/404 before upstream; connection
  failure -> 502/504; read failure after headers -> error event, close, no DONE.
- Nested allowed invocations followed by denial -> retain observed tokens, deny
  the new target without sending it. This is NOT the five-second drain limitation:
  tokens already returned by a completed nested call must not disappear.

### 5. Good/Base/Bad Cases
- Good: two completed calls at 9/3 each then denial record 18/6 once.
- Base: unrestricted/master CRUD retains historical behavior (including query
  handling); metadata does not impose new universal ownership checks.
- Bad: awaiting `AsyncClient.request` for streaming retrieval buffers completion.

### 6. Tests Required
- `test_openai_access_enforcement.py`: real boto invocation and AUTH destination
  with nonempty provider association; compare parsing with installed OpenAI
  `SSEDecoder`; repeated/reset/mismatched event fields and failed-write withholding.
- AsyncByteStream must assert first delivery before producing later chunks; test
  complete/read-error/disconnect/HTTP-error cleanup, pinned bearer/SigV4 and repeated
  query keys. Assert zero new usage recording on retrieval after creation.
- Real WebSearchService multi-iteration loop + SDK HTTP wire: success, late denied
  target, metadata failure, both requested stream modes; assert totals exactly once.

### 7. Wrong vs Correct
```python
# Wrong: named clients see the last event, but the gate sees the first.
event = next(line[6:].strip() for line in lines if line.startswith("event:"))

# Correct: apply SSE field parsing in order, retaining the last value.
for line in lines:
    name, _, value = line.partition(":")
    if name == "event":
        event = value.removeprefix(" ")
```

## Code Review Checklist

- [ ] Every env/table/field name spelled identically in config, env.example, CDK (both env
      maps), CLAUDE.md, frontend types (see `guides/cross-layer-thinking-guide.md`).
- [ ] No new black/ruff/mypy findings on touched files; baseline untouched.
- [ ] Integration failures == known baseline set only.
- [ ] i18n: every `t('...')` key exists in both `en.json` and `zh.json`, no orphans.
- [ ] Refactors of shared UI (e.g. extracting `useHoverPopover` from `UsageHoverChart`) keep the
      original component's behaviour byte-for-byte (delays, pointer-events, positioning).
