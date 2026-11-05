import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import MailAccount from './MailAccount';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

// 운영 빌드 시험에서 업무 UI 설정을 켠다(설정 값은 모듈을 읽을 때 정해진다). 개발 모드 시험은 접근 확인을 하지 않아 영향이 없다.
vi.mock('../components/BusinessAccess', async importOriginal => {
  const actual = await importOriginal<typeof import('../components/BusinessAccess')>();
  return { ...actual, useBusinessAccess: (business: Parameters<typeof actual.useBusinessAccess>[0], operational?: boolean) => actual.useBusinessAccess(business, operational, true) };
});
const csAgent = () => jsonResponse(200, { data: { id: 'u1', roles: ['cs_agent'] } });

const connection = (over: Record<string, unknown>) => ({ mailbox_id: '99999999-9999-4999-8999-999999999999', state: 'connected', revision: 1, updated_at: '2026-10-29T08:00:00Z', reconnect_required: false, reconnect_reason: null, reconnect_required_at: null, ...over });
const show = (navigate = vi.fn()) => { setApiMode('live'); renderAt('/mail/account', '/mail/account', <MailAccount navigate={navigate} />); return navigate; };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); setApiMode('mock'); clearSessionCsrfToken(); });

test('연결 상태와 다시 연결 필요 계정을 사유와 함께 보인다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [
    connection({}), connection({ mailbox_id: '88888888-8888-4888-8888-888888888888', reconnect_required: true, reconnect_reason: 'invalid_grant', updated_at: '2026-10-28T08:40:00Z' }),
  ] } })));
  show();
  const table = await screen.findByRole('table', { name: '메일 계정' });
  const rows = within(table).getAllByRole('row').slice(1);
  expect(rows[0].textContent).toContain('연결됨');
  expect(rows[1].textContent).toContain('끊김');
  expect(rows[1].textContent).toContain('필요');
  expect(rows[1].textContent).toContain('Microsoft가 저장된 연결 승인을 더 이상 받지 않음');
  expect(screen.getByText(/내 계정/).textContent).toMatch(/2건, 다시 연결 필요 1/);
});

test('세션 확인 정보가 없으면 다시 연결을 누를 수 없다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [connection({ reconnect_required: true, reconnect_reason: 'login_required' })] } })));
  show();
  await screen.findByRole('table', { name: '메일 계정' });
  expect(screen.getByText(/세션 확인 정보가 없어/)).toBeTruthy();
  for (const button of screen.getAllByRole('button', { name: '다시 연결' })) expect((button as HTMLButtonElement).disabled).toBe(true);
});

test('다시 연결이 필요한 계정이 없으면 단추가 잠기고 안내한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).endsWith('/platform/me') ? csAgent() : jsonResponse(200, { data: { items: [connection({})] } })));
  show();
  await screen.findByRole('table', { name: '메일 계정' });
  expect(screen.getByText('다시 연결이 필요한 계정이 없습니다')).toBeTruthy();
  expect((screen.getByRole('button', { name: '다시 연결' }) as HTMLButtonElement).disabled).toBe(true);
});

test('사서함 연결이 꺼진 배포(404)는 안내만 보이고 오류 화면이 아니다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(404, errorBody('NOT_FOUND', '없음'))));
  show();
  expect(await screen.findByText('사서함 연결이 이 배포에서 활성화되지 않았습니다.')).toBeTruthy();
  expect(screen.queryByRole('table', { name: '메일 계정' })).toBeNull();
});

test('목록 오류는 오류 화면으로, 가져오기 기록은 없다고 밝힌다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(403, errorBody('FORBIDDEN', '권한이 없습니다.'))));
  show();
  expect(await screen.findByText(/권한이 없습니다/)).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [] } })));
  show();
  await screen.findByText('연결된 사서함이 없습니다.');
  expect(screen.getByText('마지막 가져온 기록을 주는 API가 아직 없음')).toBeTruthy();
});

test('다시 연결을 누르면 고정 authority의 동의 주소로 이동하고, 다른 주소는 거부한다', async () => {
  const consent = 'https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?x=1';
  let reply = consent;
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: {} }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' } });
    if (path.endsWith('/auth/mailbox/start')) return jsonResponse(200, { data: { authorization_url: reply } });
    return jsonResponse(200, { data: { items: [connection({ reconnect_required: true, reconnect_reason: 'consent_required' })] } });
  }));
  setApiMode('live');
  const { request } = await import('../api/client');
  await request('/api/v1/platform/me');
  const navigate = show();
  await screen.findByRole('table', { name: '메일 계정' });
  fireEvent.click(screen.getAllByRole('button', { name: '다시 연결' })[1]);
  await waitFor(() => expect(navigate).toHaveBeenCalledWith(consent));
  reply = 'https://evil.example/authorize';
  fireEvent.click(screen.getAllByRole('button', { name: '다시 연결' })[1]);
  expect(await screen.findByText(/연결 주소를 확인할 수 없습니다/)).toBeTruthy();
  expect(navigate).toHaveBeenCalledTimes(1);
});

test('운영 빌드(개발 모드 아님)에서도 연결 상태를 읽어 보인다', async () => {
  vi.stubEnv('DEV', false);
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).endsWith('/platform/me') ? csAgent() : jsonResponse(200, { data: { items: [connection({})] } })));
  show();
  expect(await screen.findByRole('table', { name: '메일 계정' })).toBeTruthy();
  expect(screen.queryByText(/운영 인증 중계와 권한 검증 후 제공됩니다/)).toBeNull();
});
