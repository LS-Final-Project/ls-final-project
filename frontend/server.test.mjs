import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { handler } from './server.mjs';

async function fixture(callback, fetcher) {
  const directory = await mkdtemp(join(tmpdir(), 'team4-web-'));
  await writeFile(join(directory, 'index.html'), '<h1>Fixture</h1>');
  const server = createServer(handler({ directory, upstream: 'https://platform.example.invalid', tokenProvider: async () => 'fixture-server-token', fetcher }));
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try { await callback(`http://127.0.0.1:${server.address().port}`); }
  finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(directory, { recursive: true }); }
}

test('serves SPA and health but blocks files and business/auth routes', async () => {
  await fixture(async origin => {
    assert.equal((await fetch(origin + '/workspace')).status, 200);
    assert.equal((await fetch(origin + '/health')).status, 200);
    for (const path of ['/missing.js', '/.env', '/api/v1/platform/me', '/api/v1/platform/dev/accounts', '/api/v1/cs/tickets']) assert.equal((await fetch(origin + path)).status, 404);
    assert.equal((await fetch(origin + '/api/v1/platform/ready', { method: 'POST' })).status, 405);
  });
});

test('adds server identity only to allowlisted fixed origin and never forwards browser credentials', async () => {
  const calls = [];
  await fixture(async origin => {
    const result = await fetch(origin + '/api/v1/platform/ready', { headers: { Authorization: 'Bearer untrusted', Cookie: 'private-cookie' } });
    assert.equal(result.status, 200);
    assert.equal((await result.json()).data.database, true);
    assert.equal((await fetch(origin + '/api/v1/platform/ready?target=https://evil.invalid')).status, 404);
  }, async (url, options) => {
    calls.push({ url: String(url), headers: options.headers });
    return new Response(JSON.stringify({ data: { database: true } }), { status: 200 });
  });
  assert.deepEqual(calls, [{ url: 'https://platform.example.invalid/api/v1/platform/ready', headers: { Authorization: 'Bearer fixture-server-token' } }]);
});

test('does not hide upstream failures as static HTML or disclose provider errors', async () => {
  await fixture(async origin => assert.equal((await fetch(origin + '/api/v1/platform/ready')).status, 503), async () => new Response('{}', { status: 503 }));
  await fixture(async origin => {
    const result = await fetch(origin + '/api/v1/platform/ready');
    assert.equal(result.status, 502);
    assert.ok(!(await result.text()).includes('fixture-secret'));
  }, async () => { throw new Error('fixture-secret'); });
});
