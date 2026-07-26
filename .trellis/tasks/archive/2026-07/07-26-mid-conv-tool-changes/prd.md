# Support mid-conversation tool changes message format

## Goal

Accept and forward Anthropic's **mid-conversation tool changes** beta message format
(`mid-conversation-tool-changes-2026-07-01`) through the proxy, so clients can add/remove
tools mid-conversation without invalidating the prompt cache.

Today the proxy rejects such requests at Pydantic validation: `ContentBlock`
(`app/schemas/anthropic.py:274`) has no `tool_addition` / `tool_removal` member, so a
`role: "system"` message carrying those blocks fails with a union validation error.

## Background (verified 2026-07-26)

Official format (docs: `build-with-claude/mid-conversation-system-messages`):

- Beta header: `mid-conversation-tool-changes-2026-07-01`
- Models: Claude Fable 5, Mythos 5, Opus 4.8, Opus 5 — on Claude API, **Amazon Bedrock**, Vertex
- Shape: a `{"role": "system"}` message whose `content` array holds `tool_addition` /
  `tool_removal` blocks (mixable with `text` blocks). Each block has a `tool` field referencing
  an already-declared tool:
  - `{"type": "tool_reference", "name": "<tool name>"}`
  - `{"type": "mcp_tool_reference", "server_name": "...", "name": "..."}`
  - `{"type": "mcp_toolset_reference", "server_name": "..."}`
- The top-level `tools` array never changes; `defer_loading: true` withholds a tool until a
  `tool_addition` surfaces it.
- Placement rules (enforced by Anthropic/Bedrock, not by us): a `system` message must follow a
  `user` turn (or an assistant turn ending in a server tool result) and must precede an
  `assistant` turn or end the array.

Current proxy state:

| Item | State |
|---|---|
| `role: "system"` in `Message` | already allowed (`app/schemas/anthropic.py:352`) |
| plain-text mid-conv system message | already forwarded by InvokeModel path (verified by running the schema) |
| `tool_addition` / `tool_removal` blocks | **rejected at validation** |
| `tool_reference` block | exists but only as a `tool_result` child with field `tool_name` (different shape) |
| beta header passthrough | works — not in `beta_headers_blocklist` (`app/core/config.py:272`) |
| `defer_loading` on tools | supported (`app/services/bedrock_service.py:484-575`) |
| Converse path (non-Claude) | would forward `role: "system"` verbatim → Bedrock Converse 400 |

## Requirements

1. **Schema**: add request-side blocks so the new format validates on `/v1/messages` and
   `/v1/messages/count_tokens`:
   - tool-change reference union: `tool_reference` (`name`), `mcp_tool_reference`
     (`server_name`, `name`), `mcp_toolset_reference` (`server_name`)
   - `tool_addition` and `tool_removal` blocks, each with `tool` + optional `cache_control`
   - both added to `ContentBlock`
   - must not break the existing `tool_reference` block used inside `tool_result` content
     (field `tool_name`), which stays as-is
2. **InvokeModel path** (Claude models): forward the blocks unchanged in the native request
   body; ensure no existing strip/rewrite branch in
   `_convert_to_anthropic_native_request` drops or mangles them.
3. **Beta header**: no blocklist change needed; keep passthrough. Do **not** auto-inject the
   beta header — it stays client-driven.
4. **Converse path** (non-Claude models): do not send `role: "system"` messages to Converse.
   Skip them (with a log line) rather than producing a Bedrock 400.
5. **No behaviour change** when the feature is unused: requests without `role: "system"`
   tool-change blocks must serialize byte-identically to today.
6. Feature is not flag-gated (it is pure format passthrough, same as `defer_loading`).

## Out of scope

- Validating placement / ordering rules of system messages (server enforces).
- Implementing tool add/remove semantics proxy-side (proxy-side tools like web_search /
  code_execution are not affected).
- OpenAI passthrough and OpenAI-compat paths.
- Admin portal / DynamoDB changes.

## Acceptance Criteria

- [ ] A request with `tools` + a trailing `{"role":"system","content":[{"type":"tool_removal","tool":{"type":"tool_reference","name":"get_weather"}}]}` validates as `MessageRequest` without error.
- [ ] `mcp_tool_reference` and `mcp_toolset_reference` variants also validate.
- [ ] `tool_addition` validates, and `text` blocks may be mixed into the same system message.
- [ ] The native InvokeModel body produced for such a request contains the system message with the tool-change blocks intact (types and fields preserved, `None` fields omitted).
- [ ] Existing `tool_result` content containing `{"type":"tool_reference","tool_name":...}` still validates and still round-trips.
- [ ] Converse conversion of a request containing a `role: "system"` message does not emit that message into `bedrock_request["messages"]`.
- [ ] Unit tests cover the above; `uv run pytest` passes; `black`/`ruff`/`mypy app` clean.
