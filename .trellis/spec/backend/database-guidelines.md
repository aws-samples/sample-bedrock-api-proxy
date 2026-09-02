# Database Guidelines

> DynamoDB patterns and conventions for this project (proxy + admin portal).

---

## Overview

- Storage is DynamoDB via **sync boto3** (`app/db/dynamodb.py`). No ORM, no migrations:
  tables are created idempotently by `DynamoDBClient.create_tables()` (local/dev) and by CDK
  (`cdk/lib/dynamodb-stack.ts`) in AWS. Both definitions must agree.
- One `*Manager` class per table, constructed as `Manager(DynamoDBClient())`. There is no
  `DynamoDBClient.<name>_manager` property convention; callers instantiate the manager.
- Table names come from `Settings.dynamodb_*_table` (`app/core/config.py`), env-overridable.
- Numbers round-trip as `Decimal`; managers convert to `float`/`int` before returning dicts to
  pydantic schemas. Whole-number floats come back as `int` — schemas use `Optional[float]`.

---

## Scenario: Adding a new DynamoDB table

### 1. Scope / Trigger
- Trigger: any new table is infra wiring across 7 files in 3 layers; missing one produces a
  runtime `ResourceNotFoundException` in exactly one deployment mode (local vs ECS vs admin).
- Reference implementation: `anthropic-proxy-speed-tests` (task 09-02-admin-model-speed-test).

### 2. Signatures
```python
# app/core/config.py
dynamodb_<name>_table: str = Field(default="anthropic-proxy-<name>", description="...")   # env DYNAMODB_<NAME>_TABLE

# app/db/dynamodb.py
class DynamoDBClient:
    self.<name>_table_name = settings.dynamodb_<name>_table          # in __init__
    def create_tables(self): ... self._create_<name>_table()        # append
    def _create_<name>_table(self):                                 # KeySchema/AttributeDefinitions/PAY_PER_REQUEST,
        ...                                                         # swallow ResourceInUseException, then
        self.dynamodb.meta.client.update_time_to_live(...)          # only if the table has a TTL attribute

class <Name>Manager:
    def __init__(self, db_client: DynamoDBClient): self.table = db_client.dynamodb.Table(db_client.<name>_table_name)
```
```ts
// cdk/lib/dynamodb-stack.ts
public readonly <name>Table: dynamodb.Table;   // new Table(... partitionKey/sortKey, billingMode from config,
                                               // removalPolicy RETAIN, encryption AWS_MANAGED,
                                               // timeToLiveAttribute?: '<attr>'), tags loop, CfnOutput
// cdk/lib/ecs-stack.ts
props interface + destructure + `<name>Table.grantReadWriteData(taskRole)` +
`DYNAMODB_<NAME>_TABLE: <name>Table.tableName` in BOTH env maps (proxy container AND admin container)
+ pass through `createAdminPortalService(... tables: { <name>Table })` + admin-side grant
// cdk/bin/app.ts
<name>Table: dynamoDBStack.<name>Table,
```
Also: `scripts/setup_tables.py` (print the name), `env.example`, `CLAUDE.md` "DynamoDB Tables".

### 3. Contracts
- Env key: `DYNAMODB_<NAME>_TABLE` — same spelling in `config.py`, `env.example`, both CDK env
  maps, `CLAUDE.md`.
- Time fields: state the unit in the field name or doc. Convention from speed-tests:
  sort keys in **epoch ms** (`tested_at`), TTL attributes in **epoch seconds** (`expires_at`,
  DynamoDB TTL requires seconds).
- Newest-first history: `query(KeyConditionExpression=Key(pk).eq(x), ScanIndexForward=False, Limit=n)`.
- "Latest per key" for tens of keys: one `Limit=1` query per key, run with
  `asyncio.gather(asyncio.to_thread(...))`. Do not add a GSI for this volume.

### 4. Validation & Error Matrix
- Table missing in one layer → `ResourceNotFoundException` only in that deployment mode → check
  all 7 files, not just the one that failed.
- `update_time_to_live` on an existing table with TTL already enabled → `ValidationException`
  → wrap in try/except, ignore "already enabled".
- `Decimal` leaking into pydantic/JSON → `TypeError: Object of type Decimal is not JSON
  serializable` → convert in the manager, never in the route.

### 5. Good/Base/Bad Cases
- Good: `SpeedTestManager.put_result()` writes floats via `Decimal(str(x))`, reads back with a
  `_from_item()` converter; `tests/unit/test_speed_test_manager.py` asserts key schema + TTL.
- Base: table with PK only, no TTL → skip `update_time_to_live`, skip `timeToLiveAttribute`.
- Bad: adding the table to `dynamodb-stack.ts` and the proxy env map but not the admin env map →
  admin portal 500s while the proxy works.

### 6. Tests Required
- moto `mock_aws` test creating the table via `DynamoDBClient()._create_<name>_table()` and
  asserting `KeySchema`, `AttributeDefinitions`, and (if any) `TimeToLiveDescription.AttributeName`.
- Manager tests: put + ordered read with `Limit`; Decimal→float on read.
- `cd cdk && npm run build && npx cdk synth -c environment=dev` (env context name is
  `environment`, see `cdk/scripts/deploy.sh`); diff the template against a pre-change synth and
  confirm only the new table/exports/env/policy statements changed.

### 7. Wrong vs Correct
#### Wrong
```python
return response.get("Items", [])          # Decimals leak to the API layer
```
#### Correct
```python
return [self._from_item(i) for i in response.get("Items", [])]   # float/int/bool normalised here
```

---

## Query Patterns

- Key lookups by secondary attribute use an existing GSI when one exists
  (`ApiKeyManager.list_api_keys_for_user` → `user_id-index`); do not `scan` + filter.
- Caches of lookups (e.g. model mapping) are per-process with a TTL from settings; changes made
  in the admin portal take up to that TTL to appear in the proxy.

---

## Naming Conventions

- Table: `anthropic-proxy-<kebab-name>`; setting: `dynamodb_<snake_name>_table`;
  env: `DYNAMODB_<UPPER_SNAKE>_TABLE`; CDK field: `<camelName>Table`; CDK construct id `<PascalName>Table`.
- Keys: `snake_case`, the same string in Python, TypeScript types, and CDK key definitions.

---

## Common Mistakes

### Common Mistake: table wired for the proxy but not the admin portal
**Symptom**: proxy works; admin endpoint returns 500 `ResourceNotFoundException`.
**Cause**: `ecs-stack.ts` has two independent env maps and two task definitions.
**Fix / Prevention**: grep the new env key in `ecs-stack.ts` and expect ≥2 hits.

### Common Mistake: reordering CDK constructs to reach a later resource
**Symptom**: `this.distribution` undefined when building the admin env; moving
`createAdminPortalService` after the CloudFront block silently drops the admin listener rules
(they read `this.adminTargetGroup`).
**Fix**: reference late-created resources with `cdk.Lazy.string({ produce: () => ... })` instead of
moving construct creation; verify with a synth diff that no logical IDs changed.
