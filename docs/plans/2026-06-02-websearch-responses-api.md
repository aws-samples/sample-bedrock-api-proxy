# Web-Search via OpenAI Responses API (per-key provider) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the proxy's server-side web-search agentic loop (triggered by `tools:[{"type":"web_search"}]` on `/openai/v1/responses`) (a) route model calls to the per-API-key provider's endpoint, and (b) drive non-Claude models through the OpenAI **Responses API** with `store=false` instead of Chat Completions — so Responses-only models like `openai.gpt-5.5` work with proxy web search while the proxy keeps conversation state itself.

**Architecture:** The web-search loop in `web_search_service.py` stays in Anthropic `MessageRequest`/`MessageResponse` format and is left **unchanged**. We add (1) a new Anthropic↔OpenAI-Responses converter pair, (2) a `invoke_responses_sync`/`invoke_responses` method on `OpenAICompatService` that calls `client.responses.create(..., store=False)`, (3) optional `base_url`/`api_key` overrides on `OpenAICompatService` + a `use_responses_api` switch on `BedrockService`, and (4) wiring in the passthrough router's web-search branch to build a provider-aware, Responses-mode `BedrockService`.

**Tech Stack:** Python 3.12, FastAPI, OpenAI Python SDK (`responses.create`), Pydantic v2, pytest, respx, moto.

**Empirically verified against live us-east-2 mantle (2026-06-02):**
- `store=false` works statelessly; full `input` sent each turn.
- Continuation needs only `function_call` (`call_id`,`name`,`arguments`) + `function_call_output` (`call_id`,`output`). **Reasoning items do NOT need to be echoed.**
- Output items: `{"type":"reasoning",...}` (ignorable), `{"type":"function_call","call_id","name","arguments"}`, `{"type":"message","content":[{"type":"output_text","text"}]}`.
- `openai.gpt-5.5` supports `/responses` but NOT `/chat/completions`.

**Scope / non-goals:**
- Only the **non-streaming** web-search path (`handle_request`) used by the passthrough is changed. The `/v1/messages` Anthropic streaming web-search path (`handle_request_streaming`) and the general (non-web-search) openai-compat path are out of scope.
- `web_search_service.py` is NOT modified (verify with `git diff --stat`).

---

### Task 1: Anthropic → OpenAI Responses request converter

**Files:**
- Create: `app/converters/anthropic_to_openai_responses.py`
- Test: `tests/unit/test_converters/test_anthropic_to_openai_responses.py`

Convert a `MessageRequest` into kwargs for `client.responses.create`: `model`, `input` (list of items), `tools` (function tools), `tool_choice`, `max_output_tokens`, `store=False`, `instructions` (from `system`).

Mapping rules:
- `system` (str or blocks) → `instructions` (joined text).
- Each message: `user`/`assistant` text → `{"role": <role>, "content": <text>}`.
- assistant `tool_use` block → `{"type":"function_call","call_id":block.id,"name":block.name,"arguments":json.dumps(block.input)}`.
- user `tool_result` block → `{"type":"function_call_output","call_id":block.tool_use_id,"output":<text of block.content>}`.
- `tools` (Anthropic custom/function tools) → `[{"type":"function","name","description","parameters":input_schema}]`. (web_search has already been replaced by a custom tool upstream by `_build_tools_for_request`.)
- Drop `thinking`, `reasoning`, image blocks (web-search loop is text+tools only).

**Step 1: Write failing tests** covering: plain user text; system→instructions; a tool_use→function_call; a tool_result→function_call_output; tools→function tools; `store` always False.

**Step 2:** `pytest tests/unit/test_converters/test_anthropic_to_openai_responses.py -v` → FAIL (module missing).

**Step 3:** Implement `convert_request(request: MessageRequest) -> dict`.

**Step 4:** Re-run → PASS.

**Step 5:** Commit `feat: add Anthropic→OpenAI Responses request converter`.

---

### Task 2: OpenAI Responses → Anthropic response converter

**Files:**
- Create: `app/converters/openai_responses_to_anthropic.py`
- Test: `tests/unit/test_converters/test_openai_responses_to_anthropic.py`

Convert a Responses API response dict → `MessageResponse`.

Mapping rules:
- `output[]` item `message` → text content block(s) from `content[].text` (type `output_text`).
- `output[]` item `function_call` → Anthropic `tool_use` block `{id:call_id, name, input:json.loads(arguments)}`.
- `output[]` item `reasoning` → ignored.
- `stop_reason`: `tool_use` if any function_call present else `end_turn`.
- `usage`: map `input_tokens`/`output_tokens` → Anthropic usage (reasoning tokens into output is fine).
- `model`, `id` (`msg_...`), `role:"assistant"`.

**Steps:** failing tests (final message; tool_use extraction; mixed reasoning+function_call → stop_reason=tool_use; usage mapping) → verify fail → implement `convert_response(resp: dict, model: str) -> MessageResponse` → verify pass → commit `feat: add OpenAI Responses→Anthropic response converter`.

---

### Task 3: `OpenAICompatService` Responses invocation + endpoint override

**Files:**
- Modify: `app/services/openai_compat_service.py`
- Test: `tests/unit/test_openai_compat_responses.py`

Changes:
1. `__init__(self, base_url: str | None = None, api_key: str | None = None)` — build the `OpenAI` client with `base_url or settings.openai_base_url`, `api_key or settings.openai_api_key`. Keep existing default behaviour when both are None.
2. Add `invoke_responses_sync(self, request, request_id=None) -> MessageResponse`:
   - `kwargs = AnthropicToOpenAIResponsesConverter().convert_request(request)`
   - `resp = self.client.responses.create(**kwargs)` ; `resp_dict = resp.model_dump()`
   - `return OpenAIResponsesToAnthropicConverter().convert_response(resp_dict, request.model)`
   - mirror existing logging style (`[OPENAI-COMPAT-RESPONSES] ...`).
3. Add async `invoke_responses(self, request, request_id=None)` wrapper using the same semaphore/executor pattern as `invoke_model`.

**Step 1: Failing test** — mock `OpenAI` so `client.responses.create` returns a stub object whose `.model_dump()` yields a function_call output; assert `invoke_responses_sync` returns a `MessageResponse` with a `tool_use` block. Also assert `base_url`/`api_key` overrides reach the `OpenAI(...)` constructor (patch `app.services.openai_compat_service.OpenAI`).

**Step 2:** run → FAIL.
**Step 3:** implement.
**Step 4:** run → PASS.
**Step 5:** commit `feat: OpenAICompatService Responses API + endpoint override`.

---

### Task 4: `BedrockService` — Responses mode + provider-aware compat client

**Files:**
- Modify: `app/services/bedrock_service.py` (`__init__` ~135-140; `invoke_model` ~650-653)
- Test: `tests/unit/test_bedrock_service_responses.py`

Changes:
1. `__init__(..., openai_base_url: str | None = None, openai_api_key: str | None = None, openai_use_responses: bool = False)`.
   - When building `_openai_compat_service`, pass `OpenAICompatService(base_url=openai_base_url, api_key=openai_api_key)`.
   - Enable the compat service if `openai_base_url` is provided even when global `settings.openai_api_key` is empty (provider supplies the key). Guard: enable if `(settings.enable_openai_compat and settings.openai_api_key and settings.openai_base_url) or (openai_base_url and openai_api_key)`.
   - Store `self._openai_use_responses = openai_use_responses`.
2. In `invoke_model`, the non-Claude branch:
   ```python
   if not self._is_claude_model(request.model) and self._openai_compat_service:
       if self._openai_use_responses:
           return await self._openai_compat_service.invoke_responses(request, request_id)
       return await self._openai_compat_service.invoke_model(request, request_id)
   ```

**Step 1: Failing test** — construct `BedrockService(openai_base_url="https://prov.test/openai/v1", openai_api_key="k", openai_use_responses=True)` with `OpenAICompatService` patched; call `await invoke_model(req)` for a non-Claude model; assert `invoke_responses` (not `invoke_model`) was called.
**Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit `feat: BedrockService Responses mode + provider-aware compat client`.

---

### Task 5: Wire the passthrough web-search branch to use it

**Files:**
- Modify: `app/api/openai_passthrough/router.py` (web-search branch ~211-262)
- Test: `tests/integration/test_openai_passthrough/test_websearch_responses.py`

Changes in `responses_create` web-search branch:
- Keep `base_url, ws_api_key = _resolve_upstream_target(api_key_info)` BEFORE the line that overwrites `api_key` with the proxy key (rename to avoid the clash at current line 216).
- Build the bedrock service provider-aware + Responses-mode for non-Claude:
  ```python
  bedrock_service = BedrockService(
      openai_base_url=base_url,
      openai_api_key=ws_api_key,
      openai_use_responses=True,
  )
  ```
  (When `base_url`/`ws_api_key` are None — key without a provider — fall back to globals; `openai_use_responses=True` still routes non-Claude to Responses, which is the desired behaviour for web search.)

**Step 1: Failing integration test** — key with `provider_id` → provider endpoint `https://prov.test/openai/v1`; `respx` mock for `POST https://prov.test/openai/v1/responses` returning a function_call then (2nd call) a final message; Tavily/web-search provider mocked; POST `/openai/v1/responses` with `tools:[{"type":"web_search"}]`; assert the upstream **`/responses`** route on the provider host was called (NOT `/chat/completions`, NOT the default host) and the final response text is returned.
**Step 2:** FAIL. **Step 3:** implement wiring. **Step 4:** PASS. **Step 5:** commit `fix: web-search loop uses per-key provider Responses API`.

---

### Task 6: Full suite + lint + type check

- `uv run pytest tests/unit/test_converters tests/unit/test_openai_compat_responses.py tests/unit/test_bedrock_service_responses.py tests/integration/test_openai_passthrough -q` → all pass.
- `git diff --stat` → confirm `app/services/web_search_service.py` is **unchanged**.
- `uv run ruff check <changed files>` and `uv run mypy app/converters app/services/openai_compat_service.py app/services/bedrock_service.py app/api/openai_passthrough` → clean.
- Commit any lint fixes.

---

### Task 7: Deploy to dev + live end-to-end verification

- Redeploy dev (same command/flags as before) from this branch.
- Verify via the proxy with key `$PROXY_API_KEY` (your proxy API key):
  ```bash
  curl -s -X POST https://<cloudfront>/openai/v1/responses \
    -H "Authorization: Bearer $PROXY_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"openai.gpt-5.5","input":"What is the latest AWS news?","tools":[{"type":"web_search"}]}'
  ```
  Expected: HTTP 200 with a final answer citing web results (no "model does not exist", no "unsupported on chat/completions").
- Confirm ECS logs show the call hitting `bedrock-mantle.us-east-2.../openai/v1/responses` (not us-east-1, not chat/completions).

---

## Risks / Open Points
- **Streaming**: passthrough web-search uses non-streaming `handle_request` even for `stream:true` clients (events are synthesised afterward), so non-streaming coverage is sufficient. Confirm during Task 5.
- **Citations / result formatting**: the existing loop's tool-result text feeds `function_call_output.output` as a string — verify the Tavily result block serialises to text cleanly via the converter (Task 1 tool_result mapping).
- **`max_output_tokens`**: Anthropic `max_tokens` maps to Responses `max_output_tokens`; ensure non-null.
