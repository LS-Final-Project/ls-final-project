// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { renderAt } from '../test/render';
import PushToggle from './PushToggle';
import { enablePush, pushSupported } from '../notifications/push';
vi.mock('../notifications/push', () => ({ enablePush: vi.fn(async () => {}), clearPush: vi.fn(async () => {}), existingPushWorker: vi.fn(async () => undefined), publicKeyBytes: vi.fn(() => new Uint8Array([4])), pushOwner: vi.fn(() => null), pushSupported: vi.fn(() => true) }));
const A = '11111111-1111-4111-8111-111111111111';
const permission = vi.fn(async () => 'granted');
beforeEach(() => { vi.clearAllMocks(); vi.mocked(pushSupported).mockReturnValue(true); vi.stubGlobal('Notification', { permission: 'default', requestPermission: permission }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('페이지 진입에는 권한을 묻지 않으며 명시 클릭에서만 요청한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ data: { public_key: 'test-only-public-key' } })));
  renderAt('/account', '/account', <PushToggle actorId={A} />);
  await waitFor(() => expect((screen.getByRole('button', { name: 'OS 알림 켜기' }) as HTMLButtonElement).disabled).toBe(false));
  expect(permission).not.toHaveBeenCalled(); expect(enablePush).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'OS 알림 켜기' }));
  await screen.findByText('이 계정의 OS 알림 켜짐'); expect(permission).toHaveBeenCalledOnce(); expect(enablePush).toHaveBeenCalledWith(A, 'test-only-public-key', expect.any(AbortSignal));
});
it.each([404, 200])('공개키 OFF/없음(%s)이면 토글을 비활성화하고 권한을 묻지 않는다', async status => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ data: { public_key: '' } }, { status })));
  renderAt('/account', '/account', <PushToggle actorId={A} />);
  await screen.findByText(status === 404 ? '서버의 OS 알림 기능이 꺼져 있습니다.' : '공개키를 확인할 수 없어 OS 알림을 켤 수 없습니다.');
  expect((screen.getByRole('button', { name: 'OS 알림 켜기' }) as HTMLButtonElement).disabled).toBe(true); expect(permission).not.toHaveBeenCalled();
});
it('denied는 다시 권한 요청하지 않고 사이트 설정 안내를 표시한다', async () => {
  vi.stubGlobal('Notification', { permission: 'denied', requestPermission: permission });
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ data: { public_key: 'test-only' } })));
  renderAt('/account', '/account', <PushToggle actorId={A} />);
  await screen.findByText(/브라우저의 사이트 설정에서 변경하세요/);
  expect((screen.getByRole('button', { name: 'OS 알림 켜기' }) as HTMLButtonElement).disabled).toBe(true); expect(permission).not.toHaveBeenCalled();
});
it('미지원은 앱 내 알림으로 안내하고 구독/권한을 요청하지 않는다', async () => {
  vi.mocked(pushSupported).mockReturnValue(false); const f = vi.fn(); vi.stubGlobal('fetch', f);
  renderAt('/account', '/account', <PushToggle actorId={A} />);
  await screen.findByText(/이 환경은 Web Push를 지원하지 않습니다/); expect(f).not.toHaveBeenCalled(); expect(permission).not.toHaveBeenCalled();
});
it('권한 응답 대기 중 계정 화면을 떠나면 이전 사용자의 구독을 만들지 않는다', async () => {
  let resolve: ((value: string) => void) | undefined;
  permission.mockImplementationOnce(() => new Promise<string>(done => { resolve = done; }));
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ data: { public_key: 'test-only' } })));
  const view = renderAt('/account', '/account', <PushToggle actorId={A} />);
  await waitFor(() => expect((screen.getByRole('button', { name: 'OS 알림 켜기' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'OS 알림 켜기' })); view.unmount(); resolve!('granted');
  await Promise.resolve(); expect(enablePush).not.toHaveBeenCalled();
});

it.each([404, 503])('이전 공개키가 캐시에 있어도 최신 %s 응답이면 켜기를 차단한다', async status => {
  let healthy = true;
  vi.stubGlobal('fetch', vi.fn(async () => healthy ? Response.json({ data: { public_key: 'test-only-public-key' } }) : Response.json({ error: { message: '설정 조회 실패' } }, { status })));
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={cache}><PushToggle actorId={A} /></QueryClientProvider>);
  const button = screen.getByRole('button', { name: 'OS 알림 켜기' });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  healthy = false;
  await cache.invalidateQueries({ queryKey: ['notification-push-key', A] });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(true));
  fireEvent.click(button);
  expect(permission).not.toHaveBeenCalled(); expect(enablePush).not.toHaveBeenCalled();
});
