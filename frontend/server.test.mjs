import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, writeFile, rm, symlink, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { handler } from './server.mjs';

/**
 * 임시 정적 파일과 로컬 HTTP 서버로 웹 중계 경계를 검사한다.
 * Args: callback은 요청 검사, fetcher는 upstream 대체, businessRelayEnabled는 명시 활성화 여부다.
 * Returns: 검사와 임시 서버·파일 정리를 마친 Promise.
 */
async function fixture(callback, fetcher, businessRelayEnabled = false) {
  const directory = await mkdtemp(join(tmpdir(), 'team4-web-'));
  await writeFile(join(directory, 'index.html'), '<h1>Fixture</h1>');
  const server = createServer(handler({ directory, upstream: 'https://platform.example.invalid', tokenProvider: async () => 'fixture-server-token', fetcher, businessRelayEnabled }));
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try { await callback(`http://127.0.0.1:${server.address().port}`); }
  finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(directory, { recursive: true }); }
}

test('serves SPA and health but blocks files, business, development and unknown auth routes', async () => {
  await fixture(async origin => {
    assert.equal((await fetch(origin + '/workspace')).status, 200);
    assert.equal((await fetch(origin + '/health')).status, 200);
    for (const path of ['/missing.js', '/.env', '/api/v1/platform/auth/unknown', '/api/v1/platform/dev/accounts', '/api/v1/cs/tickets']) assert.equal((await fetch(origin + path)).status, 404);
    assert.equal((await fetch(origin + '/api/v1/platform/ready', { method: 'POST' })).status, 405);
  });
});

test('proxies auth redirect and all cookies without following redirect or forwarding browser authorization', async () => {
  const calls = [];
  await fixture(async origin => {
    const response = await fetch(origin + '/api/v1/platform/auth/login', {
      redirect: 'manual', headers: { Authorization: 'Bearer browser-secret', Cookie: '__Host-team4_session=old; unrelated=private' },
    });
    assert.equal(response.status, 302);
    assert.equal(response.headers.get('location'), 'https://login.microsoftonline.com/consumers/authorize');
    assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.getSetCookie().length, 2);
  }, async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(null, { status: 302, headers: [
      ['Location', 'https://login.microsoftonline.com/consumers/authorize'],
      ['Set-Cookie', '__Host-team4_oauth=binding; Secure; HttpOnly; Path=/; SameSite=None'],
      ['Set-Cookie', '__Host-team4_session=old; Secure; HttpOnly; Path=/; SameSite=Lax'],
    ] });
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://platform.example.invalid/api/v1/platform/auth/login');
  assert.equal(calls[0].options.redirect, 'manual');
  assert.deepEqual(calls[0].options.headers, { Authorization: 'Bearer fixture-server-token', Cookie: '__Host-team4_session=old' });
});

test('forwards callback form as body and preserves fixed callback redirect', async () => {
  await fixture(async origin => {
    const response = await fetch(origin + '/api/v1/platform/auth/callback', {
      method: 'POST', redirect: 'manual', body: 'state=one&state=two&code=synthetic-code',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', Cookie: '__Host-team4_oauth=binding' },
    });
    assert.equal(response.status, 303);
    assert.equal(response.headers.get('location'), 'https://app.example.invalid/');
  }, async (url, options) => {
    assert.equal(String(url), 'https://platform.example.invalid/api/v1/platform/auth/callback');
    assert.equal(options.method, 'POST');
    assert.equal(options.body.toString(), 'state=one&state=two&code=synthetic-code');
    assert.equal(options.headers.Cookie, '__Host-team4_oauth=binding');
    return new Response(null, { status: 303, headers: { Location: 'https://app.example.invalid/' } });
  });
});

test('forwards me csrf response and logout origin/csrf while preserving session deletion', async () => {
  const calls = [];
  await fixture(async origin => {
    const me = await fetch(origin + '/api/v1/platform/me');
    assert.equal(me.headers.get('x-csrf-token'), 'bound-csrf');
    const logout = await fetch(origin + '/api/v1/platform/auth/logout', { method: 'POST', headers: {
      Origin: origin, 'X-CSRF-Token': 'bound-csrf', Cookie: '__Host-team4_session=session',
    } });
    assert.equal(logout.status, 204);
    assert.match(logout.headers.get('set-cookie'), /Max-Age=0/);
  }, async (url, options) => {
    calls.push(options);
    return String(url).endsWith('/me')
      ? new Response('{}', { headers: { 'X-CSRF-Token': 'bound-csrf' } })
      : new Response(null, { status: 204, headers: { 'Set-Cookie': '__Host-team4_session=; Max-Age=0; Secure; HttpOnly; Path=/' } });
  });
  assert.equal(calls[1].headers['X-CSRF-Token'], 'bound-csrf');
  assert.ok(calls[1].headers.Origin.startsWith('http://127.0.0.1:'));
});

test('rejects auth query strings, wrong methods and oversized or non-form callbacks before forwarding', async () => {
  let calls = 0;
  await fixture(async origin => {
    assert.equal((await fetch(origin + '/api/v1/platform/auth/login?return_url=https://evil.invalid')).status, 404);
    assert.equal((await fetch(origin + '/api/v1/platform/auth/callback?code=private')).status, 404);
    assert.equal((await fetch(origin + '/api/v1/platform/auth/callback')).status, 405);
    assert.equal((await fetch(origin + '/api/v1/platform/auth/logout')).status, 405);
    assert.equal((await fetch(origin + '/api/v1/platform/auth/callback', { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' } })).status, 400);
    assert.equal((await fetch(origin + '/api/v1/platform/auth/callback', { method: 'POST', body: 'code=' + 'x'.repeat(9000), headers: { 'Content-Type': 'application/x-www-form-urlencoded' } })).status, 413);
  }, async () => { calls++; return new Response('{}'); });
  assert.equal(calls, 0);
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

test('opt-in business relay keeps JSON, version and pagination but replaces browser credentials', async () => {
  const calls = [];
  await fixture(async origin => {
    const headers = {
      Cookie: '__Host-team4_session=session; __Host-team4_oauth=binding; unrelated=private',
      Authorization: 'Bearer browser-secret', 'X-Team4-Principal': '{"roles":["manager"]}',
      'X-Team4-Caller-Token': 'forged', Origin: 'https://app.example.invalid',
      'X-CSRF-Token': 'bound-csrf', 'If-Match': '"3"', 'Idempotency-Key': 'synthetic-key',
      'Content-Type': 'application/json',
    };
    const result = await fetch(origin + '/api/v1/cs/tickets', { method: 'POST', headers, body: '{"subject":"synthetic"}' });
    assert.equal(result.status, 409);
    assert.equal(result.headers.get('etag'), '"4"');
    assert.equal(result.headers.get('x-request-id'), 'synthetic-request');
    assert.equal(result.headers.get('cache-control'), 'no-store');
    assert.equal(result.headers.get('set-cookie'), null);
    assert.equal(result.headers.get('x-team4-caller-token'), null);
    assert.equal(result.headers.get('location'), null);
    assert.equal((await result.json()).error.code, 'CS_TICKET_VERSION_CONFLICT');
    await fetch(origin + '/api/v1/tft/projects?limit=10&cursor=encoded%2Bcursor%3D');
  }, async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ error: { code: 'CS_TICKET_VERSION_CONFLICT' } }), { status: 409, headers: {
      'Content-Type': 'application/json', ETag: '"4"', 'X-Request-ID': 'synthetic-request',
      'Set-Cookie': 'untrusted=secret', Location: 'https://evil.invalid', 'X-Team4-Caller-Token': 'private',
    } });
  }, true);
  assert.equal(calls[0].url, 'https://platform.example.invalid/api/v1/cs/tickets');
  assert.equal(calls[0].options.redirect, 'error');
  assert.equal(calls[0].options.body.toString(), '{"subject":"synthetic"}');
  assert.deepEqual(calls[0].options.headers, {
    Authorization: 'Bearer fixture-server-token', Cookie: '__Host-team4_session=session',
    Origin: 'https://app.example.invalid', 'X-CSRF-Token': 'bound-csrf',
    'if-match': '"3"', 'idempotency-key': 'synthetic-key', 'Content-Type': 'application/json',
  });
  assert.equal(calls[1].url, 'https://platform.example.invalid/api/v1/tft/projects?limit=10&cursor=encoded%2Bcursor%3D');
});

test('business allowlist rejects unknown paths, query injection, methods, formats and oversized input', async () => {
  let calls = 0;
  await fixture(async origin => {
    for (const path of ['/api/v1/cs/tickets?target=https://evil.invalid', '/api/v1/cs/tickets?limit=1&limit=2', '/api/v1/tft/projects?cursor=' + 'x'.repeat(201)]) assert.equal((await fetch(origin + path)).status, 400);
    for (const path of ['/api/v1/tft/projects/unknown', '/api/v1/tft/projects/00000000-0000-0000-0000-000000000001/staffing-requests', '/api/v1/platform/dev/accounts']) assert.equal((await fetch(origin + path)).status, 404);
    assert.equal((await fetch(origin + '/api/v1/tft/projects', { method: 'POST' })).status, 405);
    assert.equal((await fetch(origin + '/api/v1/cs/tickets', { method: 'HEAD' })).status, 405);
    assert.equal((await fetch(origin + '/api/v1/cs/tickets', { method: 'POST', body: '{}' })).status, 415);
    assert.equal((await fetch(origin + '/api/v1/cs/tickets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: 'x'.repeat(1024 * 1024 + 1) })).status, 413);
  }, async () => { calls++; return new Response('{}', { headers: { 'Content-Type': 'application/json' } }); }, true);
  assert.equal(calls, 0);
});

test('business upstream redirects, HTML, oversized responses and timeouts stay failures without disclosing content', async () => {
  for (const reply of [
    () => new Response(null, { status: 302, headers: { Location: 'https://evil.invalid' } }),
    () => new Response('private-provider-body', { headers: { 'Content-Type': 'text/html' } }),
    () => new Response('private-provider-body'.repeat(500000), { headers: { 'Content-Type': 'application/json' } }),
    () => { throw new Error('private-provider-body'); },
  ]) {
    await fixture(async origin => {
      const response = await fetch(origin + '/api/v1/cs/tickets');
      assert.equal(response.status, 502);
      assert.ok(!(await response.text()).includes('private-provider-body'));
    }, async () => reply(), true);
  }
});


test('serves a symlinked static root but rejects a file symlink outside it', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'team4-root-'));
  const content = join(directory, 'content');
  await mkdir(content);
  await writeFile(join(content, 'index.html'), '<h1>Linked root</h1>');
  await writeFile(join(directory, 'private.txt'), 'private fixture');
  await symlink(content, join(directory, 'public'));
  await symlink(join(directory, 'private.txt'), join(content, 'escape.txt'));
  const server = createServer(handler({ directory: join(directory, 'public'), upstream: 'https://platform.example.invalid', tokenProvider: async () => 'fixture' }));
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    assert.equal((await fetch(origin + '/tft/projects')).status, 200);
    const escaped = await fetch(origin + '/escape.txt');
    assert.equal(escaped.status, 404);
    assert.ok(!(await escaped.text()).includes('private fixture'));
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await rm(directory, { recursive: true });
  }
});
