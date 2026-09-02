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

## Code Review Checklist

- [ ] Every env/table/field name spelled identically in config, env.example, CDK (both env
      maps), CLAUDE.md, frontend types (see `guides/cross-layer-thinking-guide.md`).
- [ ] No new black/ruff/mypy findings on touched files; baseline untouched.
- [ ] Integration failures == known baseline set only.
- [ ] i18n: every `t('...')` key exists in both `en.json` and `zh.json`, no orphans.
- [ ] Refactors of shared UI (e.g. extracting `useHoverPopover` from `UsageHoverChart`) keep the
      original component's behaviour byte-for-byte (delays, pointer-events, positioning).
