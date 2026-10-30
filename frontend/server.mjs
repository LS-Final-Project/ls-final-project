import { createServer } from 'node:http';
import { readFile, stat, realpath } from 'node:fs/promises';
import { resolve, sep, extname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { evaluationReadPath, relayEvaluationRead, evaluationControlPath, relayEvaluationControl } from './evaluation-relay.mjs';

const allowed = new Set(['/api/v1/platform/health', '/api/v1/platform/ready']);
const authentication = new Map([
  ['/api/v1/platform/auth/login', 'GET'],
  ['/api/v1/platform/auth/callback', 'POST'],
  ['/api/v1/platform/auth/logout', 'POST'],
  ['/api/v1/platform/me', 'GET'],
  ['/api/v1/platform/assignees', 'GET'],
]);
const mailboxAuthentication = new Map([
  ['/api/v1/platform/auth/mailbox', 'GET'],
  ['/api/v1/platform/auth/mailbox/start', 'POST'],
  ['/api/v1/platform/auth/mailbox/callback', 'POST'],
  ['/api/v1/platform/auth/mailbox/complete', 'POST'],
]);
const mailboxForms = new Set(['/api/v1/platform/auth/mailbox/callback', '/api/v1/platform/auth/mailbox/complete']);
const mailboxDisconnect = /^\/api\/v1\/platform\/auth\/mailbox\/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\/disconnect$/;
// [경로 틀, 허용 메서드, 허용 query 키]. backend/api/platform/relay.py의 BUSINESS_ROUTES와 같아야 하며
// 두 검사가 docs/openapi/business-relay-routes.json과 각각 대조한다. 허용 query 키는 GET에만 적용한다.
// 변경 메서드의 query는 경로와 상관없이 400이다(handler의 query 검사).
export const businessRoutes = [
  ['/api/v1/cs/tickets', ['GET', 'POST'], ['limit', 'cursor']],
  ['/api/v1/cs/tickets/{uuid}', ['GET', 'PATCH'], []],
  ['/api/v1/cs/tickets/{uuid}/audit', ['GET'], []],
  ['/api/v1/cs/tickets/{uuid}/analysis', ['GET', 'POST'], []],
  ['/api/v1/cs/tickets/{uuid}/analysis/corrections', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/units', ['GET'], []],
  ['/api/v1/cs/tickets/{uuid}/unit-history', ['GET'], []],
  ['/api/v1/cs/tickets/{uuid}/units/{uuid}', ['PUT'], []],
  ['/api/v1/cs/tickets/{uuid}/resolution-case', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow', ['GET'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/start-review', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/transfer', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/recall', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/return', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/dept-result', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/dept-results/{uuid}/review', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/close', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/owner', ['PUT'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/return-subtype', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/send', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/sends/{uuid}/refresh', ['POST'], []],
  ['/api/v1/cs/tickets/{uuid}/workflow/sends/{uuid}/retry', ['POST'], []],
  ['/api/v1/cs/stats/overview', ['GET'], ['start', 'end', 'dept', 'customer']],
  ['/api/v1/cs/stats/owners', ['GET'], ['start', 'end', 'dept']],
  ['/api/v1/cs/stats/customers/{customer}', ['GET'], ['start', 'end']],
  ['/api/v1/cs/improvement-requests', ['GET', 'POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}', ['GET', 'PATCH'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/versions', ['GET'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/evidence', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/evidence/{uuid}/remove', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/owner', ['PUT'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/review', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/send', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/acknowledge', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/reply', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/action', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/resend', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/review-rejection', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/followup-plan', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/complete', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/followup-signals', ['GET'], ['as_of']],
  ['/api/v1/cs/improvement-requests/{uuid}/rounds', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/condition-comparison', ['GET', 'POST'], ['version']],
  ['/api/v1/cs/improvement-requests/{uuid}/condition-comparison/judgements', ['POST'], []],
  ['/api/v1/cs/improvement-requests/{uuid}/condition-comparison/evidence/{evidence}/remove', ['POST'], []],
  ['/api/v1/cs/condition-comparisons', ['GET'], ['product_code', 'fault_id', 'version']],
  ['/api/v1/tft/projects', ['GET'], ['limit', 'cursor']],
  ['/api/v1/tft/projects/{uuid}', ['GET'], []],
  ['/api/v1/tft/projects/{uuid}/documents', ['GET'], []],
  ['/api/v1/tft/documents/{uuid}', ['GET'], []],
  ['/api/v1/tft/documents/{uuid}/clauses', ['GET'], ['parent', 'limit', 'cursor']],
  ['/api/v1/tft/projects/{uuid}/process', ['POST'], []],
  ['/api/v1/tft/projects/{uuid}/requirements', ['GET'], ['limit', 'cursor', 'document_version_id', 'equipment', 'task', 'necessity', 'status', 'needs_rereview', 'has_open_question']],
  ['/api/v1/tft/requirements/{uuid}', ['GET'], []],
  ['/api/v1/tft/requirements/{uuid}/comparison', ['GET'], []],
  ['/api/v1/tft/requirements/{uuid}/supplement-options', ['GET'], []],
  ['/api/v1/tft/requirements/{uuid}/supplement-selection', ['POST'], []],
  ['/api/v1/tft/projects/{uuid}/partner-candidates', ['GET'], ['requirement_id']],
  ['/api/v1/tft/projects/{uuid}/questions', ['GET', 'POST'], ['status', 'requirement_id', 'target', 'target_dept']],
  ['/api/v1/tft/projects/{uuid}/questions/{uuid}/confirm', ['POST'], []],
  ['/api/v1/tft/questions/{uuid}/reply', ['POST'], []],
];
// 디코드한 경로에서 검사한다. customer·evidence는 이 문자만, 첫 글자는 영숫자로 허용해 ?·#·/와 .·.. 세그먼트가 upstream URL을 바꾸지 못하게 한다.
export const placeholders = { '{uuid}': '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '{customer}': '[A-Za-z0-9][A-Za-z0-9._-]{0,63}', '{evidence}': '[A-Za-z0-9][A-Za-z0-9._-]{0,63}', '{dept}': '[A-Z][A-Z0-9_]{0,63}' };
const compiledRoutes = businessRoutes.map(([template, methods, query]) => [new RegExp(`^${template.split(/(\{uuid\}|\{customer\}|\{evidence\}|\{dept\})/).map(part => placeholders[part] ?? part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('')}$`), methods, query]);

/**
 * 허용표에서 경로와 정확히 일치하는 업무 API의 메서드·query 키를 찾는다.
 * Args: path는 디코딩된 요청 경로다.
 * Returns: [허용 메서드, 허용 query 키]. 업무 경로가 아니면 빈 배열 두 개.
 */
function businessRoute(path) {
  const found = compiledRoutes.find(([pattern]) => pattern.test(path));
  return found ? [found[1], found[2]] : [[], []];
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

const authenticationCookies = new Set(['__Host-team4_session', '__Host-team4_oauth', '__Host-team4_mailbox_oauth']);
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
 * Args: directory는 정적 파일 위치, upstream은 고정 플랫폼 출처, tokenProvider는 서버 IAM 토큰 제공자, fetcher는 HTTP 경계다. businessRelayEnabled·evaluationReadRelayEnabled·evaluationControlRelayEnabled·mailboxAuthEnabled는 각자 기본 비활성인 업무·평가·사서함 중계 설정이다.
 * Returns: 브라우저 인증 쿠키와 IAM 토큰을 구분해 처리하는 HTTP 요청 처리기.
 */
export function handler({ directory, upstream, tokenProvider, fetcher = fetch, businessRelayEnabled = false, evaluationReadRelayEnabled = false, evaluationControlRelayEnabled = false, mailboxAuthEnabled = false }) {
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
      if (path.startsWith('/api/v1/platform/auth/mailbox/') && !mailboxAuthEnabled) return json(response, 404, { error: { code: 'NOT_FOUND' } });
      if (evaluationReadRelayEnabled && evaluationReadPath(path, request.method)) {
        return relayEvaluationRead(request, response, { path, searchParams: parsed.searchParams, target, tokenProvider, fetcher });
      }
      if (evaluationControlRelayEnabled && evaluationControlPath(path, request.method)) {
        return relayEvaluationControl(request, response, { path, searchParams: parsed.searchParams, target, tokenProvider, fetcher });
      }
      const assignees = path === '/api/v1/platform/assignees';
      if (assignees && ([...parsed.searchParams].some(([key, value]) => key !== 'role' || value !== 'cs_agent') || parsed.searchParams.getAll('role').length > 1)) return json(response, 400, { error: { code: 'BAD_REQUEST' } });
      const authMethod = authentication.get(path) || (mailboxAuthEnabled ? (mailboxAuthentication.get(path) || (mailboxDisconnect.test(path) ? 'POST' : undefined)) : undefined);
      const [business, businessQuery] = businessRelayEnabled ? businessRoute(path) : [[], []];
      if (authMethod) response.setHeader('Referrer-Policy', 'no-referrer');
      if (!['GET', 'HEAD'].includes(request.method) && !(request.method === 'POST' && authMethod === 'POST') && !business.includes(request.method)) return json(response, 405, { error: { code: 'METHOD_NOT_ALLOWED' } });
      if (path === '/health') return json(response, 200, { status: 'ok', service: 'web' });
      if (path.startsWith('/api/')) {
        if ((!allowed.has(path) && !authMethod && !business.length) || (parsed.search && !business.length && !assignees)) return json(response, 404, { error: { code: 'NOT_FOUND' } });
        if (authMethod && request.method !== authMethod) return json(response, 405, { error: { code: 'METHOD_NOT_ALLOWED' } });
        if (business.length) {
          if (!business.includes(request.method)) return json(response, 405, { error: { code: 'METHOD_NOT_ALLOWED' } });
          const pairs = [...parsed.searchParams];
          if (pairs.length && (request.method !== 'GET' || !businessQuery.length || pairs.length !== new Set(pairs.map(([key]) => key)).size || pairs.some(([key, value]) => !businessQuery.includes(key) || value.length > 200))) return json(response, 400, { error: { code: 'BAD_REQUEST' } });
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
          if (path === '/api/v1/platform/auth/callback' || (mailboxAuthEnabled && mailboxForms.has(path))) {
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
        const result = await fetcher(new URL(path + (business.length || assignees ? parsed.search : ''), target), { method: request.method, headers, body: form, signal: AbortSignal.timeout((authMethod || business.length) ? 45000 : 15000), redirect: authMethod ? 'manual' : 'error' });
        if (business.length && (result.status >= 300 && result.status < 400 || (result.status !== 204 && (result.headers.get('content-type') || '').split(';', 1)[0].trim().toLowerCase() !== 'application/json'))) {
          await result.body?.cancel();
          throw new Error('Invalid business response');
        }
        const body = business.length ? await limitedBody(result, 8 * 1024 * 1024) : await result.text();
        const outgoing = { 'Content-Type': result.headers.get('content-type') || 'application/json', 'Cache-Control': 'no-store' };
        if (authMethod) {
          for (const name of ['Location', 'X-CSRF-Token', 'X-Request-ID', 'Content-Security-Policy']) {
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
      // Compare canonical paths: macOS temporary directories may resolve through /private.
      const staticRoot = await realpath(root);
      let file = resolve(staticRoot, '.' + path);
      if (!file.startsWith(staticRoot + sep) && file !== staticRoot) return json(response, 404, { error: { code: 'NOT_FOUND' } });
      try {
        if (!(await stat(file)).isFile()) file = resolve(staticRoot, 'index.html');
      } catch {
        if (extname(path)) return json(response, 404, { error: { code: 'NOT_FOUND' } });
        file = resolve(staticRoot, 'index.html');
      }
      file = await realpath(file);
      if (!file.startsWith(staticRoot + sep)) return json(response, 404, { error: { code: 'NOT_FOUND' } });
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
  const service = createServer(handler({ directory: process.env.STATIC_DIR || '/app/dist', upstream, tokenProvider: metadataTokenProvider(upstream), businessRelayEnabled: process.env.BUSINESS_RELAY_ENABLED === 'true', evaluationReadRelayEnabled: process.env.EVALUATION_READ_RELAY_ENABLED === 'true', evaluationControlRelayEnabled: process.env.EVALUATION_CONTROL_RELAY_ENABLED === 'true', mailboxAuthEnabled: process.env.MAILBOX_AUTH_ENABLED === 'true' }));
  service.listen(Number(process.env.PORT || 8080), '0.0.0.0');
}
