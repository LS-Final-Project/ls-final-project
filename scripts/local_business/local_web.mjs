// Local provider fixture; standard web relay preserves product authentication/CSRF.
import { createServer } from 'node:https';
import { readFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';

export function validateSettings(settings) {
  const ports = settings?.ports;
  if (!ports || !['web', 'platform', 'cs', 'tft'].every(key =>
    Number.isInteger(ports[key]) && ports[key] > 0 && ports[key] <= 65535)
    || new Set(Object.values(ports)).size !== 4
    || settings.web_origin !== `https://localhost:${ports.web}`) {
    throw new Error('INVALID_LOCAL_WEB_SETTINGS');
  }
  return settings;
}

export function localHandler(settings, web) {
  validateSettings(settings);
  const authority = `localhost:${settings.ports.web}`;
  return (req, res) => {
    if (req.headers.host !== authority) { res.writeHead(403); res.end(); return; }
    let url;
    try { url = new URL(req.url, settings.web_origin); }
    catch { res.writeHead(400); res.end(); return; }
    if (url.origin !== settings.web_origin) { res.writeHead(403); res.end(); return; }
    if (url.pathname === '/local/authorize') {
      const state = url.searchParams.get('state');
      if (req.method !== 'GET' || !state || !/^[A-Za-z0-9_-]{43}$/.test(state)
        || url.searchParams.size !== 1) { res.writeHead(400); res.end(); return; }
      res.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
        'Referrer-Policy': 'no-referrer',
        'Content-Security-Policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'",
      });
      res.end(`<!doctype html><html lang="ko"><meta charset="utf-8"><title>로컬 합성 계정 선택</title><h1>로컬 업무용 합성 계정</h1><p>실제 Microsoft 로그인이 아닙니다. 명시적으로 enable-real을 실행한 뒤 허가된 신규 작업만 모델을 호출합니다. 메일·push·평가 실행은 꺼져 있습니다.</p><form method="post" action="/api/v1/platform/auth/callback"><input type="hidden" name="state" value="${state}"><button name="code" value="local-1">C/S 담당자</button><button name="code" value="local-2">변압기 기술지원</button><button name="code" value="local-3">수주 담당자</button><button name="code" value="local-4">관리자</button></form></html>`);
      return;
    }
    return web(req, res);
  };
}

async function main() {
  const state = resolve(process.env.LOCAL_BUSINESS_STATE ?? '/local/state');
  const repo = resolve(process.env.LOCAL_BUSINESS_REPO ?? '/workspace');
  const settings = validateSettings(JSON.parse(readFileSync(join(state, 'settings.json'), 'utf8')));
  process.env.NODE_ENV = 'test'; // Standard relay permits HTTP only to literal 127.0.0.1.
  const { handler } = await import(pathToFileURL(join(repo, 'frontend/server.mjs')).href);
  const web = handler({
    directory: join(repo, 'frontend/dist'),
    upstream: `http://127.0.0.1:${settings.ports.platform}/`,
    tokenProvider: async () => 'local-transport-fixture', businessRelayEnabled: true,
  });
  createServer({
    key: readFileSync(join(state, 'auth/tls-key.pem')),
    cert: readFileSync(join(state, 'auth/tls-cert.pem')),
  }, localHandler(settings, web)).listen(settings.ports.web, '127.0.0.1');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  await main();
}
