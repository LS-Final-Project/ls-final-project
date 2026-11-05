// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { clearPush } from '../notifications/push';
import { setApiMode } from '../api/client';
import { renderApp } from '../test/render';

vi.mock('../notifications/push', async importOriginal => ({
  ...await importOriginal<typeof import('../notifications/push')>(),
  clearPush: vi.fn(), reconcilePush: vi.fn(async () => {}),
}));
const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
const identity = (id: string) => ({ id, tid: id, oid: id, email: 'test@example.invalid', display_name: id === A ? '이전 시연 계정' : '새 시연 계정', roles: ['sales'], dept: null });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); setApiMode('mock'); });

test.each(['select', 'logout'])('OS 정리 실패도 시연 %s 요청을 막지 않고 실패를 안내한다', async action => {
  let active: string | null = A;
  vi.mocked(clearPush).mockRejectedValueOnce(new Error('storage blocked'));
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/dev/accounts')) return Response.json({ data: { items: [identity(A), identity(B)] } });
    if (url.endsWith('/dev/session')) {
      if (init?.method === 'DELETE') { active = null; return new Response(null, { status: 204 }); }
      active = B; return Response.json({ data: identity(B) });
    }
    if (url.endsWith('/me')) return active ? Response.json({ data: identity(active) }) : new Response('{}', { status: 401 });
    if (url.endsWith('/unread-count')) return Response.json({ data: { count: 0 } });
    return Response.json({ data: {} });
  });
  vi.stubGlobal('fetch', fetcher);
  renderApp('/');
  fireEvent.click(await screen.findByRole('button', { name: action === 'select' ? '새 시연 계정' : '선택 해제' }));
  await screen.findByText(/OS 알림 정리를 확인하지 못했습니다/);
  await waitFor(() => expect(fetcher.mock.calls.some(([url, init]) => url.endsWith('/dev/session') && init?.method === (action === 'select' ? 'POST' : 'DELETE'))).toBe(true));
  await waitFor(() => expect(screen.getByTestId('current-user').textContent).toContain(action === 'select' ? '새 시연 계정' : '선택 전'));
});
