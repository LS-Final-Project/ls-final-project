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

test('업무 개요는 받아 온 목록을 상태별로 세고 종결 시각이 없는 칸은 비워 둔다', async () => {
  const items = [item('a', 'RECEIVED', 10), item('b', 'IN_REVIEW', 2), item('c', 'TRANSFERRED', 3), item('d', 'WAITING_CUSTOMER', 4), item('e', 'CLOSED', 1), item('f', 'ANSWERED', 5)];
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items, next_cursor: null } })));
  const { container } = view();
  await screen.findByRole('link', { name: 'ID-a' });
  expect([...container.querySelectorAll('.kv-steps span')].map(x => x.textContent?.replace(/\s+/g, ' ').trim()))
    .toEqual(['접수 1', '검토중 1', '부서 검토 1', '고객 회신 대기 1', '오늘 종결 -']);
  expect(screen.getByText(/받아 온 6건 기준/)).toBeTruthy();
  expect(container.querySelector('.kv-next')?.textContent).toMatch(/접수 1건 검토 시작, 7일 이상 미처리 1/);
});

test('처리할 문의는 부서·고객 답을 기다리는 건과 종결을 빼고 경과일 내림차순으로 보인다', async () => {
  const items = [item('b', 'IN_REVIEW', 2), item('a', 'RECEIVED', 10), item('c', 'TRANSFERRED', 3), item('e', 'CLOSED', 1), item('f', 'ANSWERED', 5)];
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items, next_cursor: null } })));
  view();
  const table = within(await screen.findByRole('table', { name: '처리할 문의' }));
  await table.findByRole('link', { name: 'ID-a' });
  expect(table.getAllByRole('link').map(x => x.textContent)).toEqual(['ID-a', 'ID-f', 'ID-b']);
  expect(table.getByText('검토 시작')).toBeTruthy();
  expect(table.getByText('종결 여부 확인')).toBeTruthy();
  expect(screen.getByText(/담당 구분 API가 없어 팀 전체/)).toBeTruthy();
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
