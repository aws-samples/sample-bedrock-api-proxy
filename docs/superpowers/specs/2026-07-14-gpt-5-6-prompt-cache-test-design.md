# GPT-5.6 Prompt Cache Probe Design

## Goal

Extend `tests_bak/openai_sdk/test_responses_prompt_cache.py` to exercise
GPT-5.6 prompt caching in both request-wide modes:

- `implicit`, with automatic breakpoint placement.
- `explicit`, with a manual breakpoint on stable prompt content.

The existing GPT-5.5 and legacy retention behavior must remain available.

## Command-Line Interface

Add `--prompt-cache-mode` with these values:

- `legacy`: preserve the current `prompt_cache_retention` request shape.
- `implicit`: send `prompt_cache_options={"mode": "implicit", "ttl": "30m"}`.
- `explicit`: send the same TTL with mode `explicit` and add
  `prompt_cache_breakpoint={"mode": "explicit"}` to the stable input block.
- `both`: run isolated implicit and explicit probes sequentially and print a
  comparison.

Use `openai.gpt-5.6-luna` as the default model and a new GPT-5.6 cache-key
default. Existing arguments for model, key, iterations, streaming, and store
remain usable.

## Request Construction

For GPT-5.6 synthetic probes, construct Responses API input as structured
message content rather than a single string. Keep the long stable prefix in
its own `input_text` block and the per-iteration suffix in a following block.
Only explicit mode adds a breakpoint to the stable block.

The installed OpenAI SDK does not expose `prompt_cache_options` as a named
`Responses.create` parameter. Route that request-level field through
`extra_body`; keep known SDK parameters at the top level. Nested breakpoint
data remains in the input dictionary sent by the SDK.

The captured Codex scenario keeps its input unchanged. Cache options are
overridden per run through `extra_body`; the tool does not invent breakpoints
inside captured payloads.

## Probe Isolation And Reporting

When mode is `both`, derive separate stable keys for implicit and explicit
runs so one condition cannot warm the other. Continue reporting request hit
rate and cached-token rate, and add aggregate cache-write tokens to summaries
and the mode comparison.

Read GPT-5.6 writes from
`usage.input_tokens_details.cache_write_tokens`, while retaining the existing
provider-specific fallback field names. Cached reads continue to use
`cached_tokens`.

## Validation

Add unit tests before implementation for:

- Implicit request options without a content breakpoint.
- Explicit request options with a breakpoint on the stable block.
- `prompt_cache_options` routing through `extra_body`.
- Isolation of cache keys between modes.
- Extraction of GPT-5.6 `cache_write_tokens`.
- Preservation of legacy request behavior.

Run the focused unit test file after each red/green cycle. Finish with the
focused test suite and Python syntax compilation. Live API execution is not a
required automated check because it needs a Bedrock bearer token and incurs
model usage.
