// No additional test dependencies: run `node --test tests/access-policy.test.mjs`.
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import ts from 'typescript';

async function loadUtility(name) {
  const source = readFileSync(new URL(`../src/utils/${name}.ts`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
}
const { policyDraft, draftPolicy, validIPRule, validatePolicy, MAX_POLICY_BYTES } = await loadUtility('accessPolicy');
const { apiErrorMessage } = await loadUtility('apiErrors');
const empty = () => draftPolicy(policyDraft());
const mapping = [{ anthropic_model_id: 'alias', bedrock_model_id: 'us.actual-model:1', source: 'default' }];

for (const ip of ['0.0.0.0/0', '203.0.113.8', '203.0.113.8/32', '203.0.113.0/24', '::', '::/0', '::1/128', '2001:db8::/32', '2001:db8:1:2:3:4:5:6', '::ffff:192.0.2.0/120', '::ffff:192.0.2.1']) {
  test(`valid IP ${ip}`, () => assert.equal(validIPRule(ip), true));
}
for (const ip of ['203.0.113.8/24', '2001:db8::1/64', '::ffff:192.0.2.1/120', '1.2.3.4/33', '::/129', '::/-1', '1.2.3.4/255.255.255.0', '1.2.3.4:80', '[::1]', 'fe80::1%eth0', 'example.com', '1.2.3', '01.2.3.4', '256.1.2.3', '2001::db8::1', '1:2:3:4:5:6:7', '1:2:3:4:5:6:7:8:9', ':1:2:3:4:5:6:7', '1:2:3:4:5:6:7:', '::1/64/64', '::g', ':::']) {
  test(`invalid IP ${ip}`, () => assert.equal(validIPRule(ip), false));
}

test('legacy draft and explicit disabled object, never null', () => {
  assert.deepEqual(empty(), { version: 1, ip: { enabled: false, allow: [] }, model: { enabled: false, allow: [] } });
  assert.equal(validatePolicy(empty()), undefined);
});
test('canonical saved values round-trip exactly without resolving aliases', () => {
  const policy = { version: 1, ip: { enabled: false, allow: ['203.0.113.8/32', '2001:db8::/48'] }, model: { enabled: true, allow: ['us.actual-model:1', 'arn:aws:bedrock:us-east-1:123456789012:inference-profile/test'] } };
  assert.deepEqual(draftPolicy(policyDraft(policy)), policy);
});
test('disabling retains lists; editing IP does not mutate saved models', () => {
  const policy = { version: 1, ip: { enabled: true, allow: ['::1/128'] }, model: { enabled: true, allow: ['alias'] } };
  const draft = policyDraft(policy);
  draft.modelEnabled = false;
  draft.ipEnabled = false;
  const result = draftPolicy(draft);
  assert.deepEqual(result.model, { enabled: false, allow: ['alias'] });
  assert.equal(validatePolicy(result, mapping, policy.model.allow), undefined);
  assert.equal(policy.model.enabled, true);
});
test('trim user line edges, skip blank lines, preserve order and case', () => {
  const draft = policyDraft();
  draft.ipText = ' 203.0.113.8 \r\n\n 2001:db8::/48 ';
  draft.modelText = ' ActualModel\nactualmodel \n';
  const policy = draftPolicy(draft);
  assert.deepEqual(policy.ip.allow, ['203.0.113.8', '2001:db8::/48']);
  assert.deepEqual(policy.model.allow, ['ActualModel', 'actualmodel']);
});
test('independent enabled-empty errors', () => {
  let policy = empty();
  policy.ip.enabled = true;
  assert.equal(validatePolicy(policy).code, 'emptyIP');
  policy = empty();
  policy.model.enabled = true;
  assert.equal(validatePolicy(policy).code, 'emptyModel');
});
test('100 entries accepted, 101 rejected before deduplication even when disabled', () => {
  for (const dimension of ['ip', 'model']) {
    const policy = empty();
    policy[dimension].allow = Array(100).fill(dimension === 'ip' ? '::1' : 'exact-model');
    assert.equal(validatePolicy(policy), undefined);
    policy[dimension].allow.push(policy[dimension].allow[0]);
    assert.equal(validatePolicy(policy).code, 'tooMany');
  }
});
test('2048 Unicode characters accepted, 2049 rejected', () => {
  const policy = empty();
  policy.model.allow = ['😀'.repeat(2048)];
  assert.equal(validatePolicy(policy), undefined);
  policy.model.allow = ['😀'.repeat(2049)];
  assert.equal(validatePolicy(policy).code, 'invalidModel');
});
test('reject spaces, controls and invalid disabled IP entries', () => {
  for (const entry of ['model name', 'model\tname', 'model\u0000', 'model\u007f']) {
    const policy = empty();
    policy.model.allow = [entry];
    assert.equal(validatePolicy(policy).code, 'invalidModel');
  }
  const policy = empty();
  policy.ip.allow = ['203.0.113.8/24'];
  assert.equal(validatePolicy(policy).code, 'invalidIP');
  assert.equal(policy.ip.allow[0], '203.0.113.8/24');
});
test('64 KiB is UTF-8 byte bound, not character count', () => {
  const policy = empty();
  policy.model.allow = Array.from({ length: 10 }, (_, i) => `${i}${'😀'.repeat(2047)}`);
  assert.ok(new TextEncoder().encode(JSON.stringify(policy)).length > MAX_POLICY_BYTES);
  assert.equal(validatePolicy(policy).code, 'tooLarge');
});
test('known alias-looking literal and suggested target are both valid exact IDs', () => {
  const policy = empty();
  policy.model.enabled = true;
  // Pass catalogue data too: it must not influence literal validation, even if
  // callers supply it (the validator no longer needs any mapping arguments).
  for (const entry of ['alias', 'gpt-5.4', 'us.actual-model:1']) {
    policy.model.allow = [entry];
    const catalogue = [...mapping, { anthropic_model_id: 'gpt-5.4', bedrock_model_id: 'openai.gpt-5.4', source: 'default' }];
    assert.equal(validatePolicy(policy, catalogue), undefined);
    assert.deepEqual(policy.model.allow, [entry]);
  }
});
test('new gpt-5.4 literal survives exact JSON payload and reopened draft', () => {
  const draft = policyDraft();
  draft.modelEnabled = true;
  draft.modelText = 'gpt-5.4';
  const policy = draftPolicy(draft);
  assert.equal(validatePolicy(policy), undefined);
  const expected = { version: 1, ip: { enabled: false, allow: [] }, model: { enabled: true, allow: ['gpt-5.4'] } };
  assert.deepEqual(policy, expected);
  assert.deepEqual(draftPolicy(policyDraft(JSON.parse(JSON.stringify(policy)))), expected);
});
test('editing another dimension retains saved literal IDs after catalogue remapping', () => {
  const policy = empty();
  policy.model = { enabled: true, allow: ['gpt-5.4', 'alias'] };
  const before = JSON.stringify(policy);
  const draft = policyDraft(policy);
  draft.ipEnabled = true;
  draft.ipText = '203.0.113.8/32';
  const edited = draftPolicy(draft);
  assert.equal(validatePolicy(edited, [{ anthropic_model_id: 'gpt-5.4', bedrock_model_id: 'different-target', source: 'custom' }]), undefined);
  assert.deepEqual(edited.model, policy.model);
  assert.equal(JSON.stringify(policy), before);
});
test('literal target also present as an alias is not reinterpreted', () => {
  const policy = empty();
  policy.model.allow = ['us.actual-model:1'];
  assert.equal(validatePolicy(policy, [...mapping, { anthropic_model_id: 'us.actual-model:1', bedrock_model_id: 'other', source: 'custom' }]), undefined);
});
test('manual exact targets work when catalogue unavailable', () => {
  const policy = empty();
  policy.model.allow = ['arn:aws:bedrock:us-east-1:123456789012:inference-profile/example'];
  assert.equal(validatePolicy(policy), undefined);
});
test('FastAPI detail arrays render locations/messages, never raw inputs or contexts', () => {
  assert.equal(apiErrorMessage([{ loc: ['body', 'access_policy', 'ip', 'allow', 0], msg: 'Host bits are not allowed', input: 'SECRET', ctx: { value: 'SECRET' } }], 'fallback'), 'body.access_policy.ip.allow.0: Host bits are not allowed');
  assert.equal(apiErrorMessage('plain error', 'fallback'), 'plain error');
  assert.equal(apiErrorMessage({ unexpected: true }, 'fallback'), 'fallback');
});
test('all policy labels and validation codes have English/Chinese parity', () => {
  const read = (lang) => JSON.parse(readFileSync(new URL(`../src/i18n/${lang}.json`, import.meta.url), 'utf8')).apiKeys.policy;
  const en = read('en');
  const zh = read('zh');
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort());
  assert.deepEqual(Object.keys(en.errors).sort(), Object.keys(zh.errors).sort());
  for (const value of [...Object.values(en), ...Object.values(zh)].filter((v) => typeof v === 'string')) assert.ok(value.length);
});
