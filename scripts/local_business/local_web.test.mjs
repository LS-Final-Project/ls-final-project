import { test } from 'node:test';
import assert from 'node:assert/strict';
import { localHandler, validateSettings, requireLocalEnvironment } from './local_web.mjs';

const settings = { ports: { web: 38443, platform: 38083, cs: 38081, tft: 38082 },
  web_origin: 'https://localhost:38443' };
test('entrypoint requires explicit local and rejects even empty Cloud Run marker', () => {
  requireLocalEnvironment({ ENVIRONMENT: 'local' });
  for (const env of [{}, { ENVIRONMENT: 'production' }, { ENVIRONMENT: 'test' },
    { ENVIRONMENT: 'local', K_SERVICE: '' }, { ENVIRONMENT: 'local', K_SERVICE: 'web' }]) {
    assert.throws(() => requireLocalEnvironment(env), /LOCAL_WEB_FORBIDDEN_ENVIRONMENT/);
  }
});
function request(url, method = 'GET', host = 'localhost:38443') {
  const result = { status: null, body: '', delegated: false };
  localHandler(settings, () => { result.delegated = true; })(
    { url, method, headers: { host } },
    { writeHead: (status, headers) => Object.assign(result, { status, headers }),
      end: body => { result.body = body ?? ''; } },
  );
  return result;
}
test('fixture chooser is explicit, no model-OFF promise after enable-real', () => {
  const response = request(`/local/authorize?state=${'a'.repeat(43)}`);
  assert.equal(response.status, 200);
  assert.equal(response.headers['Cache-Control'], 'no-store');
  assert.match(response.body, /실제 Microsoft 로그인이 아닙니다/);
  assert.match(response.body, /enable-real/);
  assert.equal((response.body.match(/name="code"/g) ?? []).length, 4);
});
test('host, origin, state injection, duplicates and method rejected before relay', () => {
  for (const args of [
    ['/local/authorize?state=%22%3E'],
    [`/local/authorize?state=${'a'.repeat(43)}&state=b`],
    [`/local/authorize?state=${'a'.repeat(43)}`, 'POST'],
    ['/api/v1/platform/me', 'GET', 'external.invalid'],
    ['https://external.invalid/api/v1/platform/me'],
  ]) {
    const response = request(...args);
    assert.ok([400, 403].includes(response.status));
    assert.equal(response.delegated, false);
  }
});
test('all product paths including callback delegate unchanged', () => {
  assert.equal(request('/api/v1/platform/auth/callback', 'POST').delegated, true);
  assert.equal(request('/api/v1/platform/me').delegated, true);
});
test('settings cannot point relay to external hosts or overlapping ports', () => {
  assert.equal(validateSettings(settings), settings);
  assert.throws(() => validateSettings({ ...settings, web_origin: 'https://external.invalid' }));
  assert.throws(() => validateSettings({ ...settings, ports: { ...settings.ports, platform: 38443 } }));
});
