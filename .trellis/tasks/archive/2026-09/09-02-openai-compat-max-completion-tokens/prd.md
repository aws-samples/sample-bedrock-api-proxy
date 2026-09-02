# OpenAI-compat: send `max_completion_tokens` instead of `max_tokens`

## Goal
Requests to `/v1/messages` that the proxy routes through Bedrock's OpenAI-compatible
Chat Completions endpoint (`ENABLE_OPENAI_COMPAT`) must work for gpt-5.6 models, which
reject the deprecated `max_tokens` parameter.

## Background (evidence, 2026-09-02)
- `AnthropicToOpenAIConverter.convert_request` copies `request.max_tokens` into
  `"max_tokens"` (`app/converters/anthropic_to_openai.py:71`).
- Mantle us-east-1, `openai.gpt-5.6-sol`: `max_tokens` → 400
  `unsupported_parameter: 'max_tokens' is not supported with this model`;
  `max_completion_tokens` → 200.
- `max_completion_tokens` is accepted by every other model tested: `openai.gpt-5.4`,
  `xai.grok-4.3` (Mantle us-west-2); `moonshotai.kimi-k2.5`, `zai.glm-5`,
  `minimax.minimax-m2.5`, `openai.gpt-oss-120b-1:0` (bedrock-runtime `openai/v1`).
- OpenAI marks `max_tokens` deprecated in favour of `max_completion_tokens`.
- Surfaced by the admin-portal speed test on `openai.gpt-5.6-sol`; affects every client
  request to those models, not just the speed test.

## Requirements
- R1 `convert_request` emits `max_completion_tokens` and never `max_tokens`.
- R2 Debug logging in `app/services/openai_compat_service.py` prints the new key.
- R3 No change to the Responses-API converter (`max_output_tokens`) or the passthrough routes.

## Acceptance Criteria
- [ ] AC1 Unit test: converting a `MessageRequest(max_tokens=N)` yields
      `result["max_completion_tokens"] == N` and `"max_tokens" not in result`.
- [ ] AC2 Existing OpenAI-compat tests pass; `uv run pytest tests/unit` green.
- [ ] AC3 Manual: speed test on `openai.gpt-5.6-sol` through a proxy with
      `MANTLE_ENDPOINT_URL` = Mantle us-east-1 records `status=ok` (after deploy).

## Out of scope
- Retry-on-unsupported-parameter machinery for the compat path (not needed: the new
  parameter is accepted everywhere tested).
- Kimi / GLM / MiniMax not being served by the Mantle route in prod (endpoint/mapping
  configuration issue, separate).
