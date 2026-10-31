import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer, request as httpRequest } from 'node:http';
import { mkdtemp, writeFile, rm, symlink, mkdir, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { handler, businessRoutes, placeholders } from './server.mjs';

/**
 * 임시 정적 파일과 로컬 HTTP 서버로 웹 중계 경계를 검사한다.
 * Args: callback은 요청 검사, fetcher는 upstream 대체이며 뒤의 네 설정은 각각 업무·평가 읽기·평가 제어·사서함 명시 활성화 여부다.
 * Returns: 검사와 임시 서버·파일 정리를 마친 Promise.
 */
async function fixture(callback, fetcher, businessRelayEnabled = false, evaluationReadRelayEnabled = false, evaluationControlRelayEnabled = false, mailboxAuthEnabled = false) {
  const directory = await mkdtemp(join(tmpdir(), 'team4-web-'));
  await writeFile(join(directory, 'index.html'), '<h1>Fixture</h1>');
  const server = createServer(handler({ directory, upstream: 'https://platform.example.invalid', tokenProvider: async () => 'fixture-server-token', fetcher, businessRelayEnabled, evaluationReadRelayEnabled, evaluationControlRelayEnabled, mailboxAuthEnabled }));
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
    assert.equal((await fetch(origin + '/api/v1/tft/projects', { method: 'DELETE' })).status, 405);
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

const jobUrl = '/api/v1/platform/jobs/22222222-2222-4222-8222-222222222222';

test('evaluation read relay defaults closed and allows only GET with bounded cursor inputs', async () => {
  let calls = 0;
  const fetcher = async () => { calls++; return new Response('{}', { headers: { 'Content-Type': 'application/json' } }); };
  await fixture(async origin => {
    for (const suffix of ['', '/events']) assert.equal((await fetch(origin + jobUrl + suffix)).status, 404);
  }, fetcher);
  await fixture(async origin => {
    for (const method of ['POST', 'DELETE', 'HEAD']) assert.equal((await fetch(origin + jobUrl, { method })).status, 405);
    for (const path of [jobUrl + '/events?target=https://evil.invalid', jobUrl + '?after=1', jobUrl + '/events?after=1&after=2', jobUrl + '/events?after=' + '1'.repeat(65)]) assert.equal((await fetch(origin + path)).status, 400);
    for (const path of ['/api/v1/platform/jobs/not-uuid/events', jobUrl + '/cancel', '/api/v1/platform/evaluations/experiments/extra', '/api/v1/platform/evaluations/run']) assert.equal((await fetch(origin + path)).status, 404);
  }, fetcher, false, true);
  assert.equal(calls, 0);
});

test('business relay opens only the job status paths of the read relay (RFP status_url)', async () => {
  const urls = [];
  await fixture(async origin => {
    for (const suffix of ['', '/events']) assert.equal((await fetch(origin + jobUrl + suffix)).status, 200);
    // 평가 읽기·제어는 자기 스위치가 없으면 닫혀 있다.
    assert.equal((await fetch(origin + '/api/v1/platform/evaluations/policy')).status, 404);
    assert.equal((await fetch(origin + jobUrl, { method: 'POST' })).status, 405);
    assert.equal((await fetch(origin + '/api/v1/platform/jobs/not-uuid')).status, 404);
  }, async url => {
    urls.push(String(url));
    const type = String(url).endsWith('/events') ? 'text/event-stream' : 'application/json';
    return new Response(type === 'application/json' ? '{}' : ': ok\n\n', { headers: { 'Content-Type': type } });
  }, true);
  assert.deepEqual(urls.map(url => new URL(url).pathname), [jobUrl, jobUrl + '/events']);
});

test('evaluation status and error relay preserve status/ETag and discard browser/upstream credentials', async () => {
  for (const status of [200, 401, 403, 404, 410, 503]) {
    await fixture(async origin => {
      const response = await fetch(origin + jobUrl, { headers: {
        Cookie: '__Host-team4_session=session; __Host-team4_oauth=private; unrelated=private',
        Authorization: 'Bearer forged', 'X-Team4-Principal': 'forged', 'X-Team4-Caller-Token': 'forged',
      } });
      assert.equal(response.status, status);
      assert.equal(response.headers.get('etag'), '"5"');
      assert.equal(response.headers.get('x-request-id'), 'request-id');
      assert.equal(response.headers.get('set-cookie'), null);
      assert.equal(response.headers.get('location'), null);
      assert.deepEqual(await response.json(), { data: { status: 'RUNNING' } });
    }, async (url, options) => {
      assert.equal(String(url), 'https://platform.example.invalid' + jobUrl);
      assert.deepEqual(options.headers, { Authorization: 'Bearer fixture-server-token', Cookie: '__Host-team4_session=session' });
      assert.equal(options.redirect, 'error');
      return new Response('{"data":{"status":"RUNNING"}}', { status, headers: {
        'Content-Type': 'application/json', ETag: '"5"', 'X-Request-ID': 'request-id',
        'Set-Cookie': 'private=secret', Location: 'https://evil.invalid',
      } });
    }, false, true);
  }
});

test('evaluation SSE forwards cursor, delivers first frame before upstream closes and aborts on disconnect', async () => {
  let abort;
  const aborted = new Promise(resolve => { abort = resolve; });
  let streamController;
  await fixture(async origin => {
    const controller = new AbortController();
    const response = await fetch(origin + jobUrl + '/events?after=1', {
      signal: controller.signal, headers: { 'Last-Event-ID': '2', Cookie: '__Host-team4_session=session' },
    });
    assert.equal(response.headers.get('content-type'), 'text/event-stream; charset=utf-8');
    assert.equal(response.headers.get('x-accel-buffering'), 'no');
    const reader = response.body.getReader();
    const frame = await reader.read();
    assert.equal(new TextDecoder().decode(frame.value), 'id: 3\nevent: job.progress\ndata: {}\n\n');
    controller.abort();
    await Promise.race([aborted, new Promise((_, reject) => setTimeout(() => reject(new Error('Upstream was not aborted')), 2000).unref())]);
    reader.releaseLock();
  }, async (url, options) => {
    assert.equal(String(url), 'https://platform.example.invalid' + jobUrl + '/events?after=1');
    assert.equal(options.headers['Last-Event-ID'], '2');
    options.signal.addEventListener('abort', () => { streamController.error(new Error('Disconnected')); abort(); }, { once: true });
    return new Response(new ReadableStream({ start(controller) {
      streamController = controller;
      controller.enqueue(new TextEncoder().encode('id: 3\nevent: job.progress\ndata: {}\n\n'));
    } }), { headers: { 'Content-Type': 'text/event-stream' } });
  }, false, true);
});

test('evaluation SSE preserves completed 204 and pre-header errors without synthetic success', async () => {
  for (const status of [204, 400, 401, 403, 404, 410, 503]) {
    await fixture(async origin => {
      const response = await fetch(origin + jobUrl + '/events');
      assert.equal(response.status, status);
      if (status === 204) assert.equal(await response.text(), '');
      else assert.deepEqual(await response.json(), { error: { code: 'SAFE_ERROR' } });
    }, async () => new Response(status === 204 ? null : '{"error":{"code":"SAFE_ERROR"}}', {
      status, headers: status === 204 ? {} : { 'Content-Type': 'application/json' },
    }), false, true);
  }
});

test('evaluation relay rejects redirects, wrong content types and oversized JSON', async () => {
  for (const reply of [
    () => new Response(null, { status: 302, headers: { Location: 'https://evil.invalid' } }),
    () => new Response('private-upstream', { headers: { 'Content-Type': 'text/html' } }),
    () => new Response('private-upstream'.repeat(100000), { status: 503, headers: { 'Content-Type': 'application/json' } }),
    () => { throw new Error('private-upstream'); },
  ]) await fixture(async origin => {
    const response = await fetch(origin + jobUrl + '/events');
    assert.equal(response.status, 502);
    assert.ok(!(await response.text()).includes('private-upstream'));
  }, async () => reply(), false, true);
});

test('evaluation mid-stream failure terminates the connection without a synthetic completion', async () => {
  await fixture(async origin => {
    const response = await fetch(origin + jobUrl + '/events');
    const reader = response.body.getReader();
    assert.match(new TextDecoder().decode((await reader.read()).value), /id: 1/);
    await assert.rejects(reader.read());
    reader.releaseLock();
  }, async () => new Response(new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('id: 1\nevent: job.queued\ndata: {}\n\n'));
    setTimeout(() => controller.error(new Error('private-provider-failure')), 30);
  } }), { headers: { 'Content-Type': 'text/event-stream' } }), false, true);
});

test('evaluation upstream header deadline aborts the request and returns a safe failure', { timeout: 20000 }, async () => {
  let aborted = false;
  await fixture(async origin => {
    const response = await fetch(origin + jobUrl + '/events');
    assert.equal(response.status, 502);
    assert.equal((await response.json()).error.code, 'UPSTREAM_UNAVAILABLE');
    assert.equal(aborted, true);
  }, async (_url, options) => new Promise((_, reject) => {
    options.signal.addEventListener('abort', () => { aborted = true; reject(new Error('private-timeout')); }, { once: true });
  }), false, true);
});

const controlRun = '/api/v1/platform/evaluations/runs/22222222-2222-4222-8222-222222222222';
const controlHeaders = { Origin: 'https://app.example.invalid', 'X-CSRF-Token': 'session-csrf', 'If-Match': '"2"', 'Idempotency-Key': 'control-key-123' };

test('evaluation control relay defaults closed independently of read relay', async () => {
  let calls = 0;
  await fixture(async origin => {
    assert.equal((await fetch(origin + controlRun)).status, 404);
    assert.equal((await fetch(origin + controlRun + '/cancel', { method: 'POST', headers: controlHeaders })).status, 405);
  }, async () => { calls++; return Response.json({}); }, false, true);
  assert.equal(calls, 0);
});

test('evaluation controls forward only session and command headers to fixed platform', async () => {
  const calls = [];
  await fixture(async origin => {
    for (const action of ['cancel', 'resume']) {
      const response = await fetch(origin + controlRun + '/' + action, { method: 'POST', headers: {
        ...controlHeaders, Cookie: '__Host-team4_session=session; unrelated=private', Authorization: 'Bearer browser', 'X-User-ID': 'fake',
      } });
      assert.equal(response.status, 200); assert.equal(response.headers.get('etag'), '"3"');
      assert.equal(response.headers.get('set-cookie'), null); assert.equal(response.headers.get('location'), null);
    }
    assert.equal((await fetch(origin + controlRun)).status, 200);
  }, async (url, init) => {
    calls.push({url: String(url), init});
    return Response.json({data: {}}, {headers:{ETag:'"3"', 'Set-Cookie':'private=1', Location:'https://wrong.invalid'}});
  }, false, false, true);
  assert.equal(calls.length, 3);
  assert.equal(calls[0].url, 'https://platform.example.invalid' + controlRun + '/cancel');
  assert.deepEqual(calls[0].init.headers, { Authorization: 'Bearer fixture-server-token', Cookie: '__Host-team4_session=session', origin: controlHeaders.Origin, 'x-csrf-token':'session-csrf', 'if-match':'"2"', 'idempotency-key':'control-key-123' });
  assert.equal(calls[0].init.redirect, 'error'); assert.equal(calls[0].init.body, undefined);
});

test('evaluation controls reject wrong methods, queries, bodies and missing protection before forwarding', async () => {
  let calls=0;
  await fixture(async origin => {
    const post = (suffix, headers=controlHeaders, body) => fetch(origin+controlRun+suffix,{method:'POST',headers,body});
    assert.equal((await fetch(origin+controlRun+'/cancel')).status,405);
    assert.equal((await post('/resume?x=1')).status,400);
    assert.equal((await post('/cancel',controlHeaders,'{}')).status,413);
    assert.equal((await post('/cancel',{})).status,403);
    assert.equal((await post('/cancel',{...controlHeaders,'Idempotency-Key':''})).status,400);
    const {'If-Match': ignored, ...withoutVersion}=controlHeaders;
    assert.equal((await post('/cancel',withoutVersion)).status,428);
    assert.equal((await post('/cancel',{...controlHeaders,'If-Match':'*'})).status,400);
    assert.equal((await fetch(origin+'/api/v1/platform/evaluations/policy')).status,404);
    assert.equal((await post('/delete')).status,405);
  },async()=>{calls++;return Response.json({});},false,false,true);
  assert.equal(calls,0);
});

const evaluationsBase = '/api/v1/platform/evaluations';
const admissionHeaders = { Origin: 'https://app.example.invalid', 'X-CSRF-Token': 'session-csrf', 'Idempotency-Key': 'admission-key-123', 'Content-Type': 'application/json' };
const admissionBody = JSON.stringify({ experiment_id: '33333333-3333-4333-8333-333333333333' });

test('evaluation read relay forwards policy and own list GETs only, without queries or bodies', async () => {
  const calls = [];
  await fixture(async origin => {
    for (const suffix of ['/policy', '/experiments', '/runs']) {
      const response = await fetch(origin + evaluationsBase + suffix, { headers: { Cookie: '__Host-team4_session=session; unrelated=private', Authorization: 'Bearer browser' } });
      assert.equal(response.status, 200, suffix);
    }
    for (const suffix of ['/policy?x=1', '/runs?limit=5']) assert.equal((await fetch(origin + evaluationsBase + suffix)).status, 400);
    const withBody = await new Promise((resolve, reject) => {
      const sent = httpRequest(origin + '/', { method: 'GET', path: evaluationsBase + '/experiments', headers: { 'Content-Length': '2' } }, reply => { reply.resume(); reply.on('end', () => resolve(reply.statusCode)); });
      sent.on('error', reject);
      sent.end('{}');
    });
    assert.equal(withBody, 413);
    // 읽기만 켜져 있으면 같은 경로의 접수 POST는 중계하지 않는다.
    assert.equal((await fetch(origin + evaluationsBase + '/runs', { method: 'POST', headers: admissionHeaders, body: admissionBody })).status, 405);
  }, async (url, init) => { calls.push({ url: String(url), init }); return Response.json({ data: { items: [] } }); }, false, true);
  assert.deepEqual(calls.map(call => call.url), ['/policy', '/experiments', '/runs'].map(suffix => 'https://platform.example.invalid' + evaluationsBase + suffix));
  assert.deepEqual(calls[0].init.headers, { Authorization: 'Bearer fixture-server-token', Cookie: '__Host-team4_session=session' });
});

test('evaluation admission relay forwards one small JSON body with protection headers under the control flag', async () => {
  const calls = [];
  await fixture(async origin => {
    const response = await fetch(origin + evaluationsBase + '/runs', { method: 'POST', headers: { ...admissionHeaders, 'If-Match': 'opaque-1', Cookie: '__Host-team4_session=session; unrelated=private', Authorization: 'Bearer browser' }, body: admissionBody });
    assert.equal(response.status, 202);
    assert.equal(response.headers.get('location'), controlRun);
    assert.equal(response.headers.get('retry-after'), '5');
    assert.deepEqual(await response.json(), { data: { run_id: 'r' } });
    const without = await fetch(origin + evaluationsBase + '/runs', { method: 'POST', headers: admissionHeaders, body: admissionBody });
    assert.equal(without.headers.get('location'), null);
    // 목록 GET은 제어 플래그만으로는 중계하지 않는다.
    assert.equal((await fetch(origin + evaluationsBase + '/runs')).status, 404);
  }, async (url, init) => {
    calls.push({ url: String(url), init });
    const location = calls.length === 1 ? controlRun : 'https://wrong.invalid/x';
    return Response.json({ data: { run_id: 'r' } }, { status: 202, headers: { Location: location, 'Retry-After': '5', 'Set-Cookie': 'private=1' } });
  }, false, false, true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, 'https://platform.example.invalid' + evaluationsBase + '/runs');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(Buffer.from(calls[0].init.body).toString(), admissionBody);
  assert.deepEqual(calls[0].init.headers, { Authorization: 'Bearer fixture-server-token', Cookie: '__Host-team4_session=session', origin: admissionHeaders.Origin, 'x-csrf-token': 'session-csrf', 'if-match': 'opaque-1', 'idempotency-key': 'admission-key-123', 'Content-Type': 'application/json' });
  assert.equal(calls[1].init.headers['if-match'], undefined);
});

test('with both evaluation flags on, GET runs lists and POST runs admits', async () => {
  const calls = [];
  await fixture(async origin => {
    assert.equal((await fetch(origin + evaluationsBase + '/runs')).status, 200);
    assert.equal((await fetch(origin + evaluationsBase + '/runs', { method: 'POST', headers: admissionHeaders, body: admissionBody })).status, 200);
  }, async (url, init) => { calls.push(init.method); return Response.json({ data: {} }); }, false, true, true);
  assert.deepEqual(calls, ['GET', 'POST']);
});

test('evaluation admission relay rejects unsafe requests before forwarding', async () => {
  let calls = 0;
  await fixture(async origin => {
    const post = (headers = admissionHeaders, body = admissionBody, suffix = '') => fetch(origin + evaluationsBase + '/runs' + suffix, { method: 'POST', headers, body });
    const { 'Content-Type': ignored, ...noType } = admissionHeaders;
    assert.equal((await post(noType)).status, 415);
    assert.equal((await post({ ...admissionHeaders, 'Content-Type': 'text/plain' })).status, 415);
    assert.equal((await post(admissionHeaders, 'x'.repeat(1025))).status, 413);
    assert.equal((await post(admissionHeaders, '')).status, 413);
    assert.equal((await post({ 'Content-Type': 'application/json', 'Idempotency-Key': 'admission-key-123' })).status, 403);
    assert.equal((await post({ ...admissionHeaders, 'Idempotency-Key': 'short' })).status, 400);
    assert.equal((await post({ ...admissionHeaders, 'If-Match': 'x'.repeat(129) })).status, 400);
    assert.equal((await post(admissionHeaders, admissionBody, '?experiment=1')).status, 400);
  }, async () => { calls++; return Response.json({}); }, false, false, true);
  assert.equal(calls, 0);
});

test('evaluation control relay preserves conflict/auth errors and refuses HTML or redirect success', async () => {
  for(const status of [401,403,409,412,503]) {
    await fixture(async origin => {
      assert.equal((await fetch(origin+controlRun+'/cancel',{method:'POST',headers:controlHeaders})).status,status);
    },async()=>Response.json({error:{code:'SAFE_ERROR'}},{status}),false,false,true);
  }
  for(const result of [new Response('private html'),new Response(null,{status:302,headers:{Location:'https://wrong.invalid'}}),Response.json({value:'x'.repeat(1024*1024)})]) {
    await fixture(async origin => {
      const response=await fetch(origin+controlRun+'/cancel',{method:'POST',headers:controlHeaders});
      assert.equal(response.status,502);assert.ok(!(await response.text()).includes('private'));
    },async()=>result,false,false,true);
  }
});

test('summary relay is read-only, defaults closed and forwards no browser identity', async () => {
  const path = controlRun + '/summary';
  await fixture(async origin => assert.equal((await fetch(origin + path)).status, 404));
  let calls = 0;
  await fixture(async origin => {
    assert.equal((await fetch(origin + path, { method: 'POST' })).status, 405);
    assert.equal((await fetch(origin + path + '?gold=anything')).status, 400);
    assert.equal((await fetch(origin + path + '/raw')).status, 404);
    const response = await fetch(origin + path, { headers: { Authorization: 'browser', Cookie: '__Host-team4_session=session; other=secret', 'X-User-Id': 'spoofed' } });
    assert.equal(response.status, 403);
    assert.equal(response.headers.get('set-cookie'), null);
    assert.match(response.headers.get('cache-control'), /no-store/);
  }, async (url, options) => {
    calls++;
    assert.equal(String(url), 'https://platform.example.invalid' + path);
    assert.equal(options.method, 'GET');
    assert.deepEqual(options.headers, { Authorization: 'Bearer fixture-server-token', Cookie: '__Host-team4_session=session' });
    return Response.json({ error: { code: 'FORBIDDEN' } }, { status: 403, headers: { 'Set-Cookie': 'secret' } });
  }, false, true);
  assert.equal(calls, 1);
});

test('cases relay forwards only cursor and limit once and stays read-only', async () => {
  const path = controlRun + '/cases';
  const seen = [];
  await fixture(async origin => {
    assert.equal((await fetch(origin + path, { method: 'POST' })).status, 405);
    for (const bad of ['?gold=1', '?limit=1&limit=2', '?cursor=' + 'a'.repeat(65), '?after=1']) assert.equal((await fetch(origin + path + bad)).status, 400);
    assert.equal((await fetch(origin + path + '/raw')).status, 404);
    assert.equal((await fetch(origin + path + '?cursor=' + controlRun.slice(-36) + '&limit=20')).status, 200);
    assert.equal((await fetch(origin + path)).status, 200);
  }, async (url, options) => {
    seen.push(String(url));
    assert.equal(options.method, 'GET');
    return Response.json({ data: { items: [], next_cursor: null } });
  }, false, true);
  assert.deepEqual(seen, ['https://platform.example.invalid' + path + '?cursor=' + controlRun.slice(-36) + '&limit=20', 'https://platform.example.invalid' + path]);
});

test('mailbox relay is disabled independently and never reaches upstream by default', async () => {
  let calls = 0;
  await fixture(async origin => {
    for (const suffix of ['/start', '/callback', '/complete', '/11111111-1111-4111-8111-111111111111/disconnect']) {
      assert.equal((await fetch(origin + '/api/v1/platform/auth/mailbox' + suffix, { method: 'POST' })).status, 404);
    }
    assert.equal((await fetch(origin + '/api/v1/platform/auth/mailbox')).status, 404);
  }, async () => { calls += 1; return new Response('{}'); });
  assert.equal(calls, 0);
});

test('enabled mailbox proxy preserves bounded form, separate cookie, CSP and fixed upstream', async () => {
  const calls = [];
  await fixture(async origin => {
    const base = origin + '/api/v1/platform/auth/mailbox';
    const headers = { Origin: origin, Cookie: '__Host-team4_session=session; __Host-team4_mailbox_oauth=binding; unrelated=private', 'Content-Type': 'application/x-www-form-urlencoded', Authorization: 'Bearer browser-untrusted' };
    const relay = await fetch(base + '/callback', { method: 'POST', headers, body: 'state=synthetic-state&code=synthetic-code' });
    assert.equal(relay.status, 200);
    assert.equal(relay.headers.get('content-security-policy'), "default-src 'none'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'");
    assert.equal(relay.headers.get('referrer-policy'), 'no-referrer');
    assert.match(relay.headers.get('set-cookie'), /__Host-team4_mailbox_oauth/);
    const complete = await fetch(base + '/complete', { method: 'POST', headers, body: 'state=synthetic-state&code=synthetic-code', redirect: 'manual' });
    assert.equal(complete.status, 303);
    assert.equal(complete.headers.get('location'), 'https://app.example.invalid/');
    assert.equal((await fetch(base + '/start', { method: 'POST', headers: { ...headers, 'X-CSRF-Token': 'bound-csrf' } })).status, 200);
    assert.equal((await fetch(base + '/11111111-1111-4111-8111-111111111111/disconnect', { method: 'POST', headers: { ...headers, 'X-CSRF-Token': 'bound-csrf' } })).status, 204);
    assert.equal((await fetch(base + '/complete?code=private', { method: 'POST' })).status, 404);
    assert.equal((await fetch(base + '/complete')).status, 405);
    assert.equal((await fetch(base + '/callback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status, 400);
    assert.equal((await fetch(base + '/complete', { method: 'POST', headers, body: 'code=' + 'x'.repeat(9000) })).status, 413);
    assert.equal((await fetch(base + '/unknown')).status, 404);
  }, async (url, options) => {
    calls.push({ url: String(url), options });
    if (String(url).endsWith('/complete')) return new Response(null, { status: 303, headers: { Location: 'https://app.example.invalid/' } });
    if (String(url).endsWith('/disconnect')) return new Response(null, { status: 204 });
    return new Response('<form></form>', { headers: { 'Content-Type': 'text/html', 'Content-Security-Policy': "default-src 'none'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'", 'Set-Cookie': '__Host-team4_mailbox_oauth=binding; Secure; HttpOnly; Path=/; SameSite=Lax' } });
  }, false, false, false, true);
  assert.equal(calls.length, 4);
  assert.equal(calls[0].url, 'https://platform.example.invalid/api/v1/platform/auth/mailbox/callback');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer fixture-server-token');
  assert.equal(calls[0].options.headers.Cookie, '__Host-team4_session=session; __Host-team4_mailbox_oauth=binding');
  assert.equal(String(calls[0].options.body), 'state=synthetic-state&code=synthetic-code');
  assert.equal(calls[0].options.redirect, 'manual');
  assert.equal(calls[2].options.headers['X-CSRF-Token'], 'bound-csrf');
});

test('enabled mailbox list relay forwards GET only with server token and selected cookies', async () => {
  const calls = [];
  await fixture(async origin => {
    const list = origin + '/api/v1/platform/auth/mailbox';
    const response = await fetch(list, { headers: { Origin: origin, Cookie: '__Host-team4_session=session; unrelated=private', Authorization: 'Bearer browser-untrusted' } });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { data: { items: [] } });
    assert.equal(response.headers.get('x-csrf-token'), 'bound-csrf');
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
    assert.equal((await fetch(list + '?limit=1')).status, 404);
    assert.equal((await fetch(list, { method: 'POST' })).status, 405);
    assert.equal((await fetch(list, { method: 'HEAD' })).status, 405);
  }, async (url, options) => {
    calls.push({ url: String(url), options });
    return Response.json({ data: { items: [] } }, { headers: { 'X-CSRF-Token': 'bound-csrf' } });
  }, false, false, false, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://platform.example.invalid/api/v1/platform/auth/mailbox');
  assert.equal(calls[0].options.method, 'GET');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer fixture-server-token');
  assert.equal(calls[0].options.headers.Cookie, '__Host-team4_session=session');
  assert.equal(calls[0].options.redirect, 'manual');
  assert.equal(calls[0].options.body, undefined);
});

// 플랫폼 relay.py 검사와 같은 허용표·사례 파일이다.
const relayRoutes = JSON.parse(await readFile(new URL('../docs/openapi/business-relay-routes.json', import.meta.url), 'utf8'));
/**
 * 클라이언트 URL 정규화 없이 원문 경로를 그대로 보낸다. fetch는 .·%2E%2E 세그먼트를 먼저 줄인다.
 * Args: origin은 검사 서버 주소, method·path는 퍼센트 인코딩 원문 요청이다.
 * Returns: 응답 상태를 담은 Promise.
 */
function rawRequest(origin, method, path) {
  // DELETE에 본문을 붙이면 405 뒤 keep-alive 연결이 끊겨 다음 요청이 실패하므로 본문은 쓰기 메서드만 보낸다.
  const write = ['POST', 'PATCH', 'PUT'].includes(method);
  return new Promise((resolve, reject) => {
    const sent = httpRequest(origin + '/', { method, path, headers: write ? { 'Content-Type': 'application/json' } : {} }, response => { response.resume(); response.on('end', () => resolve(response.statusCode)); });
    sent.on('error', reject);
    sent.end(write ? '{}' : undefined);
  });
}
const routeRows = rows => rows.map(([path, methods, query]) => JSON.stringify([path, [...methods].sort(), [...query].sort()])).sort();

test('web business relay table matches the shared fixture exactly', () => {
  assert.deepEqual(routeRows(businessRoutes), routeRows(relayRoutes.routes.map(route => [route.path, route.methods, route.query])));
  assert.equal(placeholders['{customer}'], relayRoutes.customer_pattern);
  assert.equal(placeholders['{evidence}'], relayRoutes.evidence_pattern);
  assert.equal(placeholders['{dept}'], relayRoutes.dept_pattern);
});

test('every route placeholder has a pattern so none is matched literally (KAN-116 {dept})', () => {
  const used = new Set(relayRoutes.routes.flatMap(route => route.path.match(/\{[^}]+\}/g) ?? []));
  for (const name of used) assert.ok(name in placeholders, name);
});

test('web business relay forwards or rejects each shared case before reaching the platform', async () => {
  const calls = [];
  await fixture(async origin => {
    for (const item of relayRoutes.cases) {
      const before = calls.length;
      const status = await rawRequest(origin, item.method, item.path);
      if (item.forward) {
        assert.equal(status, 200, item.path);
        assert.equal(calls.length, before + 1, item.path);
        const [url, method] = calls.at(-1);
        assert.equal(method, item.method, item.path);
        const sent = new URL(url);
        const asked = new URL(item.path, 'http://local.invalid');
        assert.equal(sent.origin, 'https://platform.example.invalid');
        assert.equal(decodeURIComponent(sent.pathname), decodeURIComponent(asked.pathname));
        assert.deepEqual([...sent.searchParams], [...asked.searchParams]);
      } else {
        assert.equal(status, item.status, item.path);
        assert.equal(calls.length, before, item.path);
      }
    }
  }, async (url, options) => { calls.push([String(url), options.method]); return Response.json({ data: {} }); }, true);
});


const fileUrl = '/api/v1/tft/documents/11111111-1111-4111-8111-111111111111/file';
const uploadUrl = '/api/v1/tft/projects/11111111-1111-4111-8111-111111111111/documents';

test('web relay passes a document only on the download path, as an attachment', async () => {
  await fixture(async origin => {
    const done = await fetch(origin + fileUrl);
    assert.equal(done.status, 200);
    assert.equal(done.headers.get('content-type'), 'application/pdf');
    assert.equal(done.headers.get('content-disposition'), 'attachment');
    assert.equal(done.headers.get('x-content-type-options'), 'nosniff');
    assert.equal(done.headers.get('cache-control'), 'no-store');
    assert.equal((await done.arrayBuffer()).byteLength, 9 * 1024 * 1024);
    assert.equal((await fetch(origin + fileUrl.replace('/file', ''))).status, 502);
  }, async () => new Response(new Uint8Array(9 * 1024 * 1024), { headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'inline' } }), true);
  await fixture(async origin => {
    const refused = await fetch(origin + fileUrl);
    assert.equal(refused.status, 502);
    assert.ok(!(await refused.text()).includes('<html>'));
  }, async () => new Response('<html>', { headers: { 'Content-Type': 'text/html' } }), true);
  await fixture(async origin => {
    assert.equal((await fetch(origin + fileUrl)).status, 502);
  }, async () => new Response(new Uint8Array(26 * 1024 * 1024 + 1), { headers: { 'Content-Type': 'application/pdf' } }), true);
  await fixture(async origin => {
    const refused = await fetch(origin + fileUrl);
    assert.equal(refused.status, 404);
    assert.equal(refused.headers.get('content-disposition'), null);
  }, async () => Response.json({ error: { code: 'TFT_DOCUMENT_NOT_FOUND' } }, { status: 404 }), true);
});

test('web relay takes multipart only on the upload path, up to the file limit', async () => {
  const calls = [];
  const post = (origin, path, type, body) => fetch(origin + path, { method: 'POST', headers: { 'Content-Type': type }, body });
  await fixture(async origin => {
    const multipart = 'multipart/form-data; boundary=synthetic';
    const body = new Uint8Array(2 * 1024 * 1024);
    assert.equal((await post(origin, uploadUrl, multipart, body)).status, 201);
    assert.equal(calls.at(-1).headers['Content-Type'], multipart);
    assert.equal(calls.at(-1).body.length, body.length);
    const before = calls.length;
    assert.equal((await post(origin, uploadUrl, 'application/json', '{}')).status, 415);
    assert.equal((await post(origin, '/api/v1/tft/projects', multipart, 'x')).status, 415);
    assert.equal((await post(origin, '/api/v1/tft/projects', 'application/json', body)).status, 413);
    assert.equal((await post(origin, uploadUrl, multipart, new Uint8Array(26 * 1024 * 1024 + 1))).status, 413);
    assert.equal(calls.length, before);
  }, async (url, options) => { calls.push(options); return Response.json({ data: {} }, { status: 201 }); }, true);
});

test('assignee lookup forwards only the cs_agent selector and session', async () => {
  const calls = [];
  await fixture(async origin => {
    const response = await fetch(origin + '/api/v1/platform/assignees?role=cs_agent', {
      headers: { Cookie: '__Host-team4_session=session; unrelated=private' },
    });
    assert.equal(response.status, 200);
    for (const query of ['role=sales', 'role=cs_agent&role=cs_agent', 'other=x']) {
      assert.equal((await fetch(origin + '/api/v1/platform/assignees?' + query)).status, 400);
    }
    assert.equal((await fetch(origin + '/api/v1/platform/assignees', { method: 'POST' })).status, 405);
  }, async (url, options) => {
    calls.push(String(url));
    assert.equal(options.headers.Cookie, '__Host-team4_session=session');
    return new Response('{"data":{"items":[]}}', {headers: {'Content-Type': 'application/json'}});
  });
  assert.deepEqual(calls, ['https://platform.example.invalid/api/v1/platform/assignees?role=cs_agent']);
});

test('web relay preserves DOCX attachment on the exact RFP export file route', async () => {
  const path = '/api/v1/tft/projects/11111111-1111-4111-8111-111111111111/draft/exports/22222222-2222-4222-8222-222222222222/file';
  const mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  await fixture(async origin => {
    const response = await fetch(origin + path);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('content-type'), mime);
    assert.equal(response.headers.get('content-disposition'), 'attachment; filename="review.docx"');
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(await response.text(), 'PK-docx');
    assert.equal((await fetch(origin + path.split('/exports/')[0])).status, 502);
  }, async () => new Response('PK-docx', { headers: { 'Content-Type': mime, 'Content-Disposition': 'attachment; filename="review.docx"' } }), true);
});

test('notification relay forwards only session-owned public endpoints and bounded query/body', async () => {
  const calls = [];
  const id = '11111111-1111-4111-8111-111111111111';
  await fixture(async origin => {
    for (const [path, method, body] of [
      ['/api/v1/platform/notifications?limit=20&offset=0', 'GET'],
      ['/api/v1/platform/notifications/unread-count', 'GET'],
      [`/api/v1/platform/users?ids=${Array(100).fill(id).join(',')}`, 'GET'],
      [`/api/v1/platform/notifications/${id}/read`, 'POST', '{}'],
      ['/api/v1/platform/push/public-key', 'GET'],
      ['/api/v1/platform/push/subscription', 'PUT', '{"endpoint":"synthetic-test-only"}'],
      ['/api/v1/platform/push/subscription', 'DELETE', '{"endpoint":"synthetic-test-only"}'],
    ]) {
      const response = await fetch(origin + path, { method, body, headers: { 'Content-Type': 'application/json', Origin: origin, 'X-CSRF-Token': 'csrf', Cookie: '__Host-team4_session=bound; unrelated=private; __Host-team4_oauth=not-needed', Authorization: 'Bearer browser-secret', 'X-User-ID': 'spoof' } });
      assert.equal(response.status, method === 'GET' ? 200 : 204);
      assert.equal(response.headers.get('cache-control'), 'no-store');
    }
    const total = calls.length;
    for (const path of [
      '/api/v1/platform/notifications?user_id=someone', '/api/v1/platform/notifications?limit=101',
      '/api/v1/platform/notifications?offset=-1', '/api/v1/platform/notifications?limit=1&limit=2',
      '/api/v1/platform/notifications/unread-count?offset=0', '/api/v1/platform/push/public-key?key=x',
      '/api/v1/platform/users?ids=not-a-uuid', `/api/v1/platform/users?ids=${Array(101).fill(id).join(',')}`,
      `/api/v1/platform/users?ids=${id}&ids=${id}`, `/api/v1/platform/users?email=${id}`,
      '/api/v1/platform/notifications/other/read', '/api/v1/platform/push/private-key',
    ]) assert.ok((await fetch(origin + path)).status >= 400, path);
    assert.equal((await fetch(origin + '/api/v1/platform/push/subscription', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status, 405);
    assert.equal((await fetch(origin + `/api/v1/platform/notifications/${id}/read`, { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: '{}' })).status, 415);
    assert.equal(calls.length, total);
  }, async (url, options) => {
    calls.push([String(url), options]);
    assert.equal(options.headers.Authorization, 'Bearer fixture-server-token');
    assert.equal(options.headers.Cookie, '__Host-team4_session=bound');
    assert.equal(options.headers['X-CSRF-Token'], 'csrf');
    assert.equal(options.headers['X-User-ID'], undefined);
    assert.equal(options.redirect, 'error');
    return options.method === 'GET' ? Response.json({ data: {} }) : new Response(null, { status: 204 });
  });
  assert.equal(calls.length, 7);
  assert.equal(calls[0][0], 'https://platform.example.invalid/api/v1/platform/notifications?limit=20&offset=0');
});

test('root service worker is JavaScript with revalidation instead of a one-hour static cache', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'team4-sw-'));
  await writeFile(join(directory, 'index.html'), '<h1>Fixture</h1>');
  await writeFile(join(directory, 'sw.js'), '// no fake push');
  const server = createServer(handler({ directory, upstream: 'https://platform.example.invalid', tokenProvider: async () => 'not-used' }));
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/sw.js`);
    assert.equal(response.status, 200); assert.match(response.headers.get('content-type'), /javascript/);
    assert.equal(response.headers.get('cache-control'), 'no-cache');
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(directory, { recursive: true }); }
});
