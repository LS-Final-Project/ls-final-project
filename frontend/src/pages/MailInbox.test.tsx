import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import MailInbox from './MailInbox';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import type { MailTriage, ReplyReview } from '../api/mail';
import { errorBody, jsonResponse, renderAt } from '../test/render';

// 운영 빌드 시험에서 업무 UI 설정을 켠다(설정 값은 모듈을 읽을 때 정해진다). 개발 모드 시험은 접근 확인을 하지 않아 영향이 없다.
vi.mock('../components/BusinessAccess', async importOriginal => {
  const actual = await importOriginal<typeof import('../components/BusinessAccess')>();
  return { ...actual, useBusinessAccess: (business: Parameters<typeof actual.useBusinessAccess>[0], operational?: boolean) => actual.useBusinessAccess(business, operational, true) };
});

const review = (over: Partial<ReplyReview>): ReplyReview => ({
  message_id: '44444444-4444-4444-8444-444444444444', received_at: '2026-10-29T08:41:00Z', created_at: '2026-10-29T08:42:00Z', reason: 'AMBIGUOUS',
  candidates: [{ owner_service: 'cs', business_ref: '66666666-6666-4666-8666-666666666666', method: 'HEADER' }, { owner_service: 'platform', business_ref: 'THREAD-9', method: 'THREAD' }], ...over,
});
const triage = (over: Partial<MailTriage> = {}): MailTriage => ({
  message_id: '44444444-4444-4444-8444-444444444444', version: 2, latest_version: 3, is_latest: false, mail_type: 'CS', needs_review: true, attachments_pending: true,
  evidence: [{ source: 'subject', start: 0, end: 3 }, { source: 'reply_header', start: null, end: null }], link_suggestion: 'EXISTING_THREAD', provenance_kind: 'ai_draft', reason: null,
  created_at: '2026-10-29T08:42:00Z', ...over,
});
const serve = (items: ReplyReview[], tri: MailTriage | Response = triage()) => vi.fn(async (url: RequestInfo | URL) => String(url).includes('/triage')
  ? (tri instanceof Response ? tri : jsonResponse(200, { data: tri })) : jsonResponse(200, { data: { items } }));
const show = () => { setApiMode('live'); return renderAt('/mail/inbox', '/mail/inbox', <MailInbox />); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); setApiMode('mock'); clearSessionCsrfToken(); });

test('검토 대기 답장을 사유·후보 수와 함께 최신순으로 보이고 선택한 메일의 분류를 읽는다', async () => {
  const older = review({ message_id: '55555555-5555-4555-8555-555555555555', received_at: '2026-10-28T08:00:00Z', reason: 'UNMATCHED_REPLY', candidates: [] });
  const fetch = serve([older, review({})]);
  vi.stubGlobal('fetch', fetch);
  show();
  const table = await screen.findByRole('table', { name: '검토 대기 메일' });
  const rows = within(table).getAllByRole('row').slice(1);
  expect(rows[0].textContent).toContain('후보가 여럿');
  expect(rows[0].textContent).toContain('2건');
  expect(rows[1].textContent).toContain('맞는 후보 없음');
  expect(await screen.findByText('C/S 문의')).toBeTruthy();
  expect(screen.getByText('기존 건에 이어 붙이기')).toBeTruthy();
  expect(screen.getByText(/제목 0~3자, 답장 헤더/)).toBeTruthy();
  expect(screen.getByText(/\(최신 3\)/)).toBeTruthy();
  expect(screen.getByText('첨부 확인이 끝나지 않았습니다')).toBeTruthy();
  expect(fetch.mock.calls.some(call => String(call[0]).endsWith('/mail/messages/44444444-4444-4444-8444-444444444444/triage'))).toBe(true);
});

test('다른 메일을 고르면 그 메일의 분류를 다시 읽고 후보 표를 바꾼다', async () => {
  const other = review({ message_id: '77777777-7777-4777-8777-777777777777', received_at: '2026-10-28T08:00:00Z', candidates: [] });
  const fetch = serve([review({}), other]);
  vi.stubGlobal('fetch', fetch);
  show();
  await screen.findByRole('table', { name: '연결 후보' });
  fireEvent.click(screen.getByRole('button', { name: /77777777/ }));
  await screen.findByText('시스템이 찾은 후보가 없습니다');
  expect(screen.queryByRole('table', { name: '연결 후보' })).toBeNull();
  expect(fetch.mock.calls.some(call => String(call[0]).includes('/77777777-7777-4777-8777-777777777777/triage'))).toBe(true);
});

test('검토 사유로 거르고 일자 범위가 거꾸로면 표를 비운다', async () => {
  vi.stubGlobal('fetch', serve([review({}), review({ message_id: '88888888-8888-4888-8888-888888888888', reason: 'CONFLICT', received_at: '2026-10-27T08:00:00Z' })]));
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  fireEvent.change(screen.getByLabelText('검토 사유'), { target: { value: 'CONFLICT' } });
  await waitFor(() => expect(within(screen.getByRole('table', { name: '검토 대기 메일' })).getAllByRole('row').length).toBe(2));
  fireEvent.change(screen.getByLabelText('시작일'), { target: { value: '2026-11-02' } });
  fireEvent.change(screen.getByLabelText('종료일'), { target: { value: '2026-11-01' } });
  expect(screen.getByRole('alert').textContent).toContain('종료일은 시작일보다 빠를 수 없습니다');
  expect(screen.getByText('검토할 메일이 없습니다.')).toBeTruthy();
});

test('연결은 후보를 고르기 전에는 눌러지지 않는다', async () => {
  vi.stubGlobal('fetch', serve([review({})]));
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  expect((screen.getByRole('button', { name: '연결' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(await screen.findByLabelText('66666666 선택'));
  expect((screen.getByRole('button', { name: '연결' }) as HTMLButtonElement).disabled).toBe(false);
});

test('목록 오류와 예상 밖 본문, 분류 오류를 각각 보인다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(403, errorBody('FORBIDDEN', '권한이 없습니다.'))));
  show();
  expect(await screen.findByText(/권한이 없습니다/)).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [{ message_id: 'x' }] } })));
  show();
  expect(await screen.findByText(/검토 대기 메일 목록을 확인할 수 없습니다/)).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', serve([review({})], jsonResponse(404, errorBody('NOT_FOUND', '분류가 없습니다.'))));
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  expect(await screen.findByText('찾을 수 없습니다')).toBeTruthy();
});

test('모의 응답 모드는 합성 예시임을 알린다', async () => {
  setApiMode('mock');
  renderAt('/mail/inbox', '/mail/inbox', <MailInbox />);
  expect(await screen.findByText(/합성 예시이며 실제 메일이 아닙니다/)).toBeTruthy();
  expect(await screen.findByText('C/S 문의')).toBeTruthy();
});

async function withSession() {
  const { request } = await import('../api/client');
  await request('/api/v1/platform/me');
}
const latest = () => triage({ is_latest: true, latest_version: 2, attachments_pending: false });
const serveWrite = (post: (init: RequestInit) => Response) => vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
  const path = String(url);
  if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: {} }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' } });
  if (path.includes('/triage')) return init?.method === 'POST' ? post(init) : jsonResponse(200, { data: latest() });
  return jsonResponse(200, { data: { items: [review({})] } });
});
const openDialog = async () => {
  show();
  const button = await screen.findByRole('button', { name: '다시 분류' });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
  return screen.findByRole('dialog');
};

test('다시 분류는 읽은 판본과 바꾼 값으로 저장하고 새 판본 안내를 보인다', async () => {
  const calls: RequestInit[] = [];
  vi.stubGlobal('fetch', serveWrite(init => { calls.push(init); return jsonResponse(200, { data: { ...latest(), version: 3, latest_version: 3, provenance_kind: 'human_input' } }); }));
  setApiMode('live');
  await withSession();
  const dialog = await openDialog();
  fireEvent.change(within(dialog).getByLabelText('유형'), { target: { value: 'OTHER' } });
  fireEvent.change(within(dialog).getByLabelText('연결 추천'), { target: { value: '' } });
  fireEvent.change(within(dialog).getByLabelText('사유'), { target: { value: '  광고 메일이라 기타로 바꿈  ' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '저장' }));
  expect(await screen.findByText('분류를 새 판본으로 저장했습니다.')).toBeTruthy();
  expect(calls).toHaveLength(1);
  expect((calls[0].headers as Record<string, string>)['X-CSRF-Token']).toBe('tok');
  expect(JSON.parse(String(calls[0].body))).toEqual({ version: 2, mail_type: 'OTHER', reason: '광고 메일이라 기타로 바꿈', link_suggestion: null, needs_review: true });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('다른 판본이 먼저 저장돼 409가 나면 안내하고 최신 분류를 다시 읽는다', async () => {
  const fetch = serveWrite(() => jsonResponse(409, errorBody('MAIL_TRIAGE_VERSION_CONFLICT', '판본 충돌')));
  vi.stubGlobal('fetch', fetch);
  setApiMode('live');
  await withSession();
  const dialog = await openDialog();
  fireEvent.change(within(dialog).getByLabelText('사유'), { target: { value: '사유' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '저장' }));
  expect((await screen.findByRole('alert')).textContent).toContain('다른 판본이 먼저 저장됐습니다');
  const reads = () => fetch.mock.calls.filter(call => String(call[0]).includes('/triage') && (call[1] as RequestInit | undefined)?.method !== 'POST').length;
  await waitFor(() => expect(reads()).toBeGreaterThanOrEqual(2));
});

test('최신 판본이 아니면 다시 분류를 열 수 없고, 사유가 비면 저장 요청을 보내지 않는다', async () => {
  vi.stubGlobal('fetch', serve([review({})]));
  show();
  await screen.findByText('C/S 문의');
  expect((screen.getByRole('button', { name: '다시 분류' }) as HTMLButtonElement).disabled).toBe(true);
  cleanup();
  const fetch = serveWrite(() => jsonResponse(200, { data: latest() }));
  vi.stubGlobal('fetch', fetch);
  setApiMode('live');
  await withSession();
  const dialog = await openDialog();
  fireEvent.click(within(dialog).getByRole('button', { name: '저장' }));
  expect(fetch.mock.calls.some(call => (call[1] as RequestInit | undefined)?.method === 'POST')).toBe(false);
});

test('세션 확인 정보가 없으면 저장하지 않고 안내한다', async () => {
  const fetch = vi.fn(async (url: RequestInfo | URL, _init?: RequestInit) => String(url).includes('/triage') ? jsonResponse(200, { data: latest() }) : jsonResponse(200, { data: { items: [review({})] } }));
  vi.stubGlobal('fetch', fetch);
  const dialog = await openDialog();
  fireEvent.change(within(dialog).getByLabelText('사유'), { target: { value: '사유' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '저장' }));
  expect((await screen.findByRole('alert')).textContent).toContain('세션 확인 정보가 없습니다');
  expect(fetch.mock.calls.some(call => (call[1] as RequestInit | undefined)?.method === 'POST')).toBe(false);
});

type MailPost = (init: RequestInit, path: string) => Response;
const serveMail = (post: MailPost) => vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
  const path = String(url);
  if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', roles: ['cs_agent'] } }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' } });
  if (init?.method === 'POST') return post(init, path);
  if (path.includes('/triage')) return jsonResponse(200, { data: triage() });
  return jsonResponse(200, { data: { items: [review({})] } });
});
const posted = (calls: { path: string; body: unknown; token: string }[]): MailPost => (init, path) => { calls.push({ path, body: JSON.parse(String(init.body)), token: (init.headers as Record<string, string>)['X-CSRF-Token'] }); return jsonResponse(200, { data: { message_id: 'm', outcome: 'LINKED', candidates: [], link: null, reason: null } }); };
const MESSAGE = '44444444-4444-4444-8444-444444444444';

test('후보를 골라 사유와 함께 연결하고, 사유가 비면 보내지 않는다', async () => {
  const calls: { path: string; body: unknown; token: string }[] = [];
  const fetch = serveMail(posted(calls));
  vi.stubGlobal('fetch', fetch);
  setApiMode('live');
  await withSession();
  show();
  fireEvent.click(await screen.findByLabelText('66666666 선택'));
  fireEvent.click(screen.getByRole('button', { name: '연결' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: '연결' }));
  expect(calls).toHaveLength(0);
  fireEvent.change(within(dialog).getByLabelText('연결 사유'), { target: { value: '  답장 헤더가 가리키는 문의  ' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '연결' }));
  expect(await screen.findByText('답장을 선택한 문의에 연결했습니다.')).toBeTruthy();
  expect(calls).toEqual([{ path: `/api/v1/platform/mail/reply-reviews/${MESSAGE}/link`, token: 'tok', body: { owner_service: 'cs', business_ref: '66666666-6666-4666-8666-666666666666', note: '답장 헤더가 가리키는 문의' } }]);
  expect(fetch.mock.calls.filter(call => !(call[1] as RequestInit | undefined)?.method && String(call[0]).endsWith('/reply-reviews')).length).toBeGreaterThanOrEqual(2);
});

test('무시는 사유를 기록으로 보낸다', async () => {
  const calls: { path: string; body: unknown; token: string }[] = [];
  vi.stubGlobal('fetch', serveMail(posted(calls)));
  setApiMode('live');
  await withSession();
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  fireEvent.click(screen.getByRole('button', { name: '무시' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('무시 사유'), { target: { value: '광고 메일' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '무시' }));
  expect(await screen.findByText('업무 회신이 아니라고 닫았습니다.')).toBeTruthy();
  expect(calls).toEqual([{ path: `/api/v1/platform/mail/reply-reviews/${MESSAGE}/dismiss`, token: 'tok', body: { note: '광고 메일' } }]);
});

test('서버가 주는 보낸 사람·제목·첨부 여부를 열로 보이고, 없으면 대시와 제목 없음으로 보인다', async () => {
  vi.stubGlobal('fetch', serve([
    review({ from_address: 'buyer@example.com', subject: 'Re: panel frozen', has_attachments: true }),
    review({ message_id: '55555555-5555-4555-8555-555555555555', received_at: '2026-10-28T08:00:00Z', from_address: null, subject: null, has_attachments: false, candidates: [] }),
    review({ message_id: '66666666-6666-4666-8666-666666666667', received_at: '2026-10-27T08:00:00Z', candidates: [] }),
  ]));
  show();
  const table = await screen.findByRole('table', { name: '검토 대기 메일' });
  const rows = within(table).getAllByRole('row');
  expect(rows[0].textContent).toContain('보낸 사람');
  expect(rows[0].textContent).toContain('제목');
  expect(rows[0].textContent).toContain('첨부');
  expect(rows[1].textContent).toContain('buyer@example.com');
  expect(rows[1].textContent).toContain('Re: panel frozen');
  expect(rows[1].textContent).toContain('있음');
  expect(rows[2].textContent).toContain('(제목 없음) 55555555');
  expect(rows[2].textContent).toContain('없음');
  // 서버가 이 칸을 주지 않은 응답은 첨부를 모른다고 보인다.
  expect(rows[3].textContent).toContain('(제목 없음) 66666666');
  expect(within(rows[3]).getAllByText('-').length).toBeGreaterThanOrEqual(2);
});

test('운영 빌드(개발 모드 아님)에서도 목록을 읽고 처리 호출을 보낸다', async () => {
  vi.stubEnv('DEV', false);
  const calls: { path: string; body: unknown; token: string }[] = [];
  vi.stubGlobal('fetch', serveMail(posted(calls)));
  setApiMode('live');
  await withSession();
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  expect(screen.queryByText(/운영 인증 중계와 권한 검증 후 제공됩니다/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '무시' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('무시 사유'), { target: { value: '광고 메일' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '무시' }));
  expect(await screen.findByText('업무 회신이 아니라고 닫았습니다.')).toBeTruthy();
  expect(calls).toHaveLength(1);
});

test('모의 응답 모드에서는 처리 호출을 열지 않는다', async () => {
  vi.stubGlobal('fetch', serveMail(posted([])));
  setApiMode('mock');
  renderAt('/mail/inbox', '/mail/inbox', <MailInbox />);
  await screen.findByRole('table', { name: '검토 대기 메일' });
  fireEvent.click(screen.getByRole('button', { name: '무시' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('무시 사유'), { target: { value: '광고 메일' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '무시' }));
  expect(await screen.findByText(/실제 API 모드에서만 실행할 수 있습니다/)).toBeTruthy();
});

test('새 문의로 접수는 입력한 값만 보내고 접수한 문의로 가는 링크를 보인다', async () => {
  const calls: { path: string; body: unknown; token: string }[] = [];
  vi.stubGlobal('fetch', serveMail((init, path) => { calls.push({ path, body: JSON.parse(String(init.body)), token: 'x' }); return jsonResponse(201, { data: { created: true, message_id: MESSAGE, ticket_id: '99999999-9999-4999-8999-999999999999' } }); }));
  setApiMode('live');
  await withSession();
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  fireEvent.click(screen.getByRole('button', { name: '새 문의로 접수' }));
  const form = await screen.findByRole('form', { name: '문의 접수' });
  fireEvent.change(within(form).getByLabelText('고객 ID'), { target: { value: 'CUST-0118' } });
  fireEvent.change(within(form).getByLabelText('제품 코드'), { target: { value: 'HMI-XGT-iXP90' } });
  fireEvent.change(within(form).getByLabelText('보고된 FW'), { target: { value: '1.12' } });
  fireEvent.click(within(form).getByRole('button', { name: '접수' }));
  expect(await screen.findByText(/문의로 접수했습니다/)).toBeTruthy();
  expect(calls[0].path).toBe(`/api/v1/platform/mail/messages/${MESSAGE}/register-ticket`);
  expect(calls[0].body).toEqual({ customer_external_id: 'CUST-0118', product_code: 'HMI-XGT-iXP90', body_language: 'en', fw_version_reported: '1.12', error_code_reported: null });
  expect(screen.getByRole('link', { name: '접수한 문의 열기' }).getAttribute('href')).toBe('/cs/tickets/99999999-9999-4999-8999-999999999999');
});

test('서버가 거부하면(422) 이유를 보이고, 결과를 알 수 없는 오류는 다시 보내지 않고 재조회하며, 세션이 없으면 보내지 않는다', async () => {
  const posts: number[] = [];
  let reply: Response = jsonResponse(422, errorBody('MAIL_REPLY_NOT_A_CANDIDATE', '후보가 아닌 문의입니다.'));
  vi.stubGlobal('fetch', serveMail(() => { posts.push(1); return reply.clone(); }));
  setApiMode('live');
  await withSession();
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  const dismiss = async () => {
    fireEvent.click(screen.getByRole('button', { name: '무시' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('무시 사유'), { target: { value: '사유' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '무시' }));
  };
  await dismiss();
  expect((await screen.findByRole('alert')).textContent).toContain('후보가 아닌 문의입니다.');
  reply = jsonResponse(503, errorBody('UNAVAILABLE', '장애'));
  await dismiss();
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('같은 요청을 다시 보내지 않고'));
  expect(posts).toHaveLength(2);
  cleanup();
  clearSessionCsrfToken();
  const fetch = vi.fn(async (url: RequestInfo | URL, _init?: RequestInit) => String(url).includes('/triage') ? jsonResponse(200, { data: triage() }) : jsonResponse(200, { data: { items: [review({})] } }));
  vi.stubGlobal('fetch', fetch);
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  await dismiss();
  expect((await screen.findByRole('alert')).textContent).toContain('세션 확인 정보가 없습니다');
  expect(fetch.mock.calls.some(call => (call[1] as RequestInit | undefined)?.method === 'POST')).toBe(false);
});
