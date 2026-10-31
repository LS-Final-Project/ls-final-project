// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, it, vi } from 'vitest';
const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
const ID = '33333333-3333-4333-8333-333333333333';
const source = readFileSync('public/sw.js', 'utf8');
const item = { id: ID, title: '서버에서 확인한 알림', link_path: `/tft/projects/${ID}`, read_at: null };
function worker(fetcher?: (path: string) => Promise<Response>, windows: any[] = []) {
  const listeners: Record<string, (event: any) => void> = {};
  const state = { owner: A };
  const close = vi.fn(); const ack = vi.fn();
  const show = vi.fn(async () => {}); const open = vi.fn(async () => {});
  const fetch = vi.fn(fetcher ?? (async (path: string) => Response.json({ data: path.endsWith('/me') ? { id: A } : { items: [item] } })));
  runInNewContext(source, { URL, Response, AbortSignal, fetch,
    caches: { open: async () => ({ match: async () => state.owner ? Response.json({ id: state.owner }) : undefined, put: async (_key: string, value: Response) => { state.owner = (await value.json()).id; }, delete: async () => { state.owner = ''; } }) },
    self: { location: { origin: 'https://app.example.invalid' }, addEventListener: (name: string, callback: (event: any) => void) => { listeners[name] = callback; }, skipWaiting: async () => {}, registration: { showNotification: show, getNotifications: async () => [{ close }] }, clients: { claim: async () => {}, get: async () => ({ url: 'https://app.example.invalid/account' }), matchAll: async () => windows, openWindow: open } },
  });
  async function emit(kind: string, payload: unknown) {
    let pending: Promise<unknown> | undefined;
    const event = kind === 'message' ? { data: payload, source: { id: 'app-client' }, ports: [{ postMessage: ack }] } : kind === 'push' ? { data: { json: () => payload } } : { notification: { data: payload, close: vi.fn() } };
    listeners[kind]({ ...event, waitUntil: (promise: Promise<unknown>) => { pending = promise; } });
    await pending;
  }
  return { state, show, open, close, ack, fetch, listeners, emit };
}
it('푸시 payload 제목/URL 대신 현재 수신함에서 확인한 값만 표시한다', async () => {
  const w = worker(); await w.emit('push', { id: ID, title: '믿지 않는 제목', url: 'https://evil.invalid' });
  expect(w.show).toHaveBeenCalledWith(item.title, { tag: `notification:${ID}`, data: { id: ID, ownerId: A } });
  expect(w.listeners.fetch).toBeUndefined();
});
it('현재 사용자가 다르면 이전 계정의 지연 푸시를 표시하지 않는다', async () => {
  const w = worker(async path => Response.json({ data: path.endsWith('/me') ? { id: B } : { items: [item] } }));
  await w.emit('push', { id: ID }); expect(w.show).not.toHaveBeenCalled();
});
it.each(['me', 'list'])('%s 응답 대기 중 로그아웃/계정 변경이면 표시하지 않는다', async boundary => {
  let resolve: ((value: Response) => void) | undefined;
  const w = worker(async path => {
    if ((boundary === 'me') === path.endsWith('/me')) return new Promise<Response>(done => { resolve = done; });
    return Response.json({ data: path.endsWith('/me') ? { id: A } : { items: [item] } });
  });
  const pending = w.emit('push', { id: ID });
  await vi.waitFor(() => expect(resolve).toBeDefined());
  w.state.owner = boundary === 'me' ? '' : B;
  resolve!(Response.json({ data: boundary === 'me' ? { id: A } : { items: [item] } }));
  await pending; expect(w.show).not.toHaveBeenCalled();
});
it('top100에 없는 알림은 생략하고 추가 페이지나 가짜 알림을 만들지 않는다', async () => {
  const w = worker(async path => Response.json({ data: path.endsWith('/me') ? { id: A } : { items: [], next_offset: 100 } }));
  await w.emit('push', { id: ID }); expect(w.show).not.toHaveBeenCalled(); expect(w.fetch).toHaveBeenCalledTimes(2);
});
it('notificationclick은 현재 수신권한을 다시 확인하고 서버의 내부 URL만 연다', async () => {
  const w = worker(); await w.emit('notificationclick', { id: ID, ownerId: A, url: 'https://evil.invalid' });
  expect(w.open).toHaveBeenCalledWith(`https://app.example.invalid${item.link_path}`);
  const denied = worker(async path => Response.json({ data: path.endsWith('/me') ? { id: A } : { items: [] } }));
  await denied.emit('notificationclick', { id: ID, ownerId: A, url: item.link_path }); expect(denied.open).not.toHaveBeenCalled();
});
it('서버 수신함의 URL도 내부 허용 경로가 아니면 열거나 표시하지 않는다', async () => {
  const w = worker(async path => Response.json({ data: path.endsWith('/me') ? { id: A } : { items: [{ ...item, link_path: '//evil.invalid' }] } }));
  await w.emit('push', { id: ID }); await w.emit('notificationclick', { id: ID, ownerId: A });
  expect(w.show).not.toHaveBeenCalled(); expect(w.open).not.toHaveBeenCalled();
});
it('로그아웃 message는 소유자 표시와 이미 표시된 OS 알림을 지운다', async () => {
  const w = worker(); await w.emit('message', { type: 'NOTIFICATION_OWNER', id: null });
  expect(w.state.owner).toBe(''); expect(w.close).toHaveBeenCalledOnce(); expect(w.ack).toHaveBeenCalledWith({ ok: true });
});
it('이전 탭은 현재 세션이 다른 사용자인데 소유자를 다시 묶을 수 없다', async () => {
  const w = worker(async () => Response.json({ data: { id: B } })); w.state.owner = B;
  await w.emit('message', { type: 'NOTIFICATION_OWNER', id: A });
  expect(w.state.owner).toBe(B); expect(w.ack).not.toHaveBeenCalled();
});
it('이미 열린 같은 origin 창을 안전한 URL로 이동하고 focus한다', async () => {
  const focus = vi.fn(async () => {}); const navigate = vi.fn(async () => ({ focus }));
  const w = worker(undefined, [{ url: 'https://app.example.invalid/account', navigate, focus }]);
  await w.emit('notificationclick', { id: ID, ownerId: A });
  expect(navigate).toHaveBeenCalledWith(`https://app.example.invalid${item.link_path}`); expect(focus).toHaveBeenCalledOnce(); expect(w.open).not.toHaveBeenCalled();
});
