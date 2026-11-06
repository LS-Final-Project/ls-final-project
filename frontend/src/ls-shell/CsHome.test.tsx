import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import CsHome from './CsHome';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
const day = (daysAgo: number) => new Date(Date.now() - daysAgo * 86_400_000).toISOString();
const item = (id: string, state: string, daysAgo: number) => ({ id, subject: id, external_id: `ID-${id}`, channel: 'EMAIL', product_code: 'P1', received_at: day(daysAgo), data_origin: 'synthetic', workflow_state: state });
const view = () => { setApiMode('live'); return renderAt('/cs', '/cs', <CsHome />); };

// 업무 홈은 상태·담당자 서버 필터로 오늘 할 일을 센다. URL의 state·owner로 응답을 나눈다.
function serve(byState: Record<string, unknown[]>, requests: unknown[] | 'error' = []) {
  return vi.fn(async (url: RequestInfo | URL) => {
    const u = new URL(String(url), 'http://x');
    if (u.pathname.endsWith('/platform/me')) return jsonResponse(200, { data: { id: 'u1', display_name: '김상담', roles: ['cs_agent'] } });
    if (u.pathname.endsWith('/improvement-requests')) return requests === 'error' ? jsonResponse(503, errorBody('UNAVAILABLE', '잠시 후')) : jsonResponse(200, { data: { items: requests } });
    const key = `${u.searchParams.get('state')}|${u.searchParams.get('owner') ?? ''}`;
    return jsonResponse(200, { data: { items: byState[key] ?? [], next_cursor: null } });
  });
}
const req = (id: string, status: string, owner: string | null = 'u1') => ({ id, status, request_type: 'FAULT', product_code: 'P1', fault_id: 'FLT-001', created_at: day(2), origin: 'SYSTEM_CANDIDATE', problem_statement: 'p', version: 1, evidence_count: 3, cs_owner: owner });

test('오늘 할 일은 새 문의와 내 담당 진행 문의를 세고, 1순위를 크게 보이고 다음 순서를 이어 보인다', async () => {
  const fetch = serve({
    'RECEIVED|': [item('a', 'RECEIVED', 10)],
    'IN_REVIEW,ANSWERED|u1': [item('b', 'IN_REVIEW', 2), item('f', 'ANSWERED', 5)],
    'TRANSFERRED,WAITING_CUSTOMER|u1': [item('c', 'TRANSFERRED', 3)],
  }, [req('r1', 'DRAFT'), req('r2', 'REVIEWED'), req('r3', 'COMPLETED'), req('r4', 'REVIEWED', 'someone-else')]);
  vi.stubGlobal('fetch', fetch);
  view();
  // 분석(우선순위·기한)이 없으면 오래된 순: a(10일) → f(5일) → b(2일). 기다리는 c는 빠진다.
  const go = await screen.findByRole('link', { name: /문의 처리/ });
  expect(go.getAttribute('href')).toBe('/cs/tickets/a');
  expect(screen.getByRole('heading', { name: /번역 전/ }).textContent).toContain('a');
  const queue = [...document.querySelectorAll('.hm-queue a.hm-q')].map(a => a.getAttribute('href'));
  expect(queue).toEqual(['/cs/tickets/f', '/cs/tickets/b']);
  expect(screen.queryByText('ID-c')).toBeNull();
  // 인사말에는 건수를 두지 않는다(KAN-243).
  expect(await screen.findByRole('heading', { name: '김상담님, 오늘도 차근차근 시작해 볼까요?' })).toBeTruthy();
  expect(screen.getByText('내 담당만 · 초안 검토 1건 포함')).toBeTruthy();
  expect(screen.getByText('검토 완료 기록')).toBeTruthy();
  expect(screen.getByText(/김상담님/)).toBeTruthy();
  // 분석이 없으면 기한·긴급 값을 만들어 보이지 않는다.
  expect(screen.queryByText(/기한 넘긴|오늘 기한|긴급 P1/)).toBeNull();
  expect(screen.queryByText('용어 풀이')).toBeNull();expect(screen.queryByText('다음 할 일')).toBeNull();
  const urls = fetch.mock.calls.map(([url]) => String(url));
  expect(urls.some(url => url.includes('state=IN_REVIEW%2CANSWERED') && url.includes('owner=u1'))).toBe(true);
  // 개선요청은 서버의 담당 필터로 내 것만 읽는다(팀 전체 목록을 받지 않는다).
  expect(urls.filter(url => url.includes('/improvement-requests'))).toEqual(['/api/v1/cs/improvement-requests?owner=u1']);
});

test('개선요청을 못 읽으면 오류를 보이고 처리할 문의 건수에 +를 붙인다', async () => {
  vi.stubGlobal('fetch', serve({ 'RECEIVED|': [item('a', 'RECEIVED', 1)] }, 'error'));
  view();
  expect(await screen.findByText('처리할 문의 1+건 중 1순위')).toBeTruthy();
  expect(screen.getByText('불러오지 못함 · 새로고침하세요')).toBeTruthy();
  expect(screen.getByText('!')).toBeTruthy();
});

test('목록 조회가 거부되면 이전 값을 보이지 않고 오류를 안내한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(403, errorBody('FORBIDDEN', '권한 없음'))));
  view();
  await screen.findByText('권한이 없습니다');
  expect(screen.queryByText('지금 할 일')).toBeNull();
});

// 개발 환경 화면(DevCheck)은 ['tickets', mode]에 쪽수 없는 목록을 저장한다. 그 캐시가 있어도 업무 홈이 깨지면 안 된다.
test('개발 환경 화면이 남긴 문의 목록 캐시와 충돌하지 않는다', async () => {
  setApiMode('live');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['tickets', 'live'], { data: { items: [item('x', 'RECEIVED', 1)], next_cursor: null } });
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [item('a', 'RECEIVED', 10)], next_cursor: null } })));
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/cs']}><Routes><Route path="/cs" element={<CsHome />} /></Routes></MemoryRouter></QueryClientProvider>);
  expect((await screen.findByRole('link', { name: /문의 처리/ })).getAttribute('href')).toBe('/cs/tickets/a');
});

test('인사말 왼쪽에 역할별 엘로 프로필과 역할 배지를 보인다', async () => {
  vi.stubGlobal('fetch', serve({}));
  const { container } = view();
  expect(await screen.findByText('C/S 담당자')).toBeTruthy();
  const profile = container.querySelector('.hello .role-pf');
  expect(profile?.getAttribute('data-role')).toBe('C/S 담당자');
  expect(profile?.querySelector('img')?.getAttribute('src')).toContain('ello-cs-headset');
});
