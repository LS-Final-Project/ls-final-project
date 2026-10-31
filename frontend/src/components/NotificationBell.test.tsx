// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, useQueryClient, focusManager } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import NotificationBell from './NotificationBell';
const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
const ID = '33333333-3333-4333-8333-333333333333';
const item = { id: ID, title: '실제 API 알림 제목', kind: 'rfp.done', link_path: `/tft/projects/${ID}`, created_at: '2026-10-31T01:00:00Z', read_at: null };
const data = (data: unknown) => Response.json({ data });
function Probe() { const location = useLocation(); const cache = useQueryClient(); return <><output aria-label="현재 경로">{location.pathname}</output><button onClick={() => cache.setQueryData(['me'], { data: { id: B } })}>사용자 전환</button><NotificationBell /></>; }
function view() { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); return render(<QueryClientProvider client={client}><MemoryRouter><Probe /></MemoryRouter></QueryClientProvider>); }
function serve(readStatus = 204) {
  return vi.fn(async (url: string) => url.endsWith('/me') ? new Response(JSON.stringify({ data: { id: A } }), { headers: { 'X-CSRF-Token': 'csrf' } }) : url.endsWith('/unread-count') ? data({ count: 1 }) : url.endsWith('/read') ? new Response(null, { status: readStatus }) : data({ items: [item], next_offset: null }));
}
afterEach(() => { cleanup(); vi.useRealTimers(); focusManager.setFocused(true); vi.unstubAllGlobals(); localStorage.clear(); });
it('수신함을 열어 실제 알림을 읽고 확인된 내부 경로로 이동한다', async () => {
  const f = serve(); vi.stubGlobal('fetch', f); view();
  fireEvent.click(await screen.findByRole('button', { name: /알림, 안 읽음 1건/ }));
  fireEvent.click(await screen.findByRole('button', { name: item.title }));
  await waitFor(() => expect(screen.getByLabelText('현재 경로').textContent).toBe(item.link_path));
  expect(f.mock.calls.some(([url]) => url === `/api/v1/platform/notifications/${ID}/read`)).toBe(true);
});
it('읽음 실패면 자동 이동하지 않고 명시적인 별도 열기만 제공한다', async () => {
  vi.stubGlobal('fetch', serve(503)); view();
  fireEvent.click(await screen.findByRole('button', { name: /알림/ }));
  fireEvent.click(await screen.findByRole('button', { name: item.title }));
  const fallback = await screen.findByRole('button', { name: '읽음 처리 없이 열기' });
  expect(screen.getByLabelText('현재 경로').textContent).toBe('/');
  fireEvent.click(fallback);
  expect(screen.getByLabelText('현재 경로').textContent).toBe(item.link_path);
});
it.each([401, 403])('%s 읽음 실패에는 별도 이동도 제공하지 않는다', async status => {
  vi.stubGlobal('fetch', serve(status)); view();
  fireEvent.click(await screen.findByRole('button', { name: /알림/ }));
  fireEvent.click(await screen.findByRole('button', { name: item.title }));
  await screen.findByRole('alert'); expect(screen.queryByRole('button', { name: '읽음 처리 없이 열기' })).toBeNull();
});
it('사용자 전환 시 이전 목록과 열린 패널을 재사용하지 않는다', async () => {
  const f = serve(); vi.stubGlobal('fetch', f); view();
  fireEvent.click(await screen.findByRole('button', { name: /알림/ }));
  await screen.findByText(item.title);
  fireEvent.click(screen.getByRole('button', { name: '사용자 전환' }));
  await waitFor(() => expect(screen.queryByText(item.title)).toBeNull());
  expect(screen.queryByText('내 알림')).toBeNull();
});
it('30초 폴링은 진행 중 요청을 중복하지 않고 오류 뒤 멈춘다', async () => {
  vi.useFakeTimers();
  let reads = 0; let resolve: ((response: Response) => void) | undefined;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/me')) return data({ id: A });
    ++reads; if (reads === 1) return new Promise<Response>(done => { resolve = done; });
    return new Response('{}', { status: 500 });
  }));
  view();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  await act(async () => { await vi.advanceTimersByTimeAsync(90000); });
  expect(reads).toBe(1);
  await act(async () => { resolve!(data({ count: 1 })); await vi.advanceTimersByTimeAsync(1); });
  await act(async () => { await vi.advanceTimersByTimeAsync(30001); });
  expect(reads).toBe(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(90000); });
  expect(reads).toBe(2);
});

it('숨김 탭에서는 폴링하지 않고 보이는 탭으로 돌아오면 최신 수를 조회한다', async () => {
  vi.useFakeTimers();
  let reads = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/me') ? data({ id: A }) : data({ count: ++reads })));
  view();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(reads).toBe(1);
  focusManager.setFocused(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(90001); });
  expect(reads).toBe(1);
  await act(async () => { focusManager.setFocused(true); await vi.advanceTimersByTimeAsync(1); });
  expect(reads).toBe(2);
  expect(screen.getByRole('button', { name: '알림, 안 읽음 2건' })).toBeTruthy();
});

it.each(['/account', '/cs'])('%s의 배타적인 Layout/LsShell은 종을 하나만 그린다', async path => {
  const { renderApp } = await import('../test/render');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/me') ? data({ id: A, display_name: '알림 사용자', roles: ['cs_agent'], dept: null }) : url.endsWith('/unread-count') ? data({ count: 0 }) : data({ items: [] })));
  renderApp(path);
  await screen.findByRole('button', { name: '알림, 안 읽음 0건' });
  expect(screen.getAllByRole('button', { name: /^알림/ })).toHaveLength(1);
});
