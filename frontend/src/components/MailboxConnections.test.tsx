import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import MailboxConnections from './MailboxConnections';
import { me } from '../api/platform';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const LIST = '/api/v1/platform/auth/mailbox';
const ID = '11111111-1111-4111-8111-111111111111';
const AUTH_URL = 'https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?state=synthetic';
const item = (state: string, revision: number) => ({ mailbox_id: ID, state, revision, updated_at: '2026-10-28T00:00:00Z' });
const listed = (...items: unknown[]) => jsonResponse(200, { data: { items }, meta: { request_id: '11111111-1111-4111-8111-111111111100' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); clearSessionCsrfToken(); setApiMode('mock'); });

type Route = (url: string, init?: RequestInit) => Response | Promise<Response>;
/**
 * /me로 세션 CSRF를 받은 뒤 사서함 구역을 그린다.
 * Args: route는 /me 외 요청의 응답, navigate는 동의 주소 이동 대체, csrf는 /me 응답에 CSRF 헤더를 줄지 여부다.
 * Returns: 모든 요청을 기록한 fetch 스텁.
 */
async function view(route: Route, navigate = vi.fn(), csrf = true) {
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => String(url) === '/api/v1/platform/me'
    ? new Response(JSON.stringify({ data: {} }), { status: 200, headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': 'session-bound-test' } : {}) } })
    : route(String(url), init));
  vi.stubGlobal('fetch', fetchMock);
  setApiMode('live');
  await me();
  renderAt('/account', '/account', <MailboxConnections navigate={navigate} />);
  return fetchMock;
}
const posts = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST');

test('현재 소유자의 연결을 상태 문자열로 보여주고 비밀을 그리지 않는다', async () => {
  await view(() => listed(item('connected', 1), { ...item('disconnected', 2), mailbox_id: '22222222-2222-4222-8222-222222222222' }));
  expect(await screen.findByText(ID)).toBeTruthy();
  expect(screen.getByText('연결됨')).toBeTruthy();
  expect(screen.getByText('해제됨')).toBeTruthy();
  expect(screen.getAllByRole('button', { name: '해제' })).toHaveLength(1);
  expect(document.body.textContent).not.toContain('session-bound-test');
});

test('사서함 연결이 꺼진 배포의 404는 안내 한 줄만 보여준다', async () => {
  await view(() => jsonResponse(404, errorBody('NOT_FOUND', 'missing')));
  expect(await screen.findByText('사서함 연결이 이 배포에서 활성화되지 않았습니다.')).toBeTruthy();
  expect(screen.queryByRole('button')).toBeNull();
});

test('예상 밖 목록 본문은 화면을 깨지 않고 오류로 보여준다', async () => {
  await view(() => jsonResponse(200, { data: { id: 'not-a-list' } }));
  expect(await screen.findByText('사서함 연결 목록을 확인할 수 없습니다.')).toBeTruthy();
  expect(screen.queryByRole('table')).toBeNull();
});

test('해제는 확인 뒤에만 세션 CSRF로 보내고 목록을 다시 읽는다', async () => {
  let state = 'connected';
  const fetchMock = await view((url, init) => {
    if (url.endsWith('/disconnect')) { state = 'disconnected'; return new Response(null, { status: 204 }); }
    return listed(item(state, state === 'connected' ? 1 : 2));
  });
  fireEvent.click(await screen.findByRole('button', { name: '해제' }));
  expect(posts(fetchMock)).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '취소' }));
  expect(posts(fetchMock)).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '해제' }));
  fireEvent.click(screen.getByRole('button', { name: '해제 확인' }));
  await screen.findByText('해제됨');
  const [url, init] = posts(fetchMock)[0];
  expect(String(url)).toBe(`${LIST}/${ID}/disconnect`);
  expect((init?.headers as Record<string, string>)['X-CSRF-Token']).toBe('session-bound-test');
  expect(fetchMock.mock.calls.filter(([called]) => String(called) === LIST)).toHaveLength(2);
});

test('연결은 고정 Microsoft authority 주소로만 이동한다', async () => {
  const navigate = vi.fn();
  const fetchMock = await view(url => url.endsWith('/start') ? jsonResponse(200, { data: { authorization_url: AUTH_URL } }) : listed(), navigate);
  fireEvent.click(await screen.findByRole('button', { name: '사서함 연결' }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith(AUTH_URL));
  expect((posts(fetchMock)[0][1]?.headers as Record<string, string>)['X-CSRF-Token']).toBe('session-bound-test');
});

test('다른 곳을 가리키는 연결 주소로는 이동하지 않는다', async () => {
  const navigate = vi.fn();
  await view(url => url.endsWith('/start') ? jsonResponse(200, { data: { authorization_url: 'https://evil.example.invalid/' } }) : listed(), navigate);
  fireEvent.click(await screen.findByRole('button', { name: '사서함 연결' }));
  expect(await screen.findByText('사서함 연결 주소를 확인할 수 없습니다.')).toBeTruthy();
  expect(navigate).not.toHaveBeenCalled();
});

test('세션 CSRF가 없으면 연결·해제를 보내지 않는다', async () => {
  const fetchMock = await view(() => listed(item('connected', 1)), vi.fn(), false);
  expect(((await screen.findByRole('button', { name: '해제' })) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '사서함 연결' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '사서함 연결' }));
  expect(posts(fetchMock)).toHaveLength(0);
});

test('해제 뒤 목록 재조회가 실패해도 성공 안내와 기존 표를 유지한다', async () => {
  let reads = 0;
  await view((url) => {
    if (url.endsWith('/disconnect')) return new Response(null, { status: 204 });
    return ++reads === 1 ? listed(item('connected', 1)) : jsonResponse(503, errorBody('UNAVAILABLE', 'list down'));
  });
  fireEvent.click(await screen.findByRole('button', { name: '해제' }));
  fireEvent.click(screen.getByRole('button', { name: '해제 확인' }));
  expect(await screen.findByText('사서함 연결을 해제했습니다.')).toBeTruthy();
  expect(await screen.findByText('사서함 연결 목록을 다시 불러오지 못했습니다.')).toBeTruthy();
  expect(screen.getByText(ID)).toBeTruthy();
  expect(screen.getByRole('table')).toBeTruthy();
});
