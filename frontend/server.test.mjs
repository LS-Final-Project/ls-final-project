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
    for (const path of ['/api/v1/platform/jobs/not-uuid/events', jobUrl + '/cancel', '/api/v1/platform/evaluations/runs']) assert.equal((await fetch(origin + path)).status, 404);
  }, fetcher, false, true);
  assert.equal(calls, 0);
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

test('mailbox relay is disabled independently and never reaches upstream by default', async () => {
  let calls = 0;
  await fixture(async origin => {
    for (const suffix of ['/start', '/callback', '/complete', '/11111111-1111-4111-8111-111111111111/disconnect']) {
      assert.equal((await fetch(origin + '/api/v1/platform/auth/mailbox' + suffix, { method: 'POST' })).status, 404);
    }
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
