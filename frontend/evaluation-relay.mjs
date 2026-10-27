import { once } from 'node:events';

const jobPath = /^\/api\/v1\/platform\/jobs\/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(\/events)?$/;

/**
 * 평가 작업의 고정 읽기 경로만 식별한다.
 * Args: path는 디코딩된 URL 경로다.
 * Returns: 상태·SSE·실행 집계 경로일 때만 true.
 */
export function evaluationReadPath(path) {
  return jobPath.test(path) || /^\/api\/v1\/platform\/evaluations\/runs\/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\/summary$/.test(path);
}

const runPath = /^\/api\/v1\/platform\/evaluations\/runs\/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(\/(cancel|resume))?$/;

/** 실행 조회·취소·재개만 식별한다. Args: URL 경로. Returns: 허용 대상 여부. */
export function evaluationControlPath(path) {
  return runPath.test(path);
}

/** 본문 없는 제어 요청을 고정 플랫폼으로 전달한다. Args: 요청/응답과 검증된 출처·경로·IAM 공급자. Returns: 중계 완료. */
export async function relayEvaluationControl(request, response, { path, searchParams, target, tokenProvider, fetcher }) {
  /** 안전한 오류만 반환한다. Args: 상태와 상수 코드. Returns: 없음. */
  const fail = (status, code) => {
    response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify({ error: { code } }));
  };
  const write = /\/(cancel|resume)$/.test(path);
  if (request.method !== (write ? 'POST' : 'GET')) return fail(405, 'METHOD_NOT_ALLOWED');
  if ([...searchParams].length) return fail(400, 'BAD_REQUEST');
  if (request.headers['transfer-encoding'] || Number(request.headers['content-length'] || 0) !== 0) return fail(413, 'REQUEST_TOO_LARGE');
  if (write) {
    if (!request.headers.origin || !request.headers['x-csrf-token']) return fail(403, 'FORBIDDEN');
    const key = request.headers['idempotency-key'];
    if (typeof key !== 'string' || !/^[\x21-\x7e]{8,128}$/.test(key)) return fail(400, 'INVALID_IDEMPOTENCY_KEY');
    if (!request.headers['if-match']) return fail(428, 'VERSION_REQUIRED');
    if (!/^"[1-9][0-9]{0,18}"$/.test(request.headers['if-match'])) return fail(400, 'INVALID_VERSION');
    if (request.headers['x-csrf-token'].length > 256 || request.headers.origin.length > 2048) return fail(400, 'BAD_REQUEST');
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  /** 연결 종료를 전파한다. Args: 없음. Returns: 없음. */
  const disconnected = () => controller.abort();
  response.once('close', disconnected);
  try {
    const headers = { Authorization: `Bearer ${await tokenProvider()}` };
    const cookies = (request.headers.cookie || '').split(';').map(value => value.trim()).filter(value => value.split('=', 1)[0] === '__Host-team4_session');
    if (cookies.length) headers.Cookie = cookies.join('; ');
    if (write) for (const name of ['origin', 'x-csrf-token', 'if-match', 'idempotency-key']) headers[name] = request.headers[name];
    const result = await fetcher(new URL(path, target), { method: request.method, headers, redirect: 'error', signal: controller.signal });
    if (result.status >= 300 && result.status < 400 || result.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
      await result.body?.cancel();
      throw new Error('Invalid control response');
    }
    const chunks = [];
    let size = 0;
    if (result.body) for await (const chunk of result.body) {
      size += chunk.length;
      if (size > 1024 * 1024) throw new Error('Response too large');
      chunks.push(chunk);
    }
    const outgoing = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' };
    for (const name of ['ETag', 'X-Request-ID']) {
      const value = result.headers.get(name);
      if (value) outgoing[name] = value;
    }
    response.writeHead(result.status, outgoing);
    response.end(Buffer.concat(chunks));
  } catch {
    if (!response.destroyed && !response.headersSent) fail(502, 'UPSTREAM_UNAVAILABLE');
    else if (!response.destroyed) response.destroy();
  } finally {
    clearTimeout(timer);
    response.off('close', disconnected);
    controller.abort();
  }
}

/**
 * 평가 상태·SSE를 고정 플랫폼으로 전달하고 종료·시간 제한·역압력을 적용한다.
 * Args: request/response는 웹 연결, path/searchParams는 검증 대상 경로/쿼리,
 * target은 고정 플랫폼 출처, tokenProvider는 서버 IAM 토큰 공급자, fetcher는 HTTP 경계다.
 * Returns: 실제 응답 전달 또는 안전한 오류·연결 종료를 마친 Promise.
 */
export async function relayEvaluationRead(request, response, { path, searchParams, target, tokenProvider, fetcher }) {
  /** 오류를 제한 JSON으로 반환한다. Args: status/code는 안전한 상수다. Returns: 없음. */
  const fail = (status, code) => {
    response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify({ error: { code } }));
  };
  if (request.method !== 'GET') return fail(405, 'METHOD_NOT_ALLOWED');
  if (path.endsWith('/summary') && (request.headers['transfer-encoding'] || Number(request.headers['content-length'] || 0) !== 0)) return fail(413, 'REQUEST_TOO_LARGE');
  const stream = path.endsWith('/events');
  const pairs = [...searchParams];
  if (pairs.length && (!stream || pairs.length !== 1 || pairs[0][0] !== 'after' || pairs[0][1].length > 64)) return fail(400, 'INVALID_EVENT_CURSOR');
  const lastEventId = request.headers['last-event-id'];
  if (lastEventId !== undefined && (typeof lastEventId !== 'string' || lastEventId.length > 64)) return fail(400, 'INVALID_EVENT_CURSOR');

  const controller = new AbortController();
  let timer;
  /** 대기 제한을 갱신한다. Args: milliseconds는 제한 시간이다. Returns: 없음. */
  const arm = milliseconds => {
    clearTimeout(timer);
    timer = setTimeout(() => controller.abort(), milliseconds);
    timer.unref();
  };
  /** 연결 종료를 전파한다. Args: 없음. Returns: 없음. */
  const disconnected = () => controller.abort();
  response.once('close', disconnected);
  arm(15000);
  try {
    const headers = { Authorization: `Bearer ${await tokenProvider()}` };
    const cookies = (request.headers.cookie || '').split(';').map(value => value.trim()).filter(value => value.split('=', 1)[0] === '__Host-team4_session');
    if (cookies.length) headers.Cookie = cookies.join('; ');
    if (stream && lastEventId !== undefined) headers['Last-Event-ID'] = lastEventId;
    const query = searchParams.toString();
    const result = await fetcher(new URL(path + (query ? '?' + query : ''), target), {
      method: 'GET', headers, redirect: 'error', signal: controller.signal,
    });
    const type = (result.headers.get('content-type') || '').split(';', 1)[0].trim().toLowerCase();
    const eventStream = stream && result.status === 200;
    const noContent = stream && result.status === 204;
    if (result.status >= 300 && result.status < 400 || !noContent && type !== (eventStream ? 'text/event-stream' : 'application/json')) {
      await result.body?.cancel();
      throw new Error('Invalid evaluation response');
    }
    const outgoing = { 'Cache-Control': 'no-cache, no-store' };
    for (const name of ['ETag', 'X-Request-ID', 'Retry-After']) {
      const value = result.headers.get(name);
      if (value) outgoing[name] = value;
    }
    if (noContent) {
      await result.body?.cancel();
      response.writeHead(204, outgoing);
      return response.end();
    }
    if (!eventStream) {
      // 오류/스냅샷은 제한된 JSON만 완전히 읽은 뒤 상태 코드를 보존한다.
      const chunks = [];
      let size = 0;
      if (result.body) for await (const chunk of result.body) {
        size += chunk.length;
        if (size > 1024 * 1024) throw new Error('Response too large');
        chunks.push(chunk);
      }
      response.writeHead(result.status, { ...outgoing, 'Content-Type': 'application/json' });
      return response.end(Buffer.concat(chunks));
    }
    if (!result.body) throw new Error('Missing event stream');
    response.writeHead(200, { ...outgoing, 'Content-Type': 'text/event-stream; charset=utf-8', 'X-Accel-Buffering': 'no' });
    response.flushHeaders();
    arm(45000);
    for await (const chunk of result.body) {
      arm(45000);
      if (!response.write(chunk)) await once(response, 'drain', { signal: controller.signal });
    }
    response.end();
  } catch {
    // upstream 본문·쿠키·IAM 토큰을 로그나 오류에 포함하지 않는다.
    if (response.destroyed) return;
    if (!response.headersSent) fail(502, 'UPSTREAM_UNAVAILABLE');
    else response.destroy();
  } finally {
    clearTimeout(timer);
    response.off('close', disconnected);
    controller.abort();
  }
}
