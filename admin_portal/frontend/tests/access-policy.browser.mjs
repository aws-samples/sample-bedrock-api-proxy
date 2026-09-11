// Optional local UI check using an already-installed Playwright (no install).
// Start `npm run preview -- --host 127.0.0.1 --port 4175 --strictPort` after build.
// PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.js node tests/access-policy.browser.mjs
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = 'http://127.0.0.1:4175';
const artifacts = process.env.POLICY_UI_ARTIFACTS || '/tmp/policy-slice5-ui';
mkdirSync(artifacts, { recursive: true });
const browser = await chromium.launch({ headless: true });
const target = 'us.actual-model:1';
const manual = 'arn:aws:bedrock:us-east-1:123456789012:inference-profile/manual';
const results = [];

try {
  for (const lang of ['en', 'zh']) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
    await context.addInitScript((language) => localStorage.setItem('language', language), lang);
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    let failNextSave = false;
    let mappingsFail = false;
    let literalTarget = 'openai.gpt-5.4';
    const literal = 'gpt-5.4';
    const writes = [];
    const keys = [{ api_key: 'mock-local-legacy', user_id: 'local-user', name: 'Legacy', owner_name: 'Local owner', created_at: 100, is_active: true, monthly_budget: 42, rate_limit: 77, service_tier: 'flex', cache_ttl: '1h', routing_strategy: 'off', compression_strategy: 'off' }];
    await context.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort(); // No production/auth/font endpoints.
      if (!url.pathname.startsWith('/api/')) return route.continue();
      const json = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
      if (url.pathname === '/api/auth/config') return json(null);
      if (url.pathname === '/api/model-mapping') {
        if (mappingsFail) return json({ detail: 'Local catalogue unavailable' }, 503);
        return json({ items: [
          { anthropic_model_id: 'friendly-alias', bedrock_model_id: target, source: 'default' },
          { anthropic_model_id: literal, bedrock_model_id: literalTarget, source: 'default' },
        ], count: 2 });
      }
      if (url.pathname === '/api/providers') return json({ items: [], count: 0 });
      if (url.pathname.startsWith('/api/dashboard')) return json({ total_api_keys: keys.length, active_api_keys: keys.length, revoked_api_keys: 0, total_budget: 42, total_budget_used: 0, total_models: 1, active_models: 1, system_status: 'operational', new_keys_this_week: 0, models_without_pricing: [] });
      if (url.pathname.startsWith('/api/keys')) {
        const method = route.request().method();
        if (method === 'GET') return json({ items: keys, count: keys.length });
        const body = route.request().postDataJSON();
        writes.push({ method, body });
        if (failNextSave) {
          failNextSave = false;
          return json({ detail: [{ loc: ['body', 'access_policy', 'model', 'allow', 0], msg: 'Strict backend validation example', input: 'DO_NOT_RENDER_INPUT', ctx: { secret: 'DO_NOT_RENDER_CTX' } }] }, 422);
        }
        if (method === 'POST') {
          const created = { ...body, api_key: `mock-local-${keys.length}`, created_at: 100, is_active: true };
          keys.push(created);
          return json(created, 201);
        }
        if (method === 'PUT') {
          const key = keys.find((item) => item.api_key === decodeURIComponent(url.pathname.split('/').pop()));
          assert.ok(key);
          Object.assign(key, body);
          return json(key);
        }
      }
      throw new Error(`Unexpected mock API: ${url.pathname}`);
    });
    const createLabel = lang === 'en' ? 'Create New Key' : '创建新密钥';
    const saveLabel = lang === 'en' ? 'Save' : '保存';
    const cancelLabel = lang === 'en' ? 'Cancel' : '取消';
    const ipLabel = lang === 'en' ? 'Restrict source IPs' : '限制来源 IP';
    const modelLabel = lang === 'en' ? 'Restrict actual models' : '限制实际模型';
    const editLabel = lang === 'en' ? 'Edit Limits' : '编辑限制';
    const addLabel = lang === 'en' ? 'Add exact target' : '添加精确目标';
    const form = page.locator('form');
    const save = () => form.getByRole('button', { name: saveLabel, exact: true }).click();
    const cancel = () => form.getByRole('button', { name: cancelLabel, exact: true }).click();
    const openEdit = (name) => page.getByRole('row').filter({ has: page.getByText(name, { exact: true }) }).getByTitle(editLabel).click();
    const waitClosed = () => form.waitFor({ state: 'detached' });
    await page.goto(`${origin}/admin/api-keys`);
    await page.getByText('Legacy', { exact: true }).waitFor();

    // An unrelated legacy edit must omit policy and retain existing settings.
    await openEdit('Legacy');
    assert.equal(await form.getByRole('switch', { name: ipLabel }).isChecked(), false);
    await form.locator('input[type=text]').nth(0).fill('Legacy renamed');
    await save();
    await waitClosed();
    assert.ok(!Object.hasOwn(writes.at(-1).body, 'access_policy'));
    for (const field of ['monthly_budget', 'rate_limit', 'service_tier', 'cache_ttl']) {
      assert.equal(writes.at(-1).body[field], { monthly_budget: 42, rate_limit: 77, service_tier: 'flex', cache_ttl: '1h' }[field]);
    }

    // Two independent keys: first IP-only, then model-only.
    await page.getByRole('button', { name: createLabel }).click();
    await form.locator('input[type=text]').nth(0).fill('local-test');
    await form.locator('input[type=text]').nth(1).fill('IP only');
    await form.getByRole('switch', { name: ipLabel }).check();
    const beforeValidation = writes.length;
    await save();
    await form.getByRole('alert').waitFor();
    assert.equal(writes.length, beforeValidation);
    await page.locator('#policy-ip-list').fill('203.0.113.8/24');
    await save();
    assert.match(await form.getByRole('alert').innerText(), /203.0.113.8\/24/);
    assert.equal(await page.locator('#policy-ip-list').inputValue(), '203.0.113.8/24');
    assert.equal(writes.length, beforeValidation);
    await page.locator('#policy-ip-list').fill(' 203.0.113.8/32 \n2001:db8::/48');
    await save();
    await waitClosed();
    assert.deepEqual(writes.at(-1).body.access_policy, { version: 1, ip: { enabled: true, allow: ['203.0.113.8/32', '2001:db8::/48'] }, model: { enabled: false, allow: [] } });

    // Known alias-looking IDs can be the literal wire value in legacy compat.
    await page.getByRole('button', { name: createLabel }).click();
    await form.locator('input[type=text]').nth(0).fill('local-test');
    await form.locator('input[type=text]').nth(1).fill('Literal model');
    await form.getByRole('switch', { name: modelLabel }).check();
    await page.locator('#policy-model-list').fill(literal);
    const warning = page.locator('#policy-model-alias-warning');
    await warning.waitFor();
    assert.equal(await warning.getAttribute('role'), 'status');
    assert.ok((await warning.innerText()).includes(literalTarget));
    assert.match(await warning.innerText(), lang === 'en' ? /save gpt-5\.4 literally.*exact ID.*runtime/ : /按字面值保存 gpt-5\.4.*运行时.*精确 ID/);
    assert.equal(await form.getByRole('alert').count(), 0);
    // Merely selecting even this literal's mapping must not convert the entry.
    await page.locator('#policy-model-mapping').selectOption(literal);
    assert.equal(await form.locator('code').innerText(), literalTarget);
    assert.equal(await page.locator('#policy-model-list').inputValue(), literal);
    await page.setViewportSize({ width: 1440, height: 2400 });
    await form.locator('fieldset').screenshot({ path: `${artifacts}/${lang}-literal-editor.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await warning.scrollIntoViewIfNeeded();
    const warningBounds = await warning.boundingBox();
    assert.ok(warningBounds.x >= 0 && warningBounds.x + warningBounds.width <= 390);
    await page.screenshot({ path: `${artifacts}/${lang}-literal-mobile.png` });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await save();
    await waitClosed();
    const literalPolicy = { version: 1, ip: { enabled: false, allow: [] }, model: { enabled: true, allow: [literal] } };
    assert.equal(writes.at(-1).method, 'POST');
    assert.deepEqual(writes.at(-1).body.access_policy, literalPolicy);
    await openEdit('Literal model');
    assert.equal(await page.locator('#policy-model-list').inputValue(), literal);
    await warning.waitFor();
    await save();
    await waitClosed();
    assert.ok(!Object.hasOwn(writes.at(-1).body, 'access_policy'));

    // A refreshed mapping cannot rewrite or invalidate already saved literals.
    literalTarget = 'openai.remapped-target';
    await page.reload();
    await openEdit('Literal model');
    await warning.filter({ hasText: literalTarget }).waitFor();
    assert.equal(await page.locator('#policy-model-list').inputValue(), literal);
    await page.locator('#policy-ip-list').fill('203.0.113.8/32');
    await form.getByRole('switch', { name: ipLabel }).check();
    await save();
    await waitClosed();
    assert.equal(writes.at(-1).method, 'PUT');
    assert.deepEqual(writes.at(-1).body.access_policy, { ...literalPolicy, ip: { enabled: true, allow: ['203.0.113.8/32'] } });
    await openEdit('Literal model');
    assert.equal(await page.locator('#policy-model-list').inputValue(), literal);
    // Explicit Add appends the suggested target without replacing the literal.
    await page.locator('#policy-model-mapping').selectOption(literal);
    assert.equal(await form.locator('code').innerText(), literalTarget);
    assert.equal(await page.locator('#policy-model-list').inputValue(), literal);
    await form.getByRole('button', { name: addLabel, exact: true }).click();
    assert.equal(await page.locator('#policy-model-list').inputValue(), `${literal}\n${literalTarget}`);
    await save();
    await waitClosed();
    assert.deepEqual(writes.at(-1).body.access_policy.model.allow, [literal, literalTarget]);
    await openEdit('Literal model');
    assert.equal(await page.locator('#policy-model-list').inputValue(), `${literal}\n${literalTarget}`);
    await cancel();

    await page.getByRole('button', { name: createLabel }).click();
    await form.locator('input[type=text]').nth(0).fill('local-test');
    await form.locator('input[type=text]').nth(1).fill('Model only');
    await form.getByRole('switch', { name: modelLabel }).check();
    await save();
    await form.getByRole('alert').waitFor();
    await page.locator('#policy-model-mapping').selectOption('friendly-alias');
    assert.equal(await form.locator('code').innerText(), target);
    assert.equal(await page.locator('#policy-model-list').inputValue(), '');
    await form.getByRole('button', { name: addLabel, exact: true }).click();
    assert.equal(await page.locator('#policy-model-list').inputValue(), target);
    await page.locator('#policy-model-list').fill(`${target}\n${manual}`);
    failNextSave = true;
    await save();
    await form.getByText('Strict backend validation example', { exact: false }).waitFor();
    assert.doesNotMatch(await form.innerText(), /DO_NOT_RENDER|\[object Object\]/);
    assert.equal(await page.locator('#policy-model-list').inputValue(), `${target}\n${manual}`);
    await page.setViewportSize({ width: 1440, height: 2200 });
    await form.locator('fieldset').screenshot({ path: `${artifacts}/${lang}-editor.png` });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await save();
    await waitClosed();
    const modelPolicy = { version: 1, ip: { enabled: false, allow: [] }, model: { enabled: true, allow: [target, manual] } };
    assert.deepEqual(writes.at(-1).body.access_policy, modelPolicy);
    assert.ok(!writes.at(-1).body.access_policy.model.allow.includes('friendly-alias'));
    await page.getByText('Model only', { exact: true }).waitFor();
    await page.locator('table').evaluate((table) => { table.parentElement.scrollLeft = 0; });
    const ipRow = page.getByRole('row').filter({ has: page.getByText('IP only', { exact: true }) });
    const modelRow = page.getByRole('row').filter({ has: page.getByText('Model only', { exact: true }) });
    assert.match(await ipRow.innerText(), lang === 'en' ? /IP restricted \(2\)/ : /IP 受限（2 项）/);
    assert.match(await modelRow.innerText(), lang === 'en' ? /Models restricted \(2\)/ : /模型受限（2 项）/);
    await page.screenshot({ path: `${artifacts}/${lang}-table.png`, fullPage: true });

    // Reopen exact saved lists; explicitly disable without clearing, then re-enable.
    await openEdit('Model only');
    assert.equal(await page.locator('#policy-model-list').inputValue(), `${target}\n${manual}`);
    assert.equal(await form.getByRole('switch', { name: modelLabel }).isChecked(), true);
    await form.getByRole('switch', { name: modelLabel }).uncheck();
    await save();
    await waitClosed();
    assert.deepEqual(writes.at(-1).body.access_policy, { ...modelPolicy, model: { ...modelPolicy.model, enabled: false } });
    await openEdit('Model only');
    assert.equal(await page.locator('#policy-model-list').inputValue(), `${target}\n${manual}`);
    assert.equal(await form.getByRole('switch', { name: modelLabel }).isChecked(), false);
    await form.getByRole('switch', { name: modelLabel }).check();
    await save();
    await waitClosed();
    assert.deepEqual(writes.at(-1).body.access_policy, modelPolicy);

    await openEdit('IP only');
    assert.equal(await page.locator('#policy-ip-list').inputValue(), '203.0.113.8/32\n2001:db8::/48');
    assert.equal(await form.getByRole('switch', { name: modelLabel }).isChecked(), false);
    await page.locator('#policy-model-list').fill('discard-unsaved');
    await cancel();
    await openEdit('IP only');
    assert.equal(await page.locator('#policy-model-list').inputValue(), '');
    await save();
    await waitClosed();
    assert.ok(!Object.hasOwn(writes.at(-1).body, 'access_policy'));

    // Catalogue outage cannot prevent editing unrelated fields / manual exact IDs.
    mappingsFail = true;
    await page.reload();
    await openEdit('Model only');
    await form.getByText(lang === 'en' ? 'Mappings unavailable.' : '无法加载模型映射', { exact: false }).waitFor();
    await page.locator('#policy-model-list').fill(manual);
    await save();
    await waitClosed();
    assert.deepEqual(writes.at(-1).body.access_policy.model.allow, [manual]);
    assert.equal(keys.find((key) => key.name === 'IP only').access_policy.ip.enabled, true);

    // Narrow viewport: fields and controls remain inside the modal.
    await page.setViewportSize({ width: 390, height: 844 });
    await openEdit('Model only');
    const bounds = await page.locator('#policy-model-list').boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    await form.getByText(lang === 'en' ? 'Mappings unavailable.' : '无法加载模型映射', { exact: false }).waitFor();
    await page.locator('#policy-model-list').scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${artifacts}/${lang}-mobile-editor.png` });
    await form.getByRole('button', { name: saveLabel, exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${artifacts}/${lang}-mobile-notices.png` });
    await cancel();
    assert.deepEqual(pageErrors, []);
    results.push({ lang, writes: writes.length, checks: 'legacy omission; independent create; empty/CIDR rejection; literal gpt-5.4 warning/save/reopen/remap; chooser selection inert/explicit target append; exact POST/PUT payloads; 422 detail; reopen; disable/retain/re-enable; cancel; mapping outage/manual; narrow layout', pageErrors });
    await context.close();
  }
  writeFileSync(`${artifacts}/results.json`, JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
} finally {
  await browser.close();
}
