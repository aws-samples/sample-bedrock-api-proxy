# Design: per-key access policies

Status: approved by the user on 2026-09-11 after the final planning summary. Implement within these boundaries; return to planning for material scope changes.

## 1. Policy representation and administration

Proposed new attributes on the existing API-key item (names here are design proposals, not existing APIs):

```json
{
  "access_policy": {
    "version": 1,
    "ip": {"enabled": true, "allow": ["203.0.113.8/32", "2001:db8::/48"]},
    "model": {"enabled": true, "allow": ["<exact-upstream-model-id>"]}
  }
}
```

- Missing `access_policy` means legacy unrestricted access. Both dimensions have explicit enabled switches; no truthiness-based permission decisions.
- A present but malformed policy, unsupported version, or enabled empty list fails closed at runtime. Admin validation rejects it before writing. An explicit disabled dimension with an empty list is valid.
- Create/PUT accept a complete, validated policy object. Omitting the object on update preserves it. Explicit null is rejected, not silently swallowed by the current `exclude_none=True` behavior. Disable a dimension explicitly; no null/empty-list clearing sentinel. Replace the whole policy atomically in one DynamoDB update to avoid partial switch/list states.
- Validate IPs with the Python standard library; normalize individual addresses to host networks, reject CIDRs with unintended host bits rather than silently broadening them, remove duplicates, and reject scoped IPv6, DNS names, ports, and wildcard expressions in allowlist entries. Normalize IPv4-mapped IPv6 consistently for both peers and rules. Bound each dimension to 100 entries; cap each model identifier at 2048 characters and the serialized policy at 64 KiB. These are proposed v1 safety limits.
- Model allow entries are literal upstream IDs, case-sensitive; never resolve stored policy IDs through the live alias mapping during authorization. The admin chooser can show aliases but submits the selected exact target, displayed before save. Manual exact IDs/ARNs remain possible for models absent from the catalogue. Provider-account permissions remain governed by existing provider configuration, not a new field here.
- Wire manager creation/update, backend schemas, frontend types/forms, list indicators, and English/Chinese strings. Preserve unrelated fields and old API clients. New CLI-created keys remain unrestricted unless explicitly configured through the admin API; new CLI policy switches are out of scope.

The auth cache stores policy data, never the result of IP authorization. Evaluate the current request's IP after every key lookup, including a cache hit. Use strongly consistent API-key reads on cache misses to make the configured cache window meaningful after a successful admin write; preserve single-flight and deep-copy isolation. The default window is 60 seconds, not prompt-cache `cache_ttl`. Do not cache transient lookup failures or fall back to expired policy data. An admitted request uses its policy snapshot; no forced cancellation of already running streams.

## 2. Client IP trust boundary

Resolve the source once, before rate limiting and handler side effects, in/alongside `AuthMiddleware`. Distinguish the transport peer from an asserted forwarded address. Do not combine an already rewritten ASGI `client` with a second independent XFF parser.

Recommended v1 deployment contract:

| Topology | Source selection | Required trust prerequisite |
|---|---|---|
| Direct/local Docker | Transport peer; ignore forwarded headers | No upstream address rewriting |
| ALB to proxy | Rightmost XFF address | Transport peer is an allowed ALB subnet address, security group permits the proxy port only from ALB, ALB XFF append mode |
| CloudFront to ALB to proxy | Second-from-right XFF address | Above conditions, plus the API listener validates the distribution-specific secret and cannot bypass CloudFront; both hops append |
| Other reverse proxy | Explicit fixed trusted-hop configuration, never auto-detected | Operator verifies an invariant topology, trusted ingress peers, and appended/sanitized headers |

Proposed application settings: `CLIENT_IP_TRUSTED_PROXY_CIDRS` (default empty) and `CLIENT_IP_TRUSTED_PROXY_HOPS` (default 0). Restrict trusted CIDRs to actual ingress subnets/peers, reject trust-all CIDRs and invalid configurations, and bound hop count and header size. A fixed hop count is safe only with the listed topology guarantees; it is not a substitute for authenticating ingress. Do not infer trust from an arbitrary header or claim to support mixed-length ingress paths with one setting.

For managed launches, disable Uvicorn proxy-header rewriting (`--no-proxy-headers`, verified with the installed CLI help) and let one app component own source attribution. CDK supplies ALB subnet CIDRs, chooses one or two hops, explicitly sets append mode with XFF port preservation disabled, and preserves/reviews the existing security-group and CloudFront listener protections. Document the same launch requirement for manual Uvicorn users; an externally rewritten peer must not be accepted as proof of a valid forwarded chain.

Trusted-proxy mode rejects a restricted request if the peer is not trusted, XFF is missing/oversized/malformed, the chain is too short, or the selected token is not an address. Direct mode ignores attacker-supplied XFF. Parse IPv6 with a standard parser; do not use a naive colon split or silently strip arbitrary ports. Unrestricted keys keep their existing access behavior.

The observed client is the NAT/VPN/forward-proxy exit address. No attempt is made to identify a workstation behind that exit. No external IP-range downloads or extra lookup per request are needed. Deployment smoke tests, not unit tests alone, must prove both spoof resistance and the observed exit address before enabling an IP policy in production.

## 3. Actual-model authorization

One reusable policy evaluator is shared by HTTP handlers, routing/failover selection, and outbound services. The authorization object is immutable and request-scoped; never put mutable per-key policy on a globally reused Bedrock client or service. Explicit propagation into tool loops and executor work must be tested; a ContextVar alone does not propagate through arbitrary `run_in_executor` calls.

### Resolve, authorize, and send the same target

1. Preserve the client model name for response compatibility and diagnostics. An alias is not an authorization entry.
2. Select the candidate according to the existing API/routing mode. Resolve that candidate using the effective mapping appropriate to that outbound adapter. Do not change unrestricted old OpenAI-compat behavior incidentally.
3. Compare the exact outbound `model`/`modelId` to the literal stored allowed set. Mapping-table errors may retain existing resolver fallback behavior, but the fallback target must itself pass authorization.
4. Send that already-resolved target without resolving it again. The permission check and invocation must use the same object/value even if a remote mapping refresh occurs between them. Repeat at each newly selected retry/loop/continuation target.

No equivalence is inferred between regional/global prefixes, versions, bare IDs, or inference-profile ARNs. An ARN authorizes that exact upstream resource, not all its underlying models; externally changing what a resource represents is outside the proxy's identity guarantee. Display this limitation in documentation.

### Entry and outbound coverage

- `app/api/messages.py`: validate policy before image fetching, sandbox/tool side effects, or streaming response creation. For non-routed paths, authorize the prepared target immediately. For routed paths, do not reject a client name merely because its default target is disallowed: select an authorized candidate before work begins.
- The Bedrock invocation/counting boundary must check the final native InvokeModel, Converse, Runtime Responses, and old OpenAI-compat outgoing IDs. An early check is useful for 403 responses but is not the security boundary by itself.
- PTC, standalone execution, web-search/fetch loops, and PTC continuations must inherit the admitted policy. A continuation may use a saved original model; authorize that actual saved/prepared target, not only the latest request body.
- `/v1/messages/count_tokens` applies the same permission policy even when it would use local estimation. Authorization failures must not be caught as an upstream error and turned into an estimated success.
- OpenAI chat/completions and Responses creation authorize after mapping and before opening an upstream connection. Parameter-negotiation retries retain the same authorized model. Each nested web-search call also passes the outbound guard.
- Both model-list surfaces filter with the same actual-target matcher. Model details enforce it; preserve response shapes and correct pagination metadata rather than returning disallowed entries or misleading page counts.

### Routing and failover

Filter the candidate set before cost/quality/smart selection and before key-pool acquisition; skip disallowed rule/failover targets. A key allowing only one model must never cause a classifier/auxiliary upstream inference outside its policy. Local routing computations are not model calls.

No permitted route produces 403; permitted routes with no available provider/key produce the existing availability error (503). Final outbound authorization is still required. `BedrockProvider` currently accepts `model_id` but invokes the original request (`app/services/bedrock_provider.py:45,73`); do not mistake the logged `target_model` for the sent model. Make only the minimal target-propagation correction needed for restricted requests and add a wire-level assertion. If consistent enforcement would require a broader unrestricted routing behavior change, return to planning rather than silently expand this task.

## 4. Stateful endpoints and errors

### Responses ownership and target metadata

Extend the existing response-context table; do not add a table or store ordinary request/response content just for authorization. Add a versioned authorization metadata row distinguished from existing `META`/`CHUNK#` records. It records the response ID, a non-plaintext key-owner identity, exact authorized target, provider/endpoint identity, creation/expiry, and routing kind (proxy-managed vs upstream). Never persist credentials or a user-supplied arbitrary endpoint. For model-restricted stateful operations, resolve credentials fresh from the recorded, still-valid provider configuration; if the provider destination has changed incompatibly, deny rather than redirect a stored ID to a different tenant.

- Register new response IDs for streaming and non-streaming create calls, including proxy-managed web search. Capture metadata at the first ID-bearing SSE event, before forwarding that event, not only after successful completion. Do not buffer the full stream.
- Reuse the configured response-context TTL (currently 3600 seconds by default); authorization lookup uses a strongly consistent read and explicitly checks `expires_at`, since DynamoDB TTL deletion is asynchronous. Document that a model-restricted key cannot use an ID beyond the locally verifiable retention window even if the upstream still retains it.
- For model-restricted keys, check owner, current admitted model permission, and stored upstream identity before GET/DELETE/cancel/input_items and before using `previous_response_id`. Check both the historical target and any newly requested target. A permitted model change within the same verified backend is possible; do not migrate upstream IDs between endpoints.
- Missing, expired, or pre-upgrade records without sufficient metadata are rejected for model-restricted keys without probing the upstream. Expired-but-not-yet-deleted and physically deleted authorization rows both deny: expiry cannot grant access.
- Keys without model restrictions and master retain existing response-ID behavior, whether new metadata is live, expired, or absent. Preserve existing proxy-managed context ownership checks, but do not add partial/time-limited tenant isolation for unrestricted upstream Responses. Metadata registration supports later enabling model restrictions; it is not an authorization gate for currently unrestricted operations. Universal unrestricted-key ownership enforcement is a separate feature.
- For model-restricted access, the proposed external status for unknown/foreign/unverifiable IDs is generic 404, avoiding an existence oracle; an owned ID whose model is now forbidden returns 403. Backend metadata read failure returns 503 and never grants access.
- Ordinary response metadata is small; storage is off the event loop. For model-restricted creates, failure to register metadata prevents successful ID delivery (503 before headers, otherwise the surface's stream error and close). For unrestricted creates, best-effort registration failure may preserve existing behavior, but that response will be unusable after model restrictions are enabled. Do not report a rolled-back upstream operation: creation may already have incurred usage, which must still be accounted for.
- Preserve existing proxy-managed response capabilities rather than implementing a full local CRUD API. For model-restricted keys, a verified locally generated ID must not accidentally be forwarded to upstream CRUD endpoints that cannot own it; return a protocol-shaped 400 unsupported-operation response for those operations. Preserve proxy-managed continuation storage, protected by the new metadata checks for restricted keys. Successful verified upstream deletion retains an owner-bound deletion marker until expiry, so repeated restricted access is consistently rejected without reissuing the operation. Metadata writes are idempotent and conditional: an existing ID cannot have its owner/backend overwritten. Treat cross-backend ID collisions as a conflict and fail closed rather than rebinding a response ID.

### Error and audit contract

Invalid keys retain 401. Valid keys failing IP/model access return 403 in the endpoint's existing protocol envelope. Public health/docs exemptions and `REQUIRE_API_KEY=False` retain their current behavior; do not advertise per-key restrictions when authentication is disabled.

Known denials occur before streaming headers and before inference/tool side effects. A new target denied during an already admitted stream terminates with a protocol error without calling that target; HTTP status cannot change after headers. Never emit a successful terminal event for a denied operation or turn a permission error into a fallback result.

Log denial reason, request ID, resolved client address where operationally necessary, and model identifiers. Follow existing secret-safe logging conventions: no raw API key, authorization header, full forwarded-header dump, or new key-derived identifiers in logs. Do not persist message bodies for access auditing. Infrastructure failures and policy denials have distinct operational signals.

## 5. Compatibility, rollout, and evidence

### Rollout and rollback

Deploy schema/runtime support to every proxy worker before enabling policies in the admin portal; an older binary will ignore the new fields. Verify forwarding topology with controlled allowed/denied exit addresses and forged headers first. Then restrict a dedicated non-master canary key, verify both API surfaces, and confirm policy changes become visible after the advertised cache window. Do not run deployments from this planning task.

Do not treat rollback to an old binary as safe for restricted keys. Drain old/new workers deliberately; use a compatible enforcement version or revoke affected keys and wait for the validation window before a rollback. No data deletion/backfill is required. Existing inactive/budget-exceeded and speed-test key behavior remains unchanged. Public health endpoints remain usable by ALB/container health checks.

### Repository anchors

- Key persistence: `app/db/dynamodb.py:469,538,750`; admin update field omission: `admin_portal/backend/api/api_keys.py:181`.
- Auth cache and bypasses: `app/middleware/auth.py:89,162,181,207,237`; cache default: `app/core/config.py:166`.
- Early messages processing and routing: `app/api/messages.py:297,882,907,927,945,1450`; failover selection: `app/keypool/failover.py:52`.
- Actual API selection: `app/services/bedrock_service.py:252,763,972,1203,1844`; old OpenAI-compat body preserves the request model ID: `app/converters/anthropic_to_openai.py:70`.
- Saved PTC model: `app/services/ptc_service.py:1455,2974`; provider target mismatch: `app/services/bedrock_provider.py:45,73`.
- OpenAI mapping and stateful routes: `app/api/openai_passthrough/router.py:314,462,738,783,794,805,816`; SSE interception: `app/api/openai_passthrough/streaming.py:144`.
- Existing context owner storage/lookup: `app/api/openai_passthrough/context_store.py:137,159`; no model metadata there.
- Models discovery: `app/api/models.py:28,79`; admin schemas: `admin_portal/backend/schemas/api_key.py:7,23,40`.
- Launch: `Dockerfile:79`; ALB-only task ingress: `cdk/lib/network-stack.ts:77`; CloudFront headers and listener: `cdk/lib/ecs-stack.ts:431,437,445,459`.
- Response-context table is already provisioned and granted to the proxy (`cdk/lib/ecs-stack.ts:149,253`; `cdk/lib/dynamodb-stack.ts:512`).

### External protocol evidence

AWS ALB documentation states default XFF mode is append and warns that only secured systems' entries can be trusted:
https://docs.aws.amazon.com/elasticloadbalancing/latest/application/x-forwarded-headers.html

AWS CloudFront documentation states it appends the TCP viewer IP to any existing XFF; an ELB may then append the CloudFront peer. This supports right-anchored selection only when ingress topology is enforced:
https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/RequestAndResponseBehaviorCustomOrigin.html

The installed Uvicorn 0.42.0 proxy middleware was inspected: it rewrites ASGI client for trusted peers, and wildcard trust chooses the first XFF entry. Avoid that setting. Exact deployment smoke results are deferred to implementation/release verification, not asserted here.
