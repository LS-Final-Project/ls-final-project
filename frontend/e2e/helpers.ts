import { readFileSync } from 'node:fs';
import type { Page, Route } from '@playwright/test';

type Example = { path: string; method: string; body: unknown };
const examples: Example[] = JSON.parse(readFileSync(new URL('../src/mocks/generated/cs.json', import.meta.url), 'utf-8'));
export const example = (path: string, method = 'GET') => examples.find(item => item.path === path && item.method === method)!.body;
// 목록 예시의 첫 행(진행 중 상태라 기본 조건에 보인다)과, 상세 예시가 있는 문의. 상세는 어느 문의 ID로 와도 같은 예시를 돌려준다.
export const TICKET_ID = '22222222-2222-4222-8222-222222222223';
const DETAIL_EXAMPLE = '/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222';
export const REQUEST_ID = 'dddddddd-dddd-4ddd-8ddd-ddddddddddd1';

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  ({ status, contentType: 'application/json', headers, body: JSON.stringify(body) });
export const errorBody = (code: string, message: string) => ({ error: { code, message, retryable: false }, meta: { request_id: '11111111-1111-4111-8111-111111111199' } });
export const csAgent = { data: { id: '00000000-0000-4000-8000-000000000001', display_name: '김지원', dept: 'C/S팀', roles: ['cs_agent'] } };

// 기본 응답: 세션(cs_agent), 문의 목록·상세, 개선요청 상세. 시험이 handler로 일부를 바꾼다.
export type Handler = (route: Route, url: URL) => Promise<boolean> | boolean;
export async function openLive(page: Page, path: string, handler: Handler = () => false) {
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    if (await handler(route, url)) return;
    const { pathname } = url;
    if (pathname.endsWith('/platform/me')) return route.fulfill(json(csAgent, 200, { 'X-CSRF-Token': 'e2e' }));
    if (pathname === '/api/v1/cs/tickets') return route.fulfill(json(example('/api/v1/cs/tickets')));
    if (/^\/api\/v1\/cs\/tickets\/[0-9a-f-]{36}$/.test(pathname)) return route.fulfill(json(example(DETAIL_EXAMPLE)));
    if (pathname === `/api/v1/cs/improvement-requests/${REQUEST_ID}`) return route.fulfill(json(example(`/api/v1/cs/improvement-requests/${REQUEST_ID}`)));
    return route.fulfill(json({ data: { items: [], next_cursor: null } }));
  });
  // API 모드는 새로고침하면 모의로 돌아가므로, 처음 화면에서 실제 API로 바꾼 뒤 주소만 옮긴다.
  await page.goto('/');
  await page.selectOption('select[aria-label="API 모드"]', 'live');
  await page.evaluate(to => { history.pushState({}, '', to); dispatchEvent(new PopStateEvent('popstate')); }, path);
}
