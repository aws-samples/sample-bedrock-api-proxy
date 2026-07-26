# Journal - river (Part 1)

> AI development session journal
> Started: 2026-07-14

---



## Session 1: Mid-conversation tool changes support

**Date**: 2026-07-26
**Task**: Mid-conversation tool changes support
**Branch**: `feat/mid-conversation-tool-changes`

### Summary

Checked whether the proxy accepts Anthropic's mid-conversation-tool-changes-2026-07-01 message format: role:system messages were already allowed but tool_addition/tool_removal blocks failed ContentBlock union validation. Added ToolAddition/ToolRemoval blocks plus tool_reference/mcp_tool_reference/mcp_toolset_reference in app/schemas/anthropic.py, made the Converse path skip system-role messages (Converse only accepts user/assistant), confirmed the InvokeModel path forwards the blocks unchanged with no code change, and added 9 unit tests. Beta header needed no blocklist change. ruff/mypy baselines in this repo are already failing repo-wide; no new violations introduced.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `b478b34` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete
