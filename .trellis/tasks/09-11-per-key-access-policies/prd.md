# Per-key IP and model access policies

## Goal

Allow an administrator to constrain each proxy API key by client source IP and by the actual upstream model it may invoke, without changing unrestricted historical keys.

## Approval Status

The user approved the final planning summary, including restricted-key legacy/expired Responses behavior, with “确认” on 2026-09-11. Implementation is now authorized for R1-R7 under this plan. No deployment or unrelated changes are authorized.

## Background and Evidence

There is no existing per-key source-IP or model authorization policy. Keys already carry per-key settings in `app/db/dynamodb.py:469,750`, with validation at `:538`. A shared authentication middleware serves both authentication header styles (`app/middleware/auth.py:162,237`); its default cache window is 60 seconds (`app/core/config.py:166`).

Request names are not necessarily actual targets. Mapping, API selection, routing, and failover are separate steps (`app/services/bedrock_service.py:252`; `app/api/messages.py:907,927,945`). Saved PTC continuations can use an earlier model (`app/services/ptc_service.py:1455,2974`). The current Bedrock provider does not apply its separate `model_id` argument to the request it invokes (`app/services/bedrock_provider.py:45,73`).

Models endpoints do not filter per key (`app/api/models.py:28,79`; `app/api/openai_passthrough/router.py:816`). Ordinary response-ID routes forward without local owner/model checks (`app/api/openai_passthrough/router.py:738,783,794,805`); existing proxy-managed context stores owner but not model (`app/api/openai_passthrough/context_store.py:137,159`).

The deployment supports ALB and optional CloudFront. The Docker launch has no explicit trust configuration (`Dockerfile:79`); ECS accepts ALB ingress (`cdk/lib/network-stack.ts:77`) and CloudFront uses a secret-protected listener (`cdk/lib/ecs-stack.ts:431,445,459`). Source attribution must be secured before enabling IP restrictions.

## Requirements

### R1 — Independent per-key policies

Administrators can independently enable IP and actual-model allowlists. Missing policies on historical keys remain unrestricted. An enabled dimension must have at least one valid entry; corrupt/unknown policy data must not grant access. Editing unrelated key fields preserves policy. Disabling a restriction is explicit, not an accidental consequence of an empty/null value.

### R2 — Source-IP enforcement

Support individual IPv4/IPv6 addresses and CIDRs. Evaluate every authenticated request, including cache hits, using its own source address. Forwarded addresses are trusted only through an explicitly verified proxy topology. A restricted key with an unidentifiable source is denied. Client-controlled forwarding headers must not let a disallowed source impersonate an allowed source. The identified address is the externally visible NAT/VPN exit, not an end-user workstation behind it.

### R3 — Actual-model permissions

Authorize the literal model/resource sent upstream, not only a client-facing alias. Equivalent aliases that select the same exact target are usable. Changing a mapping cannot enlarge a key's stored permissions. Routing, failover, retries, and tool/continuation calls remain within the allowed set. Authorization and invocation use the same resolved target despite concurrent mapping refreshes. Do not implicitly equate regional/global prefixes, versions, bare IDs, or ARNs. An ARN denotes the exact resource, not an immutable promise about externally managed resource internals.

### R4 — Complete supported API coverage

Enforce across messages, token counting, OpenAI chat/completions and Responses, streaming/non-streaming, and server-side tools/continuations. Filter model lists and protect model details. Known denials occur before upstream calls or tool side effects and return 403 in the API's protocol envelope; invalid-key behavior stays 401. A denial discovered after a stream began terminates with an error, never a success or forbidden upstream fallback.

### R5 — Administration and observability

Expose create/edit controls, exact target preview for alias selections, manual exact-model entry, list restriction indicators, and English/Chinese validation text. Reject malformed CIDRs rather than silently expanding their range. Explain update latency and stateful-retention limitations. Access-denial logs must be useful without exposing keys, authorization headers, or message bodies.

### R6 — Compatibility and activation window

Preserve master-key, disabled-auth, public-health/docs, inactive/budget-exceeded, and unrestricted historical-key behavior. Use the existing configurable cache window (60 seconds by default), no distributed invalidation. After a successful policy write, subsequently admitted requests converge within that configured window under healthy reads; already admitted requests/streams use their snapshot and are not forcibly terminated. Lookup failures do not become permissive fallback. All proxy workers must understand enforcement before administrators enable it; rollback to an older non-enforcing binary is unsafe.

### R7 — Stateful response access (proposed compatibility details for final review)

Record ownership and the actual target/backend of newly created Responses, including streaming and proxy-managed responses, without storing ordinary message content for this purpose. For a model-restricted key, before response-ID access or continuation, verify that the ID belongs to the caller and that its historical target, plus any new target, is currently allowed. Do not send its stored IDs to a different backend after provider configuration changes.

For model-restricted keys, missing/expired/pre-upgrade attribution is denied without probing upstream. Unknown/foreign/unverifiable IDs return generic 404; an owned ID whose model is forbidden returns 403. Authorization-metadata read failures fail closed. Retention uses the existing configurable response-context TTL (default one hour); a restricted key must start a new request when an old ID is no longer verifiable. Expired-but-present and physically deleted metadata both deny restricted access.

Keys without model restrictions and master retain their existing response-ID behavior, including existing proxy-managed context owner checks; new metadata alone does not change their authorization. This task guarantees stateful access control for model-restricted keys, not universal tenant isolation for unrestricted Responses. Do not introduce a full local CRUD API for proxy-generated IDs.

## Acceptance Criteria

- [x] **AC1 (R1, R5):** Create/edit/reload two keys with different policies; each dimension operates independently. Omitted policy remains unchanged; explicit disable works; enabled-empty, null, malformed, and unknown-version inputs cannot silently remove restrictions.
- [x] **AC2 (R2):** Allowed/disallowed IPv4, IPv6, and CIDR boundary cases behave correctly. Forged XFF cannot bypass direct, ALB, or enforced CloudFront+ALB modes. Missing/invalid trust evidence denies restricted keys. A second request from another IP cannot reuse a cached IP pass.
- [x] **AC3 (R3):** Two aliases and direct target A work when A is authorized; B does not. Remapping an alias to B does not authorize B. Wire-level mocks prove the checked model is the one sent, including refresh races, threads, routing, and failover.
- [x] **AC4 (R3, R4):** All supported inference/count/tool paths have positive and negative tests in both stream modes where applicable; denial asserts no forbidden upstream/tool call. Model discovery hides forbidden targets. Token-estimation fallback cannot swallow permission errors.
- [x] **AC5 (R6):** Legacy keys, master, public health and disabled-auth remain compatible. Multi-worker/cache-expiry tests prove the documented update window and fail-closed read failure; admitted streams are not retroactively cancelled.
- [x] **AC6 (R7):** Owned/foreign/unknown/expired/legacy Responses and changed backends are handled as specified. Verify streaming ID capture before delivery, metadata outages, and historical plus new model permissions for continuations. No full-stream buffering or key/content leakage is introduced.
- [x] **AC7 (R5, R6):** Admin UI round-trips policies with both locales, explains exact model targets and latency/retention, and preserves unrelated settings. Rollout/rollback instructions prevent mixed-version bypass.
- [x] **AC8 (all):** Focused policy/regression tests pass, and broader tests/lint/build/synthesis introduce no new failures versus measured baselines. Production IP correctness is not claimed without actual ingress smoke tests.

## Out of Scope

Blacklists, wildcard/regex model rules, user/group RBAC, new database tables or dependencies, CLI policy flags, automatic discovery of arbitrary proxy chains, immutable enforcement of external inference-profile internals, full repair of historical unrestricted Responses ownership, and new local Responses CRUD functionality. No distributed instant revocation, forced cancellation of active streams, production deployment, or unrelated routing fixes.

## Risks and Deferred Verification

- Trust configuration is deployment-sensitive; verify actual source addresses and forged headers before activating IP policies in production.
- Model-restricted keys intentionally cannot resume unverifiable old/expired Responses. New metadata adds a small persistent write and a lookup for stateful access; failures may block restricted operations even after upstream creation has incurred usage.
- Exact target propagation spans old and new adapters. Any necessary broader unrestricted behavior change requires another planning review.
- The repository has historical quality failures; measure current baselines instead of equating pre-existing failures with regressions.

## Planning Deliverables

`design.md` defines data/trust/authorization contracts and source anchors. `implement.md` defines serial implementation slices and a verification matrix. Both were reviewed in the final planning summary and approved by the user. Execution follows the task activation gate and the verification matrix.
