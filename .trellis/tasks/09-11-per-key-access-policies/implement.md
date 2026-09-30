# Implementation plan: per-key access policies

Status: final planning summary approved by the user on 2026-09-11. Execute after the Trellis activation gate. Record completed slices and verification below.

## Ordered work

Use one task with serial implementation slices: both policies share the same key schema, auth cache, error handling, UI, and integration acceptance. No parallel writers. Before each slice, read the actual target files and relevant specs; proposed modules/tests may be added only after naming/location conventions are verified.

1. **Preflight and baselines.** Load `trellis-before-dev`, this task's artifacts, backend database/quality guidelines, and the cross-layer guide. Record existing worktree changes; do not touch unrelated Trellis upgrades or user files. Capture current focused/full test and lint baselines. Verify Uvicorn launch flags and frontend/CDK tool availability. After user approval, activate this task using the local Trellis gate; do not confuse creation consent with implementation consent.
2. **Shared policy contract and storage (R1, R5, R6).** Add validated versioned policy types and pure IP/model matchers. Wire DynamoDB create/update/get and admin schemas/routes. Preserve omitted policy on PUT, reject null, and atomically replace complete policy. Add consistent auth reads without changing single-flight/error/cache semantics. Test legacy, malformed, disabled, enabled, size, CIDR normalization, and concurrent cache isolation cases using moto where storage is involved.
3. **IP enforcement and ingress (R2).** Own raw-peer attribution in one component; configure direct, ALB, and CloudFront+ALB trust/hop modes, bounded parsing, and fail-closed behavior. Extend managed launch/config/CDK wiring without opening security groups or changing health exemptions. Exercise the ASGI transport plus proxy configuration, not only a mocked Request. Add an operator deployment checklist and examples in existing documentation.
4. **Actual-model enforcement (R3, R4).** Prepare/resolve/check the outbound model once and pass it unchanged to native/Converse/Runtime Responses/old OpenAI-compatible adapters. Propagate immutable policy through services, worker threads, tool loops, saved PTC continuations, and token counting. Filter routing/failover candidates before selection; fix only target propagation necessary for restricted requests. Assert mock upstream wire IDs, not only decisions/logs. Maintain original client-facing model IDs and unrestricted behavior.
5. **OpenAI and stateful coverage (R4, R7).** Cover chat/completions, responses, both SSE forms, parameter retries, web search, lists/details, and response-ID operations. Extend existing response-context table with metadata-only authorization rows. Handle first ID-bearing SSE frames, interrupted streams, retention, owner/endpoint checks, legacy records, and persistence failure. Do not buffer full streams or expand local CRUD capabilities. Verify no denied continuation contacts an upstream or starts tools.
6. **Admin UI and documentation (R1, R5, R6, R7).** Add independent toggles, IP/CIDR inputs, exact-model chooser/manual input, target preview, validation errors, restriction badges, and explicit save-latency/response-retention notices. Read `ApiKeys.tsx` and surrounding components before changing them; use existing styles. Update both i18n files, TypeScript types, relevant operator/API docs and configuration examples. Do not redesign unrelated UI or add frontend dependencies.
7. **Integrated quality gate.** Run focused then full suites; prove no bypass across requested entry points and no regression for historical/master/disabled-auth behavior. Run `trellis-check`, frontend build, CDK build/synth where available, and secret-safe logging checks. Compare new failures to freshly measured baselines rather than assuming the historical spec is current. Review rollout/rollback and cache-window documentation. Any broader product/compatibility change returns to planning.
8. **Delivery.** Load `trellis-finish-work` when implementation is complete. Report exact changes, executed checks, limitations, and pending production smoke checks. Do not deploy, commit, delete data, install dependencies, or modify history without separate authorization.

## Verification matrix

| Requirements | Required negative/positive proofs |
|---|---|
| R1 policy lifecycle | Old item/missing policy remains unrestricted; independent enable/disable; active empty/null/unknown-version rejected; malformed stored policy denies; PUT omission preserves; exact types and size bounds; disabled empty list never mistaken for permission denial or accidental enable |
| R2 client address | IPv4/IPv6/CIDR boundaries; IPv4-mapped IPv6; NAT exit behavior; direct spoofed XFF ignored; trusted one-/two-hop chains with forged prefix; untrusted peer; missing, duplicate, malformed, oversized or short XFF; header port handling; Uvicorn does not rewrite peer; cached key reused from a second denied IP remains denied |
| R3 actual targets | Two aliases of A allowed; direct A allowed; B denied; remap alias to B denied; refresh between prepare/send cannot change wire target; regional IDs and ARNs not implicitly equivalent; routing/failover never uses B; no permitted candidate versus unavailable permitted candidate; concurrent keys do not share policy |
| R4 endpoint completeness | Both authentication headers; native InvokeModel, Converse, Runtime Responses, old OpenAI-compatible mode; messages and OpenAI streaming/non-streaming; every server-side tool path and saved PTC target; count_tokens estimator cannot swallow denial; model-list filtering and details; parameter retries; denial before first side effect/SSE where knowable |
| R5 administration | Create/edit/reopen round trip; alias target preview and manual exact ID; validation does not silently widen CIDR; list badges; English/Chinese parity; unrelated key settings preserved; save-latency notice |
| R6 cache/compatibility | Same key multiple workers after expiry; no cache on transient read error; no stale authorization fallback; auth disabled/master behavior; accepted stream uses admitted snapshot; legacy CLI/internal speed-test keys; no plaintext key in denial logs |
| R7 stateful | Restricted owner versus different key; owned forbidden model; unknown/legacy/expired ID; expired-but-present versus TTL-deleted metadata both deny; unrestricted/master response-ID behavior unchanged regardless of new metadata; known identity before restricted forwarding; changed endpoint; prior model and new model both authorized; created/completed/interrupted SSE; conditional/idempotent writes and collisions; lookup/write outage; usage preserved if upstream already executed; no complete-stream buffering |
| Release safety | CDK topology assertions including CloudFront listener bypasses; all workers enforcement-capable before enabling; deployed canary real/forged source checks; safe rollback without old workers ignoring policies |

Test additions must assert forbidden upstream/service mocks were not called. A 403 alone is insufficient if work already ran. New cloud/database paths use moto and real table definitions; tests must not contact paid model endpoints.

## Commands and release gates

Existing focused baseline command (repository-local Python; avoids automatic dependency installation and coverage artifact updates):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -o addopts='' -p no:cacheprovider \
  tests/unit/test_auth_cache.py tests/unit/test_openai_passthrough/test_auth.py \
  tests/unit/test_openai_response_context_store.py -q
```

After implementation add the new policy tests to the focused run, then run existing broader regression targets:

```bash
.venv/bin/python -m pytest tests/unit
.venv/bin/python -m pytest tests/integration/test_multi_provider.py tests/integration/test_openai_passthrough
(cd admin_portal/frontend && npm run build)
(cd cdk && npm run build && npx --no-install cdk synth -c environment=dev)
```

Use installed black/ruff/mypy against changed files/app as appropriate, compare output to pre-change baselines, and do not run repository-wide autofix. The quality spec records historical baseline failures (including frontend ESLint configuration); remeasure them and report rather than silently repair. Do not install missing tools. Synthesis may need an environment/context unavailable locally; distinguish that blocker from a passing infrastructure check and never deploy as a workaround.

Frontend create/edit behavior requires a local browser/manual validation with mocked or authorized test services. Load the available browser skill before executing it. Confirm no production admin API is called. Topology smoke tests requiring actual ALB/CloudFront resources are a release checklist, not work authorized by this task.

Task review artifacts: `prd.md` (requirements), `design.md` (contracts/evidence), this file (execution/verification). If implementation is delegated later, curate real spec/research entries in `implement.jsonl` and `check.jsonl` per Trellis before activation; they are not replaced by this document. Do not fabricate a phase transition from a planning-only task.

## Planning verification record (2026-09-11)

- Focused existing auth/cache/context-store command above: **25 passed in 1.44s**. This is a baseline, not validation of the unimplemented feature.
- Installed Uvicorn CLI confirms `--no-proxy-headers` is supported.
- Planning structure check: required documents exist, R1-R7 map to the verification matrix, no scaffold placeholders, task status remains `planning`.
- Read-only independent review found a TTL/legacy ownership ambiguity. Resolved by explicitly limiting new stateful authorization to model-restricted keys; unrestricted/master behavior remains unchanged regardless of metadata presence. Restricted keys deny both expired-but-present and physically deleted metadata. This boundary is included in the final review summary, not silently treated as approved universal tenant isolation.
- No product files changed; no new-feature tests, frontend build, CDK synthesis, deployment, or production ingress smoke test has been performed in planning.

## Implementation baseline (2026-09-11)

- Task activation succeeded: `in_progress`. Trellis reports session identity unavailable; active pointer not persisted (degraded mode). Agents must use this explicit task path.
- `pytest -o addopts='' -p no:cacheprovider tests/unit tests/integration/test_multi_provider.py tests/integration/test_openai_passthrough -q`: **725 passed, 17 failed, 4 warnings**, 24.52s. All 17 failures are existing OpenAI passthrough chat/responses integration cases (mock expectations on the older upstream path); no product edits preceded this run.
- Frontend `npm run build` and CDK `npm run build`: both passed. Existing frontend chunk-size/Browserslist notices retained.
- `ruff check app admin_portal/backend tests --output-format concise`: **1524 existing findings**. `mypy app --no-error-summary`: **285 existing errors**. Do not reformat/fix unrelated baseline.
- Full baseline logs: `/home/ubuntu/.darwin/sessions/-home-ubuntu-workspace-sample-bedrock-api-proxy--e5eb1407bbc72365998641e19e7f432f115f0229f048f4c25065ffcb5ae2d631/session-20260911-010203283/background/` (`bg-ee2489ab-dfa1-4d43-bf0b-27662ac62dc9.log` tests; `bg-31a38e21-57e2-4c36-a7c0-edd3e85c4e02.log` lint/types; `bg-1134109e-6894-4011-b377-a0d4c7878e76.log` builds).




## Slice 1 implementation record (2026-09-11)

Shared contract/storage/admin groundwork implemented; task remains **in_progress**.
No frontend, IP middleware/deployment, outbound guards or stateful enforcement added.

- Added `app/schemas/access_policy.py`: `AccessPolicy`, `IPAccessPolicy`,
  `ModelAccessPolicy`; strict complete v1 payload, explicit switches, list/count/byte
  bounds, normalized/deduplicated IP rules, literal model IDs. `parse_stored_access_policy`
  accepts numeric DynamoDB `Decimal(1)` only at the read boundary. String/bool/float
  versions remain invalid. Model identifiers are not trimmed or resolved.
- Added `app/core/access_policy.py`: `policy_from_key_info(key_info) -> ParsedAccessPolicy`,
  `require_model(policy, target_model) -> None`, `require_ip(policy, source_ip) -> None`;
  `ParsedAccessPolicy.allows_model(str) -> bool`, `.allows_ip(str | None) -> bool`.
  Snapshots expose `ip_enabled`, immutable `ip_ranges` triples, `model_enabled`,
  `model_allow` frozenset. `UNRESTRICTED_POLICY` is a frozen singleton.
- `AccessPolicyDenied(PermissionError)` has `.reason` values `invalid_policy`,
  `model_not_allowed`, `ip_not_allowed`; no credentials/raw payload in its message.
  Future HTTP adapters must translate to 403 and must not swallow this as a retry or
  estimator fallback. Parse only **after successful authentication or its explicit
  bypass**: None/{} represent disabled auth, not permission to accept failed lookups.
  `is_master is True` bypasses. Present corrupt/null policy raises denial.
- IPv4-mapped IPv6 peers and rules contained in `::ffff:0:0/96` normalize to IPv4.
  Native IPv6 ranges such as `::/0` intentionally do not authorize IPv4/mapped peers.
- APIKeyManager create/update use omission sentinel, reject explicit null before
  writes, replace the whole document atomically, normalize valid policy reads while
  retaining corrupt values for denial. Auth validation and admin detail get reads
  are strongly consistent; existing cache/single-flight code is unchanged.
- Admin create/update schemas reject null (422). PUT uses field presence for policy,
  not `exclude_none`; unrelated optional fields retain prior behavior. Responses
  omit `access_policy` for legacy keys. Corrupt stored admin policies fail response
  validation instead of appearing unrestricted; a complete valid PUT can repair them.
- Added 118 test cases in `test_access_policy.py` and `test_access_policy_storage.py`.
  Shared database spec records the executable contract and mapped-address behavior.

### Verification

- Focused new + auth/cache/context suite: **143 passed**, 2 existing deprecation warnings.
- Full measured baseline selection: **843 passed, 17 failed**, 4 warnings. The 17
  OpenAI passthrough failures are the same baseline set; no new test failures.
- New files: Black check and Ruff clean. Touched existing files: **267 Ruff findings**
  before/after, no new findings. Full Ruff still **1524 findings**.
- `mypy app --no-error-summary`: **272 errors**, down from 285, no new diagnostics
  after normalizing line numbers. Dictionary annotation/typed policy read return paths
  removed existing errors in the touched manager. Focused schema/evaluator/admin-schema
  mypy passes with `--follow-imports=silent --explicit-package-bases` (without explicit
  package bases the admin source is discovered under two module names).
- Logs: `/tmp/policy-slice1-tests-final.txt`, `/tmp/policy-slice1-mypy-final.txt`,
  `/tmp/policy-slice1-ruff-full.txt`, `/tmp/policy-slice1-ruff-{before,after}.json`.
- No dependencies installed, no commits, no deployment. Do not activate admin policies
  until later slices enforce them across all proxy workers. Production ingress smoke
  tests and final task-wide quality gate remain pending.


## Slice 2 implementation record (2026-09-11)

Client source-IP enforcement and ingress wiring are complete; task remains
**in_progress**. No outbound model guards, Responses ownership, or UI changes.

### Changes and public contracts

- New `app/core/client_ip.py`: `ClientIPTrust.from_config(cidrs: str, hops: int)`
  validates deployment configuration and returns a frozen trust snapshot;
  `.resolve(scope) -> ClientIPResult` reads the raw transport peer plus raw headers.
  Frozen result fields: `source_ip: str | None`, `reason: str`,
  `forwarded_proto: str | None`. It never rewrites `scope['client']`.
- `CLIENT_IP_TRUSTED_PROXY_CIDRS` is a comma-separated string, default empty;
  `CLIENT_IP_TRUSTED_PROXY_HOPS` defaults to 0. Settings and middleware construction
  validate the same contract: 0–8 hops, at most 100 CIDRs / 8192 config characters,
  no host-bit CIDRs, wildcard/all-address-family trust (including /0 unions),
  mismatched empty-CIDRs/positive-hops, float/bool hops, scoped addresses or ports.
- Direct mode ignores forwarding headers. Trusted fixed-hop mode selects from the
  right only after peer-CIDR validation; all XFF tokens must be valid address
  literals. Limits: one header, 8192 bytes, 64 tokens. Duplicate/missing/short/
  malformed/oversized chains are indeterminate. IPv4-mapped IPv6 is canonical IPv4;
  IPv6 ingress is supported. Repeated valid address tokens are not duplicate headers.
- Auth resolves source once per request, parses policy only after successful auth,
  calls existing `require_ip` on every cache hit/miss, and attaches frozen
  `request.state.access_policy`. Attribution state: `request.state.client_ip` and
  `request.state.client_ip_reason`. Master/auth-disabled attach the unrestricted
  singleton; public exemptions remain unchanged (no policy attached there).
  Model-only policies do not require a determinate IP. Lower-layer
  `AccessPolicyDenied` is not caught by the admission adapter.
- Denials return 403 with path-specific Anthropic/OpenAI envelopes; invalid keys
  remain 401 (OpenAI auth errors now use OpenAI shape too). Denial logs contain
  safe reason/source and a server-generated `x-request-id`, no credentials/header
  dumps. Existing auth lookup exception logging now logs only exception type,
  removing potentially secret-bearing messages/tracebacks while preserving caching.
- Docker CMD, `main.py`, and `app/main.py` disable Uvicorn proxy rewriting.
  Trusted singleton `X-Forwarded-Proto: http|https` updates only ASGI scheme so
  slash redirects preserve proxy scheme. Direct/untrusted/invalid/duplicate proto
  is ignored. Existing CloudFront HTTP origin may report HTTP at ALB; docs explain
  canonical paths and production redirect verification rather than guessing HTTPS.
- CDK injects actual ALB public-subnet CIDRs, chooses 1/2 hops by CloudFront mode,
  and explicitly sets ALB XFF append mode / client-port preservation off. No SG or
  listener widening. Topology assertions cover Fargate/EC2, direct ALB/CloudFront,
  every forwarding rule's distribution secret, rejecting default action, ALB-only
  task/host SGs and private Fargate ingress. Docker-image construction is mocked in
  these tests to avoid irrelevant asset hashing/staging; synthesis uses real code.
- README operator checklist + env.example cover raw-peer launch invariant,
  NAT/VPN exits, limits, trust prerequisites, cache/update window, staged rollout,
  unsafe old-binary rollback and required production smoke tests.

Files changed in this slice: `app/core/client_ip.py`, `app/core/config.py`,
`app/middleware/auth.py`, `app/main.py`, `main.py`, `Dockerfile`,
`cdk/lib/ecs-stack.ts`, `cdk/test/client-ip.test.ts`, `env.example`, `README.md`,
`tests/unit/test_client_ip.py`, `tests/unit/test_access_policy_auth.py`, this record.
Existing slice-1 files and unrelated worktree changes were not modified.

### Verification and caveats

- Added **91 Python test cases**. Final focused command includes new IP/auth tests,
  slice-1 evaluator/storage tests, parent auth-cache/OpenAI auth/context-store
  baseline: **234 passed**, 4 deprecation warnings, 5.18s.
- Final full measured selection (`tests/unit`, multi-provider integration,
  OpenAI passthrough integration): **934 passed, same 17 failed**, 6 warnings,
  28.12s. Compared failing test names with slice-1 log: no additions/removals.
- New Python files: Black check and Ruff pass. Touched existing Python files:
  **43 Ruff findings before/after**, no new normalized diagnostics. Full Ruff
  remains **1524**. `mypy app --no-error-summary`: **272 errors**, identical
  normalized error set to slice 1; standalone client-IP module mypy passes.
- Frontend `npm run build` passed (unchanged frontend; existing size/tool notices).
  CDK `npm run build` passed; `node --test test/client-ip.test.js`: **4 passed**.
  CDK synth passed with installed CLI, compiled app, offline/no-staging flags:
  `AWS_EC2_METADATA_DISABLED=true ./node_modules/.bin/cdk synth --app 'node bin/app.js' --no-lookups --no-staging -c environment=dev -o /tmp/policy-slice2-cdk-synth`.
  Synthesized proxy env is `10.0.0.0/24,10.0.1.0/24` and hops `1` for dev.
  No same-environment pre-change synth was available (existing output is prod);
  topology assertions and targeted source diff replace a like-for-like template diff.
- Logs: `/tmp/policy-slice2-tests-final.txt`, `*-tests-all-final.txt`,
  `*-mypy-final.txt`, `*-ruff-{before,after}.json`, `*-ruff-full.txt`,
  `*-cdk-test.txt`, `*-cdk-synth.txt`, `*-frontend-build.txt` (same prefix).
- Production ALB/CloudFront and EC2 bridge/NAT source preservation remain untested.
  App cannot recover/detect an externally rewritten ASGI peer: manual deployments
  **must** preserve the raw peer. Fixed-hop trust requires authenticated ingress,
  not merely matching CIDRs. No Docker image build, dependency install, commit,
  deployment, paid model calls, or full-task completion performed.



## Slice 3 implementation record (2026-09-11)

Anthropic-format actual-model enforcement implemented. Task remains **in_progress**;
OpenAI passthrough router/context/SSE and UI are deliberately untouched.

### Inventory and implementation

- Inventoried messages/models endpoints, Bedrock/OpenAICompat adapters,
  routing/rules/smart/failover, BedrockProvider, standalone/search/fetch loops, and
  all seven PTC Bedrock invocation sites before editing. All tool inference flows
  already accept an explicit `bedrock_service`; they now receive a request-owned
  facade rather than a global mutable policy or ContextVar.
- Native/Converse/Runtime use mapped wire IDs; old compatibility uses ORIGINAL
  request names. CountTokens has a separate original-name Claude predicate and
  always sends a mapped Converse ID when it calls upstream. Prepared targets
  preserve these differences, pin API choice and target, and retain client names.
- New `app/services/model_access.py` owns explicit propagation (details below).
  Restricted Converse conversion uses a fresh converter with `resolved_model_id`
  because the existing converter caches mutable `_resolved_model_id` on itself.
  This avoids a second mapping read and cross-request converter state races.
- Messages preflight runs before image downloads, sandbox creation/resume, context
  compression and response headers. Routing selection for restricted requests is
  performed at preflight and reused. Final adapters check exact wire values;
  sync workers get the immutable policy/prepared target explicitly. Existing
  service-tier retries reuse prepared payloads. Token estimation cannot swallow
  `AccessPolicyDenied`; known denials are 403 `permission_error`, late denials
  terminate with a protocol error without a forbidden call or successful end.
- Candidate filters apply before rule/cost/quality selection and key acquisition;
  failover skips forbidden targets. No allowed route is 403; allowed-but-unavailable
  remains 503 (including exhausted permitted source with no usable failover).
- **Smart routing finding:** `SmartRouter.classify` calls RouteLLM
  `Controller.completion`, not a pure classifier. Restricted requests never invoke
  it; they locally prefer allowed strong then weak model, matching the existing
  high-complexity fallback without unguarded auxiliary calls. Unrestricted routing
  remains unchanged. Unknown future provider types fail the restricted candidate
  contract until they implement exact-target propagation.
- BedrockProvider formerly ignored `model_id`. Only restricted requests use the
  pinned selected target, keeping the response's original client model name.
- PTC saves `original_target` and `original_api` alongside `original_model` on new
  restricted executions, including single/batch and recursive state copies.
  Continuations authorize the saved target under the CURRENT admitted policy before
  resume. Legacy state resolves its saved model name once and checks it. No policy
  snapshot is saved in session state. HTTP PTC responses retain current client names.
- Model lists filter with adapter-aware exact-target matching; details preflight
  then send that target. Pagination shape remains `has_more=False` for the existing
  unpaginated lists. Removed the existing partial API-key debug print in messages;
  new denial logs contain reason/request ID only.

### Public propagation API for the next passthrough slice

```python
from app.services.model_access import ModelAccessService, PreparedModel

# HTTP admission: use request.state.access_policy, never reparse a mutable key row.
# Create only for model-restricted requests; leave unrestricted calls unchanged.
access = ModelAccessService(service, policy, provider_id)
prepared = access.prepare(client_model)  # -> frozen PreparedModel(target, api)
await access.invoke_model(request)       # tools receive access as bedrock_service
```

- `.prepare(name)` checks and memoizes a target **within this request only**.
  `.bind(name, PreparedModel(target, api))` checks a saved/routed target and binds
  it without mapping again. Do not store the facade in app.state/shared providers.
- BedrockService `invoke_model`, `_invoke_model_sync`, `_invoke_model_sync_inner`,
  `_invoke_model_native_sync`, `invoke_model_stream`, `count_tokens`, and count/
  streaming workers accept explicit `access_policy=UNRESTRICTED_POLICY`; applicable
  entry/sync methods also accept `prepared_model=None`. `prepare_model(name, policy)`
  and `prepare_count_model(name, policy)` perform no inference-client creation.
- OpenAICompatService `invoke_model[_sync/_stream]` and
  `invoke_responses[_sync/_stream]` accept `access_policy=UNRESTRICTED_POLICY`,
  check the converted outgoing JSON `model`, and pass policy to worker arguments
  or per-call closure. **These are Anthropic-format adapters, not the passthrough
  authorization implementation.** Passthrough should authorize its own exact
  post-mapping body using pure `require_model`, pin it through negotiations/retries,
  and explicitly pass the admitted snapshot through its nested calls.
- `preflight_model(service, name, state=None)` and `saved_model_fields(service, name)`
  are PTC/tool helpers. `guard_model_stream(events, response_model)` and
  `model_denial_event(exc)` are Anthropic SSE helpers only; they do not parse OpenAI
  passthrough SSE or implement Responses ownership/metadata.
- `RoutingEngine.route(..., candidate_allowed=access.allows_candidate)` and
  `FailoverManager.find_failover(..., candidate_allowed=...)` filter before work.
  BedrockProvider `invoke[_stream](..., model_access=access)` uses the selected pin.

### Verification and remaining scope

- Added **82 focused model-enforcement cases**, exercising actual boto3 `modelId`
  kwargs and real OpenAI SDK HTTP JSON via MockTransport: all adapters, both stream
  modes, aliases/direct IDs, remap and atomic settings replacement races, thread
  isolation, tier retries, count API/estimation, candidate/key filtering, local
  smart routing, provider target repair, HTTP early 403, lists/detail, standalone/
  search/fetch multiple iterations and late denials, PTC saved-target completion,
  single/batch state creation, current-policy denial, and pre-resume HTTP denial.
- Combined policy/auth/cache/context/new suite: **316 passed**, 4 existing warnings
  (`/tmp/policy-slice3-focused-final3.txt`). New files plus touched previously-clean
  OpenAICompat module pass Black check; new files pass Ruff. Remaining 13 touched
  existing files already failed Black at HEAD; no broad formatting was applied.
- Final full measured selection (`tests/unit`, multi-provider integration,
  passthrough integration): **1016 passed, same 17 failed**, 6 warnings, 28.87s.
  Compared failing names against slice 2: no additions/removals. Log:
  `/tmp/policy-slice3-tests-final.txt`.
- Full Ruff **1524**, mypy **272**: identical normalized diagnostic multisets
  (filename/code/message, excluding line-number movement), not just equal counts.
  Logs `/tmp/policy-slice3-ruff-{before,final}.json` and
  `/tmp/policy-slice3-mypy-{before,final}.txt`. `git diff --check` passes.
  No frontend/CDK changes or builds in this backend-only slice.
- No paid model calls, live Docker execution, dependencies, commits or deployments.
  Recursive PTC state copies and dynamic search/fetch bash branches were inventoried
  and audited; not every recursive/dynamic permutation is separately exercised.
  OpenAI passthrough/Responses attribution/UI and production ingress checks remain
  future slices/release gates. Do not enable policies on partial deployments.


## Slice 4 implementation record (2026-09-11, recovery)

OpenAI passthrough/stateful backend slice implemented; task remains **in_progress**.
No UI, deployment, dependencies, commits, or unrelated worktree edits. Recovery
first read the actual partial diff, new modules/tests, and `/tmp/policy-slice4-*`
logs; it did not replace the earlier schema/IP/native slices.

### Contracts and exported APIs

- `backend_target.resolve_verified_target(key_info, model, provider_manager=None,
  *, native=False) -> BackendTarget`: reads one provider row consistently,
  decrypts that snapshot's credentials, and returns a request-owned URL/auth
  snapshot. Identity records configured provider/endpoint/region/revision/API,
  never credentials. Provider edits invalidate old restricted IDs; default
  configured auth is fingerprinted. Ambient role/profile remains an operator
  deployment boundary, not a new STS account-discovery feature.
- `response_access.ResponseAuthorizationStore(table, ttl_seconds=None)` uses
  `response_id` + `chunk_id='AUTH#v1'` in the existing response-context table.
  `register(id, *, api_key, model, backend, kind)` is conditional/idempotent;
  `authorize(id, *, api_key, policy)` reads consistently, checks explicit expiry,
  owner, historical exact model, and tombstone; `mark_deleted(item)` conditionally
  preserves an owner/backend-bound deletion marker until the original expiry.
  Registration never refreshes TTL or overwrites owner/model/backend.
- `ResponseRegistration(store, policy, api_key, model, backend, kind='upstream')`
  is the async ID callback (`await registration(id)` / `.json(data)`). Storage
  runs in `asyncio.to_thread`; failure is fatal only for model-restricted creates.
  The transient `.target` contains fresh auth for URL pinning and is never stored.
- `registered_sse_lines(resp, on_response_id, usage_callback, *, normalize=False)`
  gates one bounded SSE frame before delivery, handles named/data-only/multiline
  and EOF frames, and ignores tool/item IDs. A restricted malformed or >1 MiB
  frame fails closed. Unrestricted byte framing remains unchanged, including its
  legacy line-oriented usage extraction. At most 100 response IDs per request
  are registered; excess restricted IDs fail closed.
- Both streaming adapters accept `on_response_id`; `open_upstream_stream` accepts
  optional `resolved_url` to prevent endpoint reselection. Chat conversion uses
  the verified response ID even if the first event is completed, never an invented
  ID for restricted traffic. Error paths close upstream and account observed usage
  in `finally`; no successful terminal chunk follows registration denial.
- Routes use `request.state.access_policy`, not mutable cached key policy. Pure
  `require_model` checks the mapped body and each chat negotiation retry. Restricted
  search receives the existing `ModelAccessService` facade plus a pinned private
  native/Responses client; nested calls retain the exact target and snapshot.
  Legacy proxy-search attribution inspects/pins its existing adapter without
  changing native/Converse/Responses selection or imposing restricted access.
- Restricted previous-ID and GET/DELETE/cancel/input_items verify current owner,
  historical model, and a freshly configured historical backend; continuation also
  verifies its new model/backend. Unknown/foreign/expired/legacy/unverifiable ->
  generic 404; owned forbidden -> 403; metadata infrastructure failure -> 503.
  Verified proxy IDs return 400 for unsupported CRUD, not an upstream request.
  Unrestricted/master CRUD does not consult metadata; existing proxy context owner
  checks remain. Chat's pre-existing converter does not implement conversation
  continuation via `previous_response_id`; supplying it is checked, not silently
  upgraded into a new Chat Completions feature.
- Model listing filters exact mapped IDs before local pagination and rebuilds
  visible cursors/count/total. Existing standard OpenAI list shape is preserved.

### Recovery findings and fixes

The inherited 142-case test file passed, but source review found: no metadata for
unrestricted proxy-search IDs; named/multiline unrestricted frames missed IDs;
restricted chat completion-first streams exposed synthetic IDs; native provider
URLs could gain `/openai/v1`; configured native credentials were lost; and filtered
`total` incorrectly shrank after a cursor. These now have targeted regressions.
Additional checks cover provider SigV4 on actual HTTP wire, current admitted
snapshot isolation, nested denied search calls, context-load preflight, corrupt
metadata, default/native/Converse attribution, and credential-bearing URLs.

A recovery broad run exposed **one actual regression**: a historical Responses
fixture omits SSE separators, so frame-only extraction lost usage. Restored the
unrestricted line-compatible extraction; its isolated test and the later broader
selection pass relative to baseline. No baseline tests were rewritten.

After a first-ID registration outage, a bounded **5-second incremental drain**
collects available later usage while withholding every remaining ID/output/success.
No full stream is buffered. If upstream never reports usage, disconnects, or exceeds
that bound, unreported token counts cannot be reconstructed; this is not an upstream
rollback. Non-streaming/proxy-search usage is recorded before metadata registration.
Proxy AUTH registration now precedes content-store saves on restricted paths, so a
metadata collision cannot overwrite another owner's proxy context first.

### Verification

- New OpenAI test file now contains **183 cases** (142 inherited partial cases +
  41 recovery cases). Combined policy/IP/native/OpenAI/auth/cache/context command:
  **499 passed, 4 existing warnings, 62.55s**. Log:
  `/tmp/policy-slice4-recovery-focused-final3.txt`.
- Final measured baseline selection (`tests/unit`,
  `tests/integration/test_multi_provider.py`,
  `tests/integration/test_openai_passthrough`, with `-o addopts='' -p no:cacheprovider`):
  **1199 passed, same 17 failed, 6 warnings, 90.21s**. Exact failing test-name set
  compared to `/tmp/policy-slice3-tests-final.txt`: no additions/removals. Log:
  `/tmp/policy-slice4-recovery-tests-all-final3.txt`.
- Restored legacy SSE usage regression + then-current OpenAI suite: **177 passed**
  (`/tmp/policy-slice4-recovery-focused-final2.txt`). Later edge run: **7 passed,
  176 deselected** (`/tmp/policy-slice4-recovery-edges.txt`); all included in final
  combined/full runs above.
- Full Ruff **1524**, mypy **272**: identical normalized filename/code/message
  multisets to slice 3, not merely equal counts. Logs:
  `/tmp/policy-slice4-recovery-ruff-final.json`,
  `/tmp/policy-slice4-recovery-mypy-final.txt`. Scoped OpenAI package/new test Ruff
  passes; `git diff --check` passes.
- Black passes for `backend_target.py`, `response_access.py`, `streaming.py`.
  **Formatting gate remains open:** Black still requests formatting of router and
  the new test file; chat adapter also had pre-existing Black differences at HEAD.
  This is explicitly not a clean Black gate. No broad formatter was run (all
  product edits used fileEditor). Log `/tmp/policy-slice4-recovery-black-final.txt`.
- No frontend/CDK builds in this backend-only slice. No live AWS inference,
  deployments, dependency installation, or commits.

### Remaining release/task scope

- Admin UI/i18n and task-wide operator retention/rollout documentation remain the
  next slice; this record is the backend handoff, not full task completion.
- No universal ownership protection was added for unrestricted Responses (R7 is
  still restricted-only). Metadata failures preserve unrestricted behavior but
  those IDs cannot become verifiable later. Legacy adapter/config mismatches are
  logged as unattributable instead of recording a guessed destination.
- Production ingress, real provider identity rotation, paid model calls and live
  SDK disconnect timing are not verified here. Configured provider revision changes
  deliberately invalidate old IDs, even for non-destination provider edits.


## Slice 5 implementation record (2026-09-11)

Admin frontend + bilingual operator docs implemented; task remains **in_progress**.
No backend/CDK product files, formatting, routing behavior, dependencies, commits,
or deployments changed in this slice. Existing source-IP README/env work preserved.

### Changes / contract

- `admin_portal/frontend/src/types/api-key.ts`: complete `AccessPolicy` / dimension
  types; optional non-null `access_policy` on read/create/update payloads.
- `src/components/AccessPolicyEditor.tsx`: independent accessible switches, newline
  IP/CIDR and exact model-ID/ARN lists, current mapping chooser with target preview
  and explicit **Add exact target**, retained disabled lists, restriction badges.
  Uses existing Tailwind form/table styling; no new table columns or redesign.
- `src/pages/ApiKeys.tsx`: create/edit integration, omit policy entirely when
  untouched (legacy and existing keys), submit complete document after deliberate
  policy edits, await mutation and retain form/input on errors. No null sentinel,
  unrelated fields/routing defaults unchanged. Closing/cancelling unmounts the draft.
- `src/utils/accessPolicy.ts`: practical local validation, strict literal IPv4/IPv6
  parser and host-bit rejection without masking/normalization; 100 entries before
  deduplication, 2048 Unicode characters per ID, 64 KiB compact UTF-8 payload.
  Trims line-edge whitespace; server remains authoritative and normalizes IPs.
  New known aliases are rejected with their target shown; chooser only appends the
  returned `bedrock_model_id`. Previously saved literals are not reinterpreted by
  refreshed mapping data. Catalogue failures retain manual exact-ID entry.
- Reuses unmodified `useModelMappings` / existing `/api/model-mapping` endpoint.
  The chooser is a mapping snapshot, not backend-mode discovery; notices explain
  literal IDs, mapping changes, routing/failover, API-mode wire targets and ARNs.
- `src/utils/apiErrors.ts` + small `src/services/api.ts` change: FastAPI 422 detail
  arrays now render safe location/message lines rather than `[object Object]`.
  Does not display validation `input` or `ctx` values. Form adds localized failure
  heading; authoritative backend messages remain verbatim (normally English).
- `src/i18n/{en,zh}.json`: matching editor, validation, badge and safety messages.
  Explicit default 60s key-policy activation, no forced stream cancellation,
  existing response-context TTL default 1h, restricted unverifiable ID behavior,
  master/auth-disabled bypass, NAT exit, raw-peer trust, all-worker rollout and
  unsafe old-binary rollback notices.
- `README.md`, `README_ZH.md`, `env.example`: administration/API payload and omission
  examples, disabled-list semantics, model identity limits, response retention and
  deployment boundaries. Removed obsolete “backend/UI still pending” README text.
  Verified actual admin API path is `/api/keys` (not the UI path `/admin/api-keys`).

### Verification / artifacts

- Added dependency-free Node tests: `node --test tests/access-policy.test.mjs`
  from frontend: **47 passed**. Covers IP/CIDR edges, disabled/empty lists, bounds,
  drafts/roundtrips, alias target behavior, safe error formatting and locale parity.
  Log `/tmp/policy-slice5-unit-tests.txt`.
- Frontend `npm run build` (TypeScript + Vite): **passed**; existing Browserslist
  age and large-chunk warnings retained. Log `/tmp/policy-slice5-frontend-build.txt`.
- `npm run lint`: **blocked by pre-existing missing ESLint 9 flat config**, same
  baseline as task preflight. Log `/tmp/policy-slice5-lint.txt`. Additionally ran
  installed ESLint programmatically, with in-memory recommended TypeScript/hooks
  config, against all six touched TS/TSX files and HEAD baseline: **0 findings / 0
  new findings**. No config file added. Log `/tmp/policy-slice5-scoped-lint.txt`.
- Existing backend schema/storage tests, read-only regression:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -o addopts='' -p no:cacheprovider tests/unit/test_access_policy.py tests/unit/test_access_policy_storage.py -q`
  -> **118 passed**, two existing Pydantic deprecation warnings.
  Log `/tmp/policy-slice5-schema-tests.txt`. Full backend/CDK gates not rerun because
  this slice changed neither layer; parent retains task-wide verification gate.
- Added optional browser harness `tests/access-policy.browser.mjs`; loads installed
  Playwright via `PLAYWRIGHT_MODULE`, no package changes. Ran Chromium against
  `npm run preview -- --host 127.0.0.1 --port 4175 --strictPort` (local built UI).
  All `/api/*` intercepted with in-memory mocks, all non-local HTTP requests
  aborted, no production admin/model endpoints or real API keys used. Loaded
  agent-browser/playwright skills before validation.
- Browser **passes in en + zh**, 8 mock writes per locale, zero page JS errors:
  legacy unrelated-edit omission/settings preservation; independent IP-only and
  model-only creates; enabled-empty, host-bit and known-alias rejection; visible
  exact target preview and submitted target (not alias); backend 422 retained form;
  exact reopen; disable/retain/re-enable; cancellation discards unsaved draft;
  catalogue outage/manual entry; table badges; narrow 390px fields/actions.
  Log `/tmp/policy-slice5-browser.txt`; `/tmp/policy-slice5-ui/results.json`.
- Screenshots: `/tmp/policy-slice5-ui/{en,zh}-editor.png`, `{en,zh}-table.png`,
  `{en,zh}-mobile-editor.png`, `{en,zh}-mobile-notices.png`. Inspected editor,
  badges and narrow notices/actions with imageViewer. External font requests were
  intentionally blocked, so legacy Material Symbols appear as text in screenshots;
  no attempt to redesign/fix existing external-font dependency.
- Initial harness failures were mock/locator setup (actual API is `/api/keys`;
  legacy icon text prefixes the create button accessible name), fixed in harness
  and docs before final successful runs. No product behavior relaxed to pass tests.
- `git diff --check` passes. No installs, commits, task completion, deployment,
  paid inference or production source-IP claims. Production ingress/real backend
  identity rotation remain release checks; backend formatting gate from slice 4
  is left to parent/reviewer as requested.

### Remaining limits for review

Manual IDs not in the catalogue cannot be distinguished from arbitrary aliases by
frontend syntax alone; they are deliberately treated as administrator-entered
literals, never resolved implicitly. Runtime literal authorization remains the
security boundary. Existing saved IDs are preserved despite mapping changes.
Browser mocks prove frontend payload/state, not DynamoDB persistence; the existing
118 schema/storage tests provide separate backend contract evidence. No universal
unrestricted Responses isolation is claimed. Task-wide final review/spec update
and integrated quality gate remain with the parent agent.

## Scoped formatting correction (2026-09-11)

- Read current source and compared Black diffs with HEAD before editing. HEAD's
  `router.py` is Black-clean; all current formatting differences were introduced
  by this task. Applied only these regions using bounded fileEditor replacements.
  Formatted the new `tests/unit/test_openai_access_enforcement.py` the same way.
- `chat_responses_adapter.py` was not edited: all five Black hunks match HEAD
  (`_MANTLE_SUPPORTED_INPUT_TYPES`, `_GPT5_EFFORT_CLAMP`, custom-tool parameters,
  custom-call dict comprehension, streaming `_finish_reason` argument). Added
  regions are already formatted. Its full-file Black check remains a baseline
  failure, not an open task-introduced formatting finding.
- `.venv/bin/black --check --diff` for router and new test: **passes**. Scoped
  `.venv/bin/ruff check --no-cache` on all three named files: **passes**, with only
  the existing top-level Ruff configuration deprecation warning.
- Compared ASTs and comments against in-memory pre-edit snapshots: unchanged.
  Adapter bytes unchanged; every changed nonblank router line came from a
  task-introduced region. Scoped `git diff --check`, including the untracked test
  via `--no-index /dev/null`, passes.
- Focused regression command with `PYTHONDONTWRITEBYTECODE=1` and
  `AWS_EC2_METADATA_DISABLED=true`: `.venv/bin/python -m pytest -o addopts=''
  -p no:cacheprovider tests/unit/test_openai_access_enforcement.py
  tests/unit/test_openai_passthrough tests/unit/test_openai_response_context_store.py
  -q` -> **267 passed in 56.39s**. Background task:
  `bg-a8ff1d87-c364-4190-a4ab-49664a1268d3` (succeeded).
- No semantic changes, installs, commits, deployments, or task completion. Full
  task-wide verification remains with the parent agent.


## Independent review correction: slice 3 only (2026-09-11)

Serial implementation under the existing approval. Read task PRD/design/records,
context specs, current routing/adapter/profile/tool/PTC source and tests first.
No spawning, commits, deployment, dependency installation, live AWS requests or
Docker execution. All edits used fileEditor. Passthrough/UI and their outstanding
review findings are explicitly left to later agents; task remains **in_progress**.

### Requirement-to-test checklist and changes

- [x] **Pricing vendor versus executable adapter.** Restricted cost/quality routing
  in `app/routing/engine.py` (current method name `_route_restricted`) now derives
  adapter names from registered providers supporting the price row's model; it
  never uses the row's `provider` vendor label as executable identity. When no
  supporting adapter is present, an allowed default-Bedrock target counts as
  permitted-but-unavailable (503), not 403. Unknown registered adapter names still
  fail the exact-target contract. Unrestricted route bodies are unchanged.
  `test_pricing_vendor_routes_registered_adapter` uses real `ProviderRegistry`,
  `BedrockProvider`, `_vendor_label` (`Anthropic`), Decimal pricing, both cost/quality,
  both HTTP stream modes and provider absence: **8 cases**. Available requests send
  A on the actual boto3 wire, never B; absence returns 503 before SSE/upstream.
  Adjusted the older mock-registry fixture to return a real-shaped adapter name,
  and removed its obsolete assumption that support lookup only sees permitted IDs
  (adapter identification now precedes adapter-aware policy evaluation; no keys or
  inference are acquired during that local support lookup).
- [x] **Pre-profile authorization.** `BedrockService.prepare_model` rejects before
  `_is_claude_model` / profile control-plane resolution when neither possible wire
  ID is allowed: mapped ID, plus original name only if old compatibility could
  send it (not the scoped Runtime branch). The final exact-target guard remains.
  Tests use the real `InferenceProfileResolver` with a denied control-plane mock:
  alias and direct inaccessible ARN, compat on/off, both HTTP stream modes -> 403,
  zero `get_inference_profile`, no inference. Model list still returns visible A
  and candidate selection skips the forbidden profile. Actual MockTransport wire
  tests preserve legacy original-name calls after resolving an allowed possible
  non-Claude profile; native resolution still denies if only the original alias
  was allowed. **12 cases** across these tests.
- [x] **Dynamic 20260209 real boundary.** Added **12 cases** for search/fetch x
  streaming/non-streaming x success/early-denial/late-denial. Real service objects
  use `tool_service.standalone_service._get_or_create_session` and nested sandbox
  `execute_bash`, not a nonexistent outer-service sandbox hook. Success executes
  two bash iterations plus final inference; remapping during iteration keeps A.
  Early denial opens no sandbox or upstream. Late target change after the second
  bash call produces a permission error with no forbidden third inference, no
  `message_delta`/`message_stop`, and closes the admitted sandbox exactly once.
- [x] **Recursive PTC replacement/current policy.** Added **16 cases** covering
  both stream modes, single/batch recursive calls, immediate recursive completion
  before another tool call, and saved native/Converse API. Uses real
  `resume_execution` wrapped by AsyncMock and actual async generators (`asend`),
  replaces stored state through real recursion, then creates new request facades.
  Saved original name/target/API survive replacement and remapped names; B-only
  policy denies before resume/asend with state intact, and A policy later resumes
  successfully on wire A via the saved API. A further **1 late-SSE case** changes
  the selected name within recursive finalization after message_start and proves
  permission error/no successful terminal event/no forbidden second wire call.
  These tests exposed no additional tool/PTC product bugs; those files were not
  edited. Existing synthetic/legacy tests are retained for their original scope.

Files edited in this correction: `app/routing/engine.py`,
`app/services/bedrock_service.py`, `tests/unit/test_model_access_enforcement.py`,
`.trellis/spec/backend/quality-guidelines.md`, and this implementation record.
Spec additions capture vendor/adapter distinction, early profile guard ordering,
and actual dynamic/PTC assertion boundaries; no API/config contract changed.

### Verification evidence

- Added **49 tests**; model-enforcement file now has **131 cases** (82 prior).
  Initial reproduction: `/tmp/policy-review3-repro.txt` -> **15 failed, 1 passed**:
  13 failures reproduce the vendor/profile issues; two were new test harness errors
  from double-wrapping an existing MockTransport. The first post-fix run also
  corrected the harness's expected error envelope (`detail.type` for its minimal
  FastAPI app without main exception handlers). No product contract was changed
  to satisfy those harness assumptions.
- Intermediate full model file: **126 passed** (`policy-review3-focused2.txt`).
  Additional direct-ARN + late-recursion edge run: **9 passed, 122 deselected**
  (`policy-review3-edges.txt`).
- Final focused/relevant command, prefixed with `PYTHONDONTWRITEBYTECODE=1
  AWS_EC2_METADATA_DISABLED=true`: `.venv/bin/python -m pytest -o addopts=''
  -p no:cacheprovider tests/unit/test_model_access_enforcement.py
  tests/unit/test_inference_profile_resolver.py
  tests/unit/test_bedrock_service_claude_detection.py
  tests/unit/test_bedrock_service_list_models.py
  tests/unit/test_bedrock_provider_cache.py
  tests/unit/test_bedrock_provider_billing.py
  tests/integration/test_multi_provider.py -q` -> **168 passed in 3.51s**.
  Log `/tmp/policy-review3-focused-final.txt`.
- Full measured selection (same prefix/options, `tests/unit
  tests/integration/test_multi_provider.py tests/integration/test_openai_passthrough`):
  **1248 passed, same 17 failed, 6 warnings in 89.60s**. Exact failing-name set
  compared with `/tmp/policy-slice4-recovery-tests-all-final3.txt`: no additions or
  removals. Log `/tmp/policy-review3-tests-all-final.txt`. A later engine line-wrap
  is AST-identical and included in the final focused run.
- Full Ruff **1524 -> 1524**, mypy **272 -> 272**, with identical normalized
  diagnostic multisets (file/code/message ignoring line movement). Logs:
  `/tmp/policy-review3-ruff-{before,final}.json` and
  `/tmp/policy-review3-mypy-{before,final}.txt`. These tools remain baseline-failing;
  a combined shell's final successful diff check is not a clean lint/type claim.
- New-test Black check and Ruff pass. Existing engine/BedrockService historical
  formatting was not broadly changed. `git diff --check` passes; AST comparison
  confirms unrestricted cost/quality/smart/budget methods match HEAD.
- No frontend/CDK build needed in this backend-only correction. No live ingress,
  real AWS control-plane permissions, Docker execution or production SDK behavior
  verified; mocked wire/control-plane and real in-process orchestration are the
  evidence. Remaining passthrough/UI findings and overall task release gates are
  still owned by the parent, not marked complete by these slice-3 results.



## Independent review correction: passthrough only (2026-09-11)

Approved serial implementation; no spawning, dependencies, commits or deployment.
Task remains in progress; frontend review and overall release gates remain separate.

Requirement-to-test checklist (before edits):
- Legacy native/Converse proxy-search with nonempty provider association must send
  through the historical default boto client; assert actual invocation and AUTH
  backend agree, including the scoped/native Runtime-selection setup.
- SSE field parsing must match installed OpenAI SSEDecoder (last event wins,
  single leading space removed, empty event reset). Check both named-event and
  payload-type interpretations for mismatches; registration precedes delivery,
  and failed writes withhold the entire frame on Responses and chat conversion.
- Restricted GET response stream=true must return the first upstream chunk before
  completion, preserving historical URL/query/auth and closing on finish/errors/
  disconnect. Retrieval must not record creation usage again. Unrestricted path
  stays unchanged.
- Request-local nested-search observations survive a later permission error in
  both requested stream modes. Successful multi-iteration aggregate and metadata
  failure record once, never twice. Existing proxy search resolves before SSE;
  denied requests must have no successful terminal event.
- Run focused and broad baseline selections; compare normalized Ruff/mypy
  diagnostics, Black-check new/previously clean touched files and diff whitespace.

### Changes and verified boundary behavior

- `router._legacy_search_registration` now passes `None` to ModelAccessService
  for both boto APIs, matching the default backend it records. Extended the
  existing regression from 2 to **12 cases**: native bare/scoped IDs and Converse,
  association absent/p1, both requested stream modes. Real BedrockService invokes
  the default mocked boto wire; provider-client creation/invocation stays zero;
  AUTH records that wire's endpoint, empty provider ID and exact model/API.
- `response_access.registered_sse_lines` now parses SSE fields in order, removes
  exactly one leading space, takes the last event and respects empty/bare resets.
  Checks response IDs using both event name and payload type on disagreement;
  Responses byte framing stays unchanged. **19 tests** use installed OpenAI
  SSEDecoder to establish the actual field interpretation, both Responses and
  chat conversion, registration and failed-write entire-frame withholding.
- Restricted streaming GET uses the shared `open_upstream_stream` with pinned
  resolved URL, current verified credentials and query string (preserving repeated
  keys). New `stream_retrieved_response` relays bytes incrementally and closes in
  finally, with BackgroundTask fallback; no usage callback and no new AUTH write.
  HTTP errors use the existing closed-response path; midstream read errors emit
  only a safe error, never DONE. Nonstream and unrestricted CRUD branch untouched.
  **8 AsyncByteStream cases** cover complete/read-error/consumer-close/HTTP-error
  with bearer and actual SigV4 signing assertions. Another **7 HTTP cases** prove
  denied states open no upstream, retrieval after creation does not rebill, and
  unrestricted/master query behavior is preserved.
- `web_search.SearchUsageAccess` is a request-owned ModelAccessService subclass,
  preserving type-sensitive preflight and target/provider memo. It snapshots input/
  output counts from each completed nested invoke. The failure-accounting context
  surrounds only orchestration, never successful aggregate/metadata processing.
  Existing two late-denial cases now require 9/3 recording. **6 real-loop cases**
  run WebSearchService + SDK MockTransport through two search iterations: success
  and metadata failure bill 27/9 once; late denial after two calls bills 18/6 once,
  never invokes B. Both stream flags tested and denied output has no successful
  terminal event. Proxy search intentionally still completes before SSE starts;
  no new real-time search streaming or changes to native message paths.
- Added executable contracts/tests to backend quality spec. No README/config/API
  payload changes needed: fixes restore already approved contracts. No UI/CDK
  edits/builds; all product/test edits were bounded fileEditor replacements.

### Verification

- **50 additional cases** in `test_openai_access_enforcement.py` (183 -> 233).
- Initial targeted reproduction: **29 failed, 8 passed**
  (`/tmp/policy-review4-repro.txt`). Failures include the four reported bugs and
  three reset-test harness assertions (SDK ServerSentEvent normalizes empty event
  to None). Retrieval reproduction first hit lost repeated query keys before its
  no-prefetch assertion; fixed streaming path preserves both.
- First post-fix targeted run: **38 passed, 5 failed**; remaining failures were the
  three harness reset expectations and two Converse fixtures whose bearer override
  legitimately enabled Responses. Corrected fixture to AK/SK/empty bearer so it
  actually exercises Converse; no production selection was relaxed. Added scoped
  native cases. Follow-up edge run: **21 passed, 205 deselected**.
- Final focused command: `PYTHONDONTWRITEBYTECODE=1 AWS_EC2_METADATA_DISABLED=true
  .venv/bin/python -m pytest -o addopts='' -p no:cacheprovider
  tests/unit/test_openai_access_enforcement.py tests/unit/test_openai_passthrough
  tests/unit/test_openai_response_context_store.py
  tests/unit/test_openai_responses_web_search.py -q`
  -> **342 passed in 72.07s** (`/tmp/policy-review4-focused-final.txt`).
- Final broad baseline selection (same prefix/options, `tests/unit
  tests/integration/test_multi_provider.py tests/integration/test_openai_passthrough`):
  **1298 passed, same 17 failed, 6 warnings in 104.98s**. Exact failing-test-name
  set matches `/tmp/policy-review3-tests-all-final.txt` with no additions/removals.
  Log `/tmp/policy-review4-tests-all-final.txt`; background task
  `bg-dfc9b4fb-007c-4eb4-87e1-f77270e60645` exits 1 for those baseline failures.
- Full Ruff **1524 -> 1524**, mypy **272 -> 272** with identical normalized
  file/code/message multisets, ignoring moved line numbers. Logs:
  `/tmp/policy-review4-ruff-{before,final}.json`,
  `/tmp/policy-review4-mypy-{before,final}.txt`. An intermediate mypy run caught
  four new annotations at the query/signature boundary; corrected without ignores.
- Scoped OpenAI package/new-test Ruff passes. Black passes for router,
  response_access, streaming, web_search and new test file. Every touched Python
  file is clean; pre-existing chat adapter formatting untouched. Logs:
  `/tmp/policy-review4-black-final.txt`. `git diff --check` passes.

### Caveats retained

Already observed nested usage no longer disappears on denial. This is distinct
from the pre-existing five-second metadata-outage drain: usage never reported by
upstream, or reported only after that bound/disconnect, remains unreconstructible.
Search observation follows its existing aggregate input/output accounting; this
correction does not redesign native cache/reasoning billing. Transport checks are
in-process MockTransport/AsyncByteStream and direct generator-close tests, not live
AWS/production disconnect timing. No paid calls, dependency changes, commit,
deployment or task completion. Parent still owns frontend review, integrated task
acceptance and production ingress/provider-rotation release checks.



## Independent review correction: frontend literal IDs (2026-09-11)

Approved frontend/docs-only correction; task remains **in_progress**. Read the
approved actual-model contract, frontend editor/validation/tests, browser harness,
and context specs before editing. No backend/native/passthrough changes authorized.

Requirement-to-test checklist (before edits):
- Permit manual known alias-looking literals (including `gpt-5.4`) with normal
  syntax/size validation; unit regression proves valid and exact JSON roundtrip.
- Show a nonblocking localized warning: entries are literal, only the exact runtime
  sent ID controls access, no automatic mapping of saved permissions. Browser tests
  assert warning plus successful POST/PUT and reopen unchanged even after remapping.
- Keep chooser as an explicit suggestion/target preview; selection alone never
  changes entries, Add exact target appends the target rather than the alias.
  Retain browser target payload assertions and existing disable/preservation tests.
- Sync both locales and README guidance; remove blanket alias-rejection language.
- Run dependency-free frontend suite (prior 47 tests), build, scoped lint, and
  existing en/zh browser harness with local intercepted APIs only. Record results;
  no dependencies, deployments, commits, or task completion.

### Changes / contract

- `src/utils/accessPolicy.ts`: `validatePolicy(policy)` checks literal syntax and
  bounds only; removed mapping/saved-model arguments and the `alias` error code.
  Known alias-looking values can be actual wire IDs (`gpt-5.4` on legacy compat).
  Catalogue membership must neither reject nor rewrite new or saved permissions.
- `src/components/AccessPolicyEditor.tsx`: matching alias names produce an amber,
  nonblocking `role=status` warning associated with the model textarea. It shows
  the mapping target but explicitly permits literal save and explains that only
  the exact runtime-sent ID is authorized, not its mapped target. The chooser
  remains a suggestion; only **Add exact target** appends its displayed target.
- `src/pages/ApiKeys.tsx`: calls the simplified validator; existing omission,
  full replacement, retained disabled lists and save-error behavior unchanged.
- `src/i18n/en.json`, `src/i18n/zh.json`, `README.md`, `README_ZH.md`: clarify exact
  runtime IDs, legacy `gpt-5.4`, chooser suggestions and no silent conversion.
  Removed obsolete blocking alias messages. This correction supersedes slice 5's
  historical alias-rejection behavior and its browser rejection expectation above.
- `tests/access-policy.test.mjs`: replaced rejection test with literal acceptance;
  added exact JSON/reopened-draft and saved-literal preservation tests (47 -> 49).
  `tests/access-policy.browser.mjs`: replaced rejection expectation and added real
  UI literal POST/reopen, untouched omission, remap + IP-edit PUT, and explicit
  target append/reopen checks, while retaining existing target-only chooser and
  validation/lifecycle checks.
- Spec review: existing `backend/quality-guidelines.md` already states that legacy
  OpenAI compatibility sends the original name and must not authorize the mapped
  ID instead. No new runtime contract/spec section needed; UI code comments,
  bilingual operator docs and regressions now enforce that existing invariant.

### Verification / evidence

- Frontend `node --test tests/access-policy.test.mjs`: **49 passed, 0 failed**.
  `/tmp/policy-review5-unit-tests.txt`.
- Frontend `npm run build`: **passed** (TypeScript + Vite). Existing stale
  Browserslist and large-chunk notices only. `/tmp/policy-review5-build.txt`.
- Installed ESLint run programmatically with in-memory recommended TypeScript and
  React Hooks rules against the three touched TS/TSX files and both test files:
  **0 errors, 0 warnings**. `/tmp/policy-review5-scoped-lint.txt`. No lint config
  added; the repository-wide ESLint 9 missing-flat-config baseline remains outside
  this correction and was not re-run or represented as passing.
- Browser skills loaded before execution. Existing installed Playwright Chromium
  against local built preview `127.0.0.1:4175`, every `/api/*` mocked, every other
  origin aborted: **en and zh pass**, **12 mock writes each**, **zero page errors**.
  Exact POST/PUT and reopened literals survive a changed catalogue target; selecting
  a mapping is inert, explicit Add appends rather than replaces the literal, and
  the separate target-only chooser still saves its target (not `friendly-alias`).
  Also retained enabled-empty/CIDR rejection, 422 detail handling, disable/retain/
  re-enable, cancellation, catalogue outage/manual entry, and 390px layout checks.
- Browser command (from frontend):
  `PLAYWRIGHT_MODULE=/home/ubuntu/.nvm/versions/node/v22.19.0/lib/node_modules/playwright/index.js POLICY_UI_ARTIFACTS=/tmp/policy-review5-ui node tests/access-policy.browser.mjs`.
  Log `/tmp/policy-review5-browser.txt`; results `/tmp/policy-review5-ui/results.json`.
  Screenshots include `{en,zh}-literal-editor.png`, `{en,zh}-literal-mobile.png`
  plus existing editor/table/mobile views. Inspected English desktop and Chinese
  mobile warning with imageViewer: wrapped/readable and distinct from validation
  errors. Local preview stopped after verification. Initial preview launch from
  repo root failed with missing package.json; corrected cwd, no product fix needed.
- `git diff --check` passes; untracked frontend source/test whitespace also checked.
  Read-only grep found no active alias-rejection message/code in frontend/READMEs;
  historical slice-5 records are retained and explicitly superseded here.

### Scope / remaining limits

Only frontend source/tests, bilingual README docs, and this record changed in this
correction. No backend/native/passthrough/wire behavior edits, installations,
production calls, deployment, commits or task completion. Browser mocks prove UI
payload/state, not live persistence or upstream acceptance. Exact runtime wire ID
remains authoritative; the chooser cannot determine every adapter mode. Full-task
acceptance and production ingress/provider verification remain with the parent.

## Final parent acceptance (2026-09-11)

All feature slices and independent review corrections are implemented. PRD AC1-AC8
are accepted against local mocked/integration/browser and infrastructure-synthesis
evidence; none of these claims substitute for the explicitly deferred production
source-IP/provider/disconnect smoke tests.

### Parent-executed final gates

- Python selection: `tests/unit tests/integration/test_multi_provider.py tests/integration/test_openai_passthrough`, `-o addopts='' -p no:cacheprovider`: **1298 passed, 17 failed, 6 warnings, 107.93s**. Parent compared exact `FAILED` name sets to the pre-change 725-pass/17-fail baseline: identical. This is 573 additional passing cases, not a clean whole-repository test claim. Final log is `bg-c08ad8b1-c57d-40b7-b84a-ca0541cf9824.log` in the session background directory recorded above.
- Frontend Node tests: **49 passed**; TypeScript/Vite build passed (existing notices only).
- CDK TypeScript build and Node topology tests: **4 passed**. Offline dev synthesis passed with the compiled app, `--no-lookups --no-staging`, output `/tmp/policy-final-cdk-synth`; no deployment.
- Ruff: **1524 existing findings**, mypy: **272 existing errors**; parent compared normalized diagnostic multisets to the review-correction snapshots: **0 new diagnostics**. Standard frontend lint remains the documented pre-existing ESLint configuration blocker; scoped TS/hooks lint is clean.
- Black check passed on all 15 new/previously-clean policy, helper, router, streaming and test files. Unrelated pre-existing formatting differences were retained. `git diff --check` passed.
- Final independent review of all reported corrections found **no remaining blocking/high/medium issues**; its focused verification passed 103 Python cases and 49 frontend cases. Earlier three review shards' concrete findings were fixed and regression-tested before this review.
- Parent inspected Chinese desktop/mobile policy screenshots and the corrected literal-ID mobile warning. Latest en/zh browser results in `/tmp/policy-review5-ui/results.json` show 12 mocked writes per locale, zero page errors. Browser automation was run by the frontend implementer, not rerun by parent.

### Delivery state and remaining release actions

- Code and task/spec changes are uncommitted; no dependencies installed, no paid inference, no production admin operations, no deployments and no history changes.
- `trellis-finish-work` was loaded and its dirty-task guard applies: do not archive or run journal auto-commits while this task's code is uncommitted. Task stays `in_progress` solely for later commit/archive lifecycle; implementation and local acceptance are complete. Unrelated pre-existing Trellis/user changes remain untouched.
- Before enabling restricted keys, deploy enforcement-capable code to every worker and validate real source-IP/forged-header behavior in the actual direct/ALB/CloudFront topology. Manual Uvicorn launch requires `--no-proxy-headers`; do not configure trust-all proxies. EC2 bridge peer preservation needs particular attention.
- Default authorization update window remains 60 seconds; existing admitted streams are not cancelled. Restricted legacy/expired/unverifiable Responses require a fresh request (existing attribution TTL defaults to one hour). Unrestricted Responses are not promised new tenant isolation. Unreported upstream usage beyond the bounded outage drain cannot be reconstructed.
