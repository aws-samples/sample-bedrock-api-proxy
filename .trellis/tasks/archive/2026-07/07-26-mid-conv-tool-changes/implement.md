# Implementation plan

## 1. Schema (`app/schemas/anthropic.py`)

Add, just after `ToolReferenceContent`:

```python
class ToolChangeToolReference(BaseModel):
    type: Literal["tool_reference"] = "tool_reference"
    name: str

class ToolChangeMcpToolReference(BaseModel):
    type: Literal["mcp_tool_reference"] = "mcp_tool_reference"
    server_name: str
    name: str

class ToolChangeMcpToolsetReference(BaseModel):
    type: Literal["mcp_toolset_reference"] = "mcp_toolset_reference"
    server_name: str

ToolChangeReference = Union[
    ToolChangeToolReference,
    ToolChangeMcpToolReference,
    ToolChangeMcpToolsetReference,
]

class ToolAdditionContent(BaseModel):
    type: Literal["tool_addition"] = "tool_addition"
    tool: ToolChangeReference
    cache_control: Optional["CacheControl"] = None

class ToolRemovalContent(BaseModel):
    type: Literal["tool_removal"] = "tool_removal"
    tool: ToolChangeReference
    cache_control: Optional["CacheControl"] = None
```

Then append `ToolAdditionContent, ToolRemovalContent` to the `ContentBlock` union
(`anthropic.py:274`). `CacheControl` is defined after these classes, so keep the
forward-ref string form — the module already does this for `ToolResultContent`; verify a
`model_rebuild()` is not needed (run the validation snippet).

Leave `ToolReferenceContent` (`tool_name`) untouched: it is the `tool_result` child shape.
Both use `type: "tool_reference"` but live in different unions, so there is no conflict.

## 2. InvokeModel passthrough (`app/services/bedrock_service.py`)

`_convert_to_anthropic_native_request` (~line 307-398) already dumps blocks generically.
Audit the branches for accidental damage:

- `fallback` strip — not applicable.
- `msg.role == "assistant"` server-tool skip — not applicable (role is `system`).
- `web_search_tool_result` rewrite — not applicable.

Expected: no code change needed. Confirm with a test that inspects the produced body; add a
short comment noting that `tool_addition`/`tool_removal` pass through verbatim only if a
reviewer would otherwise wonder.

## 3. Converse guard (`app/converters/anthropic_to_bedrock.py`)

In `_convert_messages` (~line 372), skip messages whose `role == "system"` with a
`[CONVERTER] Skipping mid-conversation system message (unsupported by Converse API)` log.
Keep it a plain skip — do not merge into the `system` field, since that would change
semantics/cache behaviour.

## 4. Tests (`tests/unit/test_converters.py`, `tests/unit/test_schemas.py` if present)

- validates: `tool_removal` + `tool_reference`; `tool_addition` + `mcp_tool_reference`;
  `mcp_toolset_reference`; `text` mixed with a tool-change block in one system message
- rejects nothing new: existing `tool_result` + `tool_reference`/`tool_name` case still passes
- native body: system message present with block types/fields intact
- Converse: `role: "system"` message not present in converted `messages`

## 5. Validation commands

```bash
uv run pytest tests/unit -q
uv run pytest -q
black app tests && ruff check app tests && mypy app
```

## Rollback

Single commit touching 3 source files + tests; `git revert` is sufficient. No config, DB, or
deployment changes.
