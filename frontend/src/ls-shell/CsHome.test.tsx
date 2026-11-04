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
function serve(byState: Record<string, unknown[]>, requests: unknown[] = []) {
  return vi.fn(async (url: RequestInfo | URL) => {
    const u = new URL(String(url), 'http://x');
    if (u.pathname.endsWith('/platform/me')) return jsonResponse(200, { data: { id: 'u1', display_name: '김상담', roles: ['cs_agent'] } });
    if (u.pathname.endsWith('/improvement-requests')) return jsonResponse(200, { data: { items: requests } });
    const key = `${u.searchParams.get('state')}|${u.searchParams.get('owner') ?? ''}`;
    return jsonResponse(200, { data: { items: byState[key] ?? [], next_cursor: null } });
  });
}
const req = (id: string, status: string) => ({ id, status, request_type: 'FAULT', product_code: 'P1', fault_id: 'FLT-001', created_at: day(2), origin: 'SYSTEM_CANDIDATE', problem_statement: 'p', version: 1, evidence_count: 3, cs_owner: null });

test('오늘 할 일은 새 문의와 내 담당 진행 문의를 세고, 오래된 순으로 할 일을 보인다', async () => {
  const fetch = serve({
    'RECEIVED|': [item('a', 'RECEIVED', 10)],
    'IN_REVIEW,ANSWERED|u1': [item('b', 'IN_REVIEW', 2), item('f', 'ANSWERED', 5)],
    'TRANSFERRED,WAITING_CUSTOMER|u1': [item('c', 'TRANSFERRED', 3)],
  }, [req('r1', 'DRAFT'), req('r2', 'REVIEWED'), req('r3', 'COMPLETED')]);
  vi.stubGlobal('fetch', fetch);
  view();
  const table = within(await screen.findByRole('table', { name: '처리할 문의' }));
  await table.findByRole('link', { name: 'ID-a' });
  expect(table.getAllByRole('link').map(x => x.textContent)).toEqual(['ID-a', 'ID-f', 'ID-b']);
  expect(table.getByText('검토 시작')).toBeTruthy();expect(table.getByText('종결 여부 확인')).toBeTruthy();
  expect(table.queryByText('ID-c')).toBeNull();
  // 오늘 할 일 = 새 1 + 내 담당 2 + 급한 개선요청 1(초안은 별도)
  expect(await screen.findByText('4건')).toBeTruthy();
  expect(screen.getByText(/김상담님/)).toBeTruthy();
  expect(screen.getByText('7일 넘은 문의 1건부터 처리하세요')).toBeTruthy();
  expect(screen.getByText('부서 전달')).toBeTruthy();
  expect(screen.queryByText('용어 풀이')).toBeNull();expect(screen.queryByText('다음 할 일')).toBeNull();
  const urls = fetch.mock.calls.map(([url]) => String(url));
  expect(urls.some(url => url.includes('state=IN_REVIEW%2CANSWERED') && url.includes('owner=u1'))).toBe(true);
});

test('목록 조회가 거부되면 이전 값을 보이지 않고 오류를 안내한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(403, errorBody('FORBIDDEN', '권한 없음'))));
  view();
  await screen.findByText('권한이 없습니다');
  expect(screen.queryByRole('table', { name: '처리할 문의' })).toBeNull();
});

// 개발 환경 화면(DevCheck)은 ['tickets', mode]에 쪽수 없는 목록을 저장한다. 그 캐시가 있어도 업무 홈이 깨지면 안 된다.
test('개발 환경 화면이 남긴 문의 목록 캐시와 충돌하지 않는다', async () => {
  setApiMode('live');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['tickets', 'live'], { data: { items: [item('x', 'RECEIVED', 1)], next_cursor: null } });
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [item('a', 'RECEIVED', 10)], next_cursor: null } })));
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/cs']}><Routes><Route path="/cs" element={<CsHome />} /></Routes></MemoryRouter></QueryClientProvider>);
  await screen.findByRole('link', { name: 'ID-a' });
});
