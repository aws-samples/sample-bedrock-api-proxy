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


## Session 2: Admin portal model speed test (TTFT/OTPS) via proxy

**Date**: 2026-09-02
**Task**: Admin portal model speed test (TTFT/OTPS) via proxy
**Branch**: `feat/admin-model-speed-test`

### Summary

Planned and shipped the admin-portal model speed test: Speed column on Model Mapping page, POST/GET speed-test routes, SpeedTestManager + anthropic-proxy-speed-tests table (90d TTL), auto-provisioned admin-speedtest key, PROXY_BASE_URL wiring in CDK (cdk.Lazy for CloudFront domain), and fixed converters so thinking type=disabled no longer enables reasoning on OpenAI-compat/Converse. Filled backend database + quality specs.

### Git Commits

| Hash | Message |
|------|---------|
| `0ad006b` | (see git log) |
| `41f3d30` | (see git log) |
| `aab0e45` | (see git log) |
| `a7595c1` | (see git log) |

### Status

[OK] **Completed**


## Session 3: OpenAI-compat: max_completion_tokens fix for gpt-5.6

**Date**: 2026-09-02
**Task**: OpenAI-compat: max_completion_tokens fix for gpt-5.6
**Branch**: `fix/openai-compat-max-completion-tokens`

### Summary

Diagnosed gpt-5.6-sol speed-test 400 (unsupported_parameter max_tokens on Mantle); switched the /v1/messages OpenAI-compat converter to max_completion_tokens after verifying acceptance on gpt-5.6-sol, gpt-5.4, grok-4.3, kimi, glm, minimax, gpt-oss. Also: deployed prod DynamoDB stack (SpeedTestsTable) to unblock ECS deploy; prod page-cache confusion resolved. PR #138.

### Git Commits

| Hash | Message |
|------|---------|
| `0a71bdf` | (see git log) |

### Status

[OK] **Completed**
