import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import MailBox from './MailBox';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import type { ReplyReview, ReviewCandidate } from '../api/mail';
import { errorBody, jsonResponse, renderAt } from '../test/render';

// 운영 빌드 시험에서 업무 UI 설정을 켠다(설정 값은 모듈을 읽을 때 정해진다).
vi.mock('../components/BusinessAccess', async importOriginal => {
  const actual = await importOriginal<typeof import('../components/BusinessAccess')>();
  return { ...actual, useBusinessAccess: (business: Parameters<typeof actual.useBusinessAccess>[0], operational?: boolean) => actual.useBusinessAccess(business, operational, true) };
});

const MESSAGE = '44444444-4444-4444-8444-444444444444';
const T1 = '66666666-6666-4666-8666-666666666666';
const T2 = '77777777-7777-4777-8777-777777777777';
const T3 = '88888888-8888-4888-8888-888888888888';
const review = (over: Partial<ReplyReview> = {}): ReplyReview => ({
  message_id: MESSAGE, received_at: '2026-10-29T08:41:00Z', created_at: '2026-10-29T08:42:00Z', reason: 'AMBIGUOUS',
  from_address: 'buyer@example.com', subject: 'Re: panel frozen', has_attachments: true,
  candidates: [{ owner_service: 'cs', business_ref: `cs_ticket:${T1}`, method: 'HEADER' }, { owner_service: 'cs', business_ref: `cs_ticket:${T2}`, method: 'THREAD' }], ...over,
});
const ticket = (id: string, external: string, subject: string) => ({
  id, external_id: external, subject, body: '합성', body_language: 'en', channel: 'EMAIL', customer_external_id: 'CUST-1', product_code: 'HMI-1',
  received_at: '2026-10-28T00:00:00Z', known_at: '2026-10-28T00:00:00Z', workflow_state: 'WAITING_CUSTOMER', data_origin: 'synthetic', derivation_kind: 'SYSTEM',
  version: 1, resolution_case: null, error_code_reported: null, fw_version_reported: null,
});
const TICKETS: Record<string, ReturnType<typeof ticket>> = { [T1]: ticket(T1, 'CS-0001', 'HMI 터치 무반응'), [T2]: ticket(T2, 'CS-0002', '인버터 알람'), [T3]: ticket(T3, 'CS-0003', '다른 문의') };
type Post = { path: string; body: unknown; token: string };
// 서버의 후보 응답(KAN-235): 저장된 후보에 문의 정보·근거가 붙고, 고객 연락처로 찾은 문의가 더해진다.
const cand = (id: string, method: ReviewCandidate['method'], evidence: ReviewCandidate['evidence'] = []): ReviewCandidate => {
  const t = TICKETS[id];
  return { owner_service: 'cs', business_ref: `cs_ticket:${id}`, method, evidence,
    ticket: { external_id: t.external_id, subject: t.subject, state: t.workflow_state, customer_external_id: t.customer_external_id, product_code: t.product_code } };
};
const CANDIDATES: ReviewCandidate[] = [cand(T1, 'HEADER', ['WAITING_CUSTOMER', 'PRODUCT_MATCH']), cand(T2, 'THREAD')];

function serve(items: ReplyReview[], post: (path: string, init: RequestInit) => Response = () => jsonResponse(200, { data: { message_id: MESSAGE, outcome: 'LINKED' } }), candidates: ReviewCandidate[] = CANDIDATES) {
  const posts: Post[] = [];
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: {} }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' } });
    if (init?.method === 'POST') { posts.push({ path, body: JSON.parse(String(init.body)), token: (init.headers as Record<string, string>)['X-CSRF-Token'] }); return post(path, init); }
    if (path.endsWith('/mail/reply-reviews')) return jsonResponse(200, { data: { items } });
    if (path.endsWith('/candidates')) return jsonResponse(200, { data: { message_id: path.split('/').at(-2), enriched: true, items: path.includes(MESSAGE) ? candidates : [] } });
    if (path.startsWith('/api/v1/cs/tickets?')) {
      const q = (new URL(path, 'http://x').searchParams.get('q') ?? '').toLowerCase();
      return jsonResponse(200, { data: { items: Object.values(TICKETS).filter(t => t.external_id.toLowerCase().includes(q)), next_cursor: null } });
    }
    const id = path.match(/\/cs\/tickets\/([0-9a-f-]{36})$/)?.[1];
    if (id && TICKETS[id]) return jsonResponse(200, { data: TICKETS[id] });
    return jsonResponse(404, errorBody('NOT_FOUND', '없음'));
  });
  vi.stubGlobal('fetch', fetch);
  return { fetch, posts };
}
async function show() {
  setApiMode('live');
  const { request } = await import('../api/client');
  await request('/api/v1/platform/me');
  return renderAt('/mail/inbox', '/mail/inbox', <MailBox />);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); setApiMode('mock'); clearSessionCsrfToken(); });

test('목록은 회신 검토 API에서 읽고 합성 시안 데이터를 보이지 않는다', async () => {
  const older = review({ message_id: '55555555-5555-4555-8555-555555555555', received_at: '2026-10-28T08:00:00Z', reason: 'UNMATCHED_REPLY', from_address: null, subject: null, has_attachments: null, candidates: [] });
  const { fetch } = serve([older, review()]);
  await show();
  const list = await screen.findByRole('region', { name: '받은 메일' });
  const rows = within(list).getAllByRole('listitem');
  expect(rows[0].textContent).toContain('Re: panel frozen');
  expect(rows[0].textContent).toContain('후보가 여럿');
  expect(rows[0].textContent).toContain('후보 2건');
  expect(rows[1].textContent).toContain('(제목 없음) 55555555');
  expect(rows[1].textContent).toContain('보낸 사람 미확인');
  // 서버가 붙인 문의 정보와 근거를 보인다.
  expect(await screen.findByText('CS-0001 · HMI 터치 무반응')).toBeTruthy();
  expect(screen.getByText('답장 헤더가 가리킴')).toBeTruthy();
  expect(screen.getByText('고객 회신 대기 중')).toBeTruthy();
  expect(screen.getByText('본문에 같은 제품')).toBeTruthy();
  expect(screen.queryByText(/시안|CS-DEMO|CUST-0042/)).toBeNull();
  expect(fetch.mock.calls.some(call => String(call[0]) === `/api/v1/platform/mail/reply-reviews/${MESSAGE}/candidates`)).toBe(true);
  // 사유 거르기
  fireEvent.click(screen.getByRole('tab', { name: /답장 대상 못 찾음/ }));
  expect(within(list).getAllByRole('listitem')).toHaveLength(1);
});

test('후보를 골라 연결하면 후보 값과 메모를 보낸다', async () => {
  const { posts, fetch } = serve([review()]);
  await show();
  const link = await screen.findByRole('button', { name: '이 문의에 연결' }) as HTMLButtonElement;
  expect(link.disabled).toBe(true);
  fireEvent.click(await screen.findByLabelText('CS-0002 · 인버터 알람 선택'));
  fireEvent.change(screen.getByLabelText('처리 메모'), { target: { value: '  같은 대화  ' } });
  fireEvent.click(link);
  expect(await screen.findByText(/CS-0002에 연결했습니다/)).toBeTruthy();
  expect(posts).toEqual([{ path: `/api/v1/platform/mail/reply-reviews/${MESSAGE}/link`, token: 'tok', body: { owner_service: 'cs', business_ref: `cs_ticket:${T2}`, note: '같은 대화' } }]);
  expect(fetch.mock.calls.filter(call => String(call[0]).endsWith('/reply-reviews') && !(call[1] as RequestInit | undefined)?.method).length).toBeGreaterThanOrEqual(2);
});

test('업무 아님은 메모가 있어야 열리고 dismiss로 보낸다', async () => {
  const { posts } = serve([review()]);
  await show();
  const button = await screen.findByRole('button', { name: '업무 아님' }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('처리 메모'), { target: { value: '광고 메일' } });
  fireEvent.click(button);
  expect(await screen.findByText('업무 아님으로 닫았습니다.')).toBeTruthy();
  expect(posts).toEqual([{ path: `/api/v1/platform/mail/reply-reviews/${MESSAGE}/dismiss`, token: 'tok', body: { note: '광고 메일' } }]);
});

test('문의 번호로 찾으면 후보는 그 후보를 고르고, 후보 밖 문의도 골라 연결한다', async () => {
  const { posts, fetch } = serve([review()]);
  await show();
  const input = await screen.findByLabelText('문의 번호로 찾기');
  fireEvent.change(input, { target: { value: 'CS-0001' } });
  fireEvent.click(screen.getByRole('button', { name: '찾기' }));
  expect(await screen.findByText('문의 번호로 찾음')).toBeTruthy();
  expect(fetch.mock.calls.some(call => String(call[0]).startsWith('/api/v1/cs/tickets?') && String(call[0]).includes('q=CS-0001'))).toBe(true);
  expect(((await screen.findByLabelText('CS-0001 · HMI 터치 무반응 선택')) as HTMLInputElement).checked).toBe(true);
  // 후보 밖 문의는 찾은 자리에 고를 수 있게 보이고, 서버가 근거(SEARCH)를 남긴다.
  fireEvent.change(input, { target: { value: 'cs-0003' } });
  fireEvent.click(screen.getByRole('button', { name: '찾기' }));
  expect(await screen.findByText('후보 밖')).toBeTruthy();
  expect(((screen.getByLabelText('CS-0003 선택')) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '이 문의에 연결' }));
  expect(await screen.findByText(/CS-0003에 연결했습니다/)).toBeTruthy();
  expect(posts).toEqual([{ path: `/api/v1/platform/mail/reply-reviews/${MESSAGE}/link`, token: 'tok', body: { owner_service: 'cs', business_ref: `cs_ticket:${T3}`, note: '문의 번호로 찾아 연결' } }]);
  cleanup();
  serve([review()]);
  await show();
  fireEvent.change(await screen.findByLabelText('문의 번호로 찾기'), { target: { value: 'CS-9999' } });
  fireEvent.click(screen.getByRole('button', { name: '찾기' }));
  expect(await screen.findByText('CS-9999 문의를 찾지 못했습니다.')).toBeTruthy();
});

test('고객 연락처로 찾은 같은 고객의 문의도 후보로 보이고, 후보가 없으면 번호로 찾으라고 안내한다', async () => {
  serve([review()], undefined, [...CANDIDATES, cand(T3, 'CONTACT', ['CUSTOMER_CONTACT'])]);
  await show();
  expect(await screen.findByText('CS-0003 · 다른 문의')).toBeTruthy();
  expect(screen.getByText('같은 고객의 진행 중 문의')).toBeTruthy();
  cleanup();
  serve([review({ reason: 'UNMATCHED_REPLY', candidates: [] })], undefined, []);
  await show();
  expect(await screen.findByText(/주고받은 문의나 등록된 고객 연락처가 없어 후보를 찾지 못했습니다/)).toBeTruthy();
  expect(screen.queryByText(/후보 중에서만 연결/)).toBeNull();
});

test('이미 처리된 검토(409)와 서버 거부(422), 결과 불명 오류를 각각 알린다', async () => {
  let reply = jsonResponse(409, errorBody('MAIL_REPLY_REVIEW_DECIDED', '이미 결정했습니다.'));
  const { posts } = serve([review()], () => reply.clone());
  await show();
  fireEvent.click(await screen.findByLabelText('CS-0001 · HMI 터치 무반응 선택'));
  fireEvent.click(screen.getByRole('button', { name: '이 문의에 연결' }));
  expect((await screen.findByRole('alert')).textContent).toContain('이미 처리된 메일입니다');
  reply = jsonResponse(422, errorBody('MAIL_REPLY_NOT_A_CANDIDATE', '후보가 아닌 문의입니다.'));
  fireEvent.click(screen.getByLabelText('CS-0001 · HMI 터치 무반응 선택'));
  fireEvent.click(screen.getByRole('button', { name: '이 문의에 연결' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('후보가 아닌 문의입니다.'));
  reply = jsonResponse(503, errorBody('UNAVAILABLE', '장애'));
  fireEvent.change(screen.getByLabelText('처리 메모'), { target: { value: '광고' } });
  fireEvent.click(screen.getByRole('button', { name: '업무 아님' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('같은 요청을 다시 보내지 않고'));
  expect(posts).toHaveLength(3);
});

test('연결할 답장이 없으면 빈 상태를, 목록 오류는 오류를 보인다', async () => {
  serve([]);
  await show();
  expect((await screen.findAllByText('연결할 고객 답장이 없습니다.')).length).toBeGreaterThan(0);
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(403, errorBody('FORBIDDEN', '권한이 없습니다.'))));
  setApiMode('live');
  renderAt('/mail/inbox', '/mail/inbox', <MailBox />);
  expect(await screen.findByText(/권한이 없습니다/)).toBeTruthy();
});

test('모의 응답 모드는 합성 예시임을 알리고 처리 호출을 보내지 않는다', async () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  setApiMode('mock');
  renderAt('/mail/inbox', '/mail/inbox', <MailBox />);
  expect(await screen.findByText(/합성 예시이며 실제 메일이 아닙니다/)).toBeTruthy();
  fireEvent.change(await screen.findByLabelText('처리 메모'), { target: { value: '광고' } });
  fireEvent.click(screen.getByRole('button', { name: '업무 아님' }));
  expect(await screen.findByText(/실제 API 모드에서만 실행할 수 있습니다/)).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});
