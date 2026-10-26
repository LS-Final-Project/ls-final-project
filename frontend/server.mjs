import { createServer } from 'node:http';
import { readFile, stat, realpath } from 'node:fs/promises';
import { resolve, sep, extname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { evaluationReadPath, relayEvaluationRead } from './evaluation-relay.mjs';

const allowed = new Set(['/api/v1/platform/health', '/api/v1/platform/ready']);
const authentication = new Map([
  ['/api/v1/platform/auth/login', 'GET'],
  ['/api/v1/platform/auth/callback', 'POST'],
  ['/api/v1/platform/auth/logout', 'POST'],
  ['/api/v1/platform/me', 'GET'],
]);
/**
 * 현재 구현된 업무 API의 경로·메서드만 반환한다.
 * Args: path는 디코딩된 요청 경로다.
 * Returns: 허용 메서드 배열 또는 빈 배열.
 */
function businessMethods(path) {
  const uuid = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
  if (path === '/api/v1/cs/tickets') return ['GET', 'POST'];
  if (new RegExp(`^/api/v1/cs/tickets/${uuid}$`).test(path)) return ['GET', 'PATCH'];
  if (new RegExp(`^/api/v1/cs/tickets/${uuid}/audit$`).test(path)) return ['GET'];
  if (path === '/api/v1/tft/projects' || new RegExp(`^/api/v1/tft/projects/${uuid}$`).test(path)) return ['GET'];
  return [];
}

/**
 * 응답을 제한 크기까지만 읽고 스트림을 정리한다.
 * Args: result는 업무 서비스 응답, limit는 허용 바이트 수다.
 * Returns: 크기 검사를 통과한 본문 Buffer.
 */
async function limitedBody(result, limit) {
  if (!result.body) return Buffer.alloc(0);
  const chunks = [];
  let size = 0;
  for await (const chunk of result.body) {
    size += chunk.length;
    if (size > limit) throw new Error('Response too large');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

const authenticationCookies = new Set(['__Host-team4_session', '__Host-team4_oauth']);
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' };

function json(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify(body));
}

export function metadataTokenProvider(audience) {
  let cached;
  return async () => {
    if (cached && cached.until > Date.now()) return cached.token;
    const endpoint = new URL('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity');
    endpoint.searchParams.set('audience', audience);
    endpoint.searchParams.set('format', 'full');
    const result = await fetch(endpoint, { headers: { 'Metadata-Flavor': 'Google' }, signal: AbortSignal.timeout(5000), redirect: 'error' });
    if (!result.ok) throw new Error('Identity token unavailable');
    const token = await result.text();
    cached = { token, until: Date.now() + 45 * 60 * 1000 };
    return token;
  };
}

/**
 * 정적 화면·인증 경로와 명시적으로 켠 업무 JSON API를 고정 플랫폼으로 중계한다.
 * Args: directory는 정적 파일 위치, upstream은 고정 플랫폼 출처, tokenProvider는 서버 IAM 토큰 제공자, fetcher는 HTTP 경계, businessRelayEnabled와 evaluationReadRelayEnabled는 기본 비활성인 업무/평가 읽기 중계 설정이다.
 * Returns: 브라우저 인증 쿠키와 IAM 토큰을 구분해 처리하는 HTTP 요청 처리기.
 */
export function handler({ directory, upstream, tokenProvider, fetcher = fetch, businessRelayEnabled = false, evaluationReadRelayEnabled = false }) {
  const root = resolve(directory);
  const target = new URL(upstream);
  if (target.protocol !== 'https:' && !(process.env.NODE_ENV === 'test' && target.hostname === '127.0.0.1')) throw new Error('Use an HTTPS platform URL');
  if (target.username || target.password || target.search || target.hash || target.pathname !== '/') throw new Error('Use a platform origin without credentials or path');
  return async (request, response) => {
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Referrer-Policy', 'same-origin');
    try {
      const parsed = new URL(request.url, 'http://local.invalid');
      const path = decodeURIComponent(parsed.pathname);
      if (evaluationReadRelayEnabled && evaluationReadPath(path)) {
        return relayEvaluationRead(request, response, { path, searchParams: parsed.searchParams, target, tokenProvider, fetcher });
      }
      const authMethod = authentication.get(path);
      const business = businessRelayEnabled ? businessMethods(path) : [];
      if (authMethod) response.setHeader('Referrer-Policy', 'no-referrer');
      if (!['GET', 'HEAD'].includes(request.method) && !(request.method === 'POST' && authMethod === 'POST') && !business.includes(request.method)) return json(response, 405, { error: { code: 'METHOD_NOT_ALLOWED' } });
      if (path === '/health') return json(response, 200, { status: 'ok', service: 'web' });
      if (path.startsWith('/api/')) {
        if ((!allowed.has(path) && !authMethod && !business.length) || (parsed.search && !business.length)) return json(response, 404, { error: { code: 'NOT_FOUND' } });
        if (authMethod && request.method !== authMethod) return json(response, 405, { error: { code: 'METHOD_NOT_ALLOWED' } });
        if (business.length) {
          if (!business.includes(request.method)) return json(response, 405, { error: { code: 'METHOD_NOT_ALLOWED' } });
          const pairs = [...parsed.searchParams];
          const list = ['/api/v1/cs/tickets', '/api/v1/tft/projects'].includes(path);
          if (pairs.length && (request.method !== 'GET' || !list || pairs.length !== new Set(pairs.map(([key]) => key)).size || pairs.some(([key, value]) => !['limit', 'cursor'].includes(key) || value.length > 200))) return json(response, 400, { error: { code: 'BAD_REQUEST' } });
        }
        const headers = { Authorization: `Bearer ${await tokenProvider()}` };
        let form;
        if (authMethod || business.length) {
          const cookies = (request.headers.cookie || '').split(';').map(part => part.trim()).filter(part => (business.length ? part.split('=', 1)[0] === '__Host-team4_session' : authenticationCookies.has(part.split('=', 1)[0])));
          if (cookies.length) headers.Cookie = cookies.join('; ');
          if (request.headers.origin) headers.Origin = request.headers.origin;
          if (request.headers['x-csrf-token']) headers['X-CSRF-Token'] = request.headers['x-csrf-token'];
          if (business.length) {
            // 브라우저가 제시한 내부 사용자·호출자·Authorization 헤더는 전달하지 않는다.
            for (const name of ['if-match', 'idempotency-key', 'x-request-id']) if (request.headers[name]) headers[name] = request.headers[name];
            if (request.method !== 'GET') {
              if ((request.headers['content-type'] || '').split(';', 1)[0].trim().toLowerCase() !== 'application/json') return json(response, 415, { error: { code: 'UNSUPPORTED_MEDIA_TYPE' } });
              const chunks = [];
              let size = 0;
              for await (const chunk of request) {
                size += chunk.length;
                if (size > 1024 * 1024) return json(response, 413, { error: { code: 'REQUEST_TOO_LARGE' } });
                chunks.push(chunk);
              }
              form = Buffer.concat(chunks);
              headers['Content-Type'] = 'application/json';
            }
          }
          if (path === '/api/v1/platform/auth/callback') {
            const type = request.headers['content-type'] || '';
            if (type.split(';', 1)[0].trim().toLowerCase() !== 'application/x-www-form-urlencoded') return json(response, 400, { error: { code: 'AUTH_INVALID_CALLBACK' } });
            const chunks = [];
            let size = 0;
            for await (const chunk of request) {
              size += chunk.length;
              if (size > 8192) return json(response, 413, { error: { code: 'REQUEST_TOO_LARGE' } });
              chunks.push(chunk);
            }
            form = Buffer.concat(chunks);
            headers['Content-Type'] = type;
          }
        }
        const result = await fetcher(new URL(path + (business.length ? parsed.search : ''), target), { method: request.method, headers, body: form, signal: AbortSignal.timeout((authMethod || business.length) ? 45000 : 15000), redirect: authMethod ? 'manual' : 'error' });
        if (business.length && (result.status >= 300 && result.status < 400 || (result.status !== 204 && (result.headers.get('content-type') || '').split(';', 1)[0].trim().toLowerCase() !== 'application/json'))) {
          await result.body?.cancel();
          throw new Error('Invalid business response');
        }
        const body = business.length ? await limitedBody(result, 8 * 1024 * 1024) : await result.text();
        const outgoing = { 'Content-Type': result.headers.get('content-type') || 'application/json', 'Cache-Control': 'no-store' };
        if (authMethod) {
          for (const name of ['Location', 'X-CSRF-Token', 'X-Request-ID']) {
            const value = result.headers.get(name);
            if (value) outgoing[name] = value;
          }
          const cookies = result.headers.getSetCookie();
          if (cookies.length) outgoing['Set-Cookie'] = cookies;
        }
        if (business.length) {
          for (const name of ['ETag', 'X-Request-ID']) {
            const value = result.headers.get(name);
            if (value) outgoing[name] = value;
          }
        }
        response.writeHead(result.status, outgoing);
        return response.end(request.method === 'HEAD' ? undefined : body);
      }
      if (path.split('/').some(part => part.startsWith('.') && part !== '')) return json(response, 404, { error: { code: 'NOT_FOUND' } });
      let file = resolve(root, '.' + path);
      if (!file.startsWith(root + sep) && file !== root) return json(response, 404, { error: { code: 'NOT_FOUND' } });
      try {
        if (!(await stat(file)).isFile()) file = resolve(root, 'index.html');
      } catch {
        if (extname(path)) return json(response, 404, { error: { code: 'NOT_FOUND' } });
        file = resolve(root, 'index.html');
      }
      file = await realpath(file);
      if (!file.startsWith(root + sep)) return json(response, 404, { error: { code: 'NOT_FOUND' } });
      response.writeHead(200, { 'Content-Type': mime[extname(file)] || 'application/octet-stream', 'Cache-Control': extname(file) === '.html' ? 'no-cache' : 'public, max-age=3600' });
      response.end(request.method === 'HEAD' ? undefined : await readFile(file));
    } catch {
      // No upstream bodies, request headers or identity tokens in logs/errors.
      if (!response.headersSent) json(response, 502, { error: { code: 'UPSTREAM_UNAVAILABLE', message: '서비스 연결을 확인해 주세요.' } });
      else response.end();
    }
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const upstream = process.env.PLATFORM_URL;
  if (!upstream) throw new Error('PLATFORM_URL is required');
  const service = createServer(handler({ directory: process.env.STATIC_DIR || '/app/dist', upstream, tokenProvider: metadataTokenProvider(upstream), businessRelayEnabled: process.env.BUSINESS_RELAY_ENABLED === 'true', evaluationReadRelayEnabled: process.env.EVALUATION_READ_RELAY_ENABLED === 'true' }));
  service.listen(Number(process.env.PORT || 8080), '0.0.0.0');
}
