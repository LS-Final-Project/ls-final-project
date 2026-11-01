// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import TicketDetail from './TicketDetail';
import { setApiMode } from '../api/client';
import { jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';

const source = examples.find(x => x.path === '/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222')!;
const base = (source.body as { data: Record<string, unknown> }).data;
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

const MAILBOX = '33333333-3333-4333-8333-333333333333';
const draft = { options: [
  { label: 'CONCISE', sentences: [{ text: 'Please send the label photo.', kind: 'EVIDENCE', evidence_chunk_ids: ['c1'] }], info_requests: [] },
  { label: 'INFO_FIRST', sentences: [{ text: 'We need the rating label photo.', kind: 'EVIDENCE', evidence_chunk_ids: ['c1'] }], info_requests: [{ info_code: 'label_photo', question: '정격 라벨 사진' }] },
] };
const analysis = (urgency = 'NONE', valid = true) => ({ data: {
  fields: { urgency_flag: { ai_value: urgency, corrected_value: null, effective_value: urgency, source: 'ai_draft', corrected: false } },
  drafts: valid ? [{ id: 'd1', analysis_id: 'a1', created_at: '2026-10-28T00:02:00Z', draft, error_code: null, input_version: 'v', job_id: null, model_version: 'm', outcome: 'VALID', prompt_version: 'p', provenance_kind: 'ai_draft', seq: 1, verification_status: 'VERIFIED', verifications: [] }] : [],
  latest_valid_draft_id: valid ? 'd1' : null, latest_valid_run_id: 'a1', runs: [], corrections: [], latest_run: null, return_subtype_stated: null, version: 0,
  missing_info: { ai: [], effective: [], error: null }, rules: { ai: null, effective: null, ai_error: null, effective_error: null, tier_note: null,
    first_response_due: { ai: null, corrected: null, effective: '2026-10-28T03:00:00Z', source: 'ai_draft' }, resolution_due: { ai: null, corrected: null, effective: null, source: null } },
} });
const sentRow = (over: Record<string, unknown> = {}) => ({ id: 's1', kind: 'INFO_REQUEST', body: 'We need the label photo.', body_type: 'text', approved_at: '2026-10-28T00:40:00Z', accepted_at: null, approved_by: '11111111-1111-4111-8111-111111111111',
  request_status: 'REGISTERED', request_error: null, send_status: 'QUEUED', send_error_code: null, mailbox_id: MAILBOX, ...over });
const flow = (state: string, version: number, extra: Record<string, unknown> = {}) => ({ data: { ticket_id: 'x', state, version, owner_user_id: null, closed_at: null, closed_by: null, customer_sent_at: null, dept_results: [], transfers: [],
  mail_origin: { mailbox_id: MAILBOX, message_id: '44444444-4444-4444-8444-444444444444', received_at: '2026-10-28T00:00:00Z', registered_by: '11111111-1111-4111-8111-111111111111', sender_address: 'buyer@example.com' }, customer_sends: [], customer_replies: [], ...extra } });
type Post = { url: string; init: RequestInit };

function server(state: string, version: number, workflowExtra: Record<string, unknown> = {}, post: (call: Post) => Response = () => jsonResponse(200, flow('IN_REVIEW', version + 1, { customer_sends: [sentRow()] })), analysisBody = analysis()) {
  const posts: Post[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', roles: ['cs_agent'] } }), { status: 200, headers: { 'X-CSRF-Token': 'synthetic-csrf' } });
    if (path.endsWith('/auth/mailbox')) return jsonResponse(200, { data: { items: [{ mailbox_id: MAILBOX, state: 'connected', revision: 1, updated_at: '2026-10-28T00:00:00Z', reconnect_required: false, reconnect_reason: null, reconnect_required_at: null }] } });
    if (['POST', 'PUT'].includes(init?.method ?? 'GET')) { const call = { url: path, init: init! }; posts.push(call); return post(call); }
    if (path.endsWith('/workflow')) return jsonResponse(200, flow(state, version, workflowExtra));
    if (path.endsWith('/analysis')) return jsonResponse(200, analysisBody);
    return jsonResponse(200, { data: { ...base, workflow_state: state } });
  }));
  return { posts };
}
function view() { setApiMode('live'); return renderAt('/cs/tickets/x', '/cs/tickets/:ticketId', <TicketDetail />); }
const body = (call: Post) => JSON.parse(call.init.body as string);
async function openDraft() {
  await waitFor(() => expect((screen.getByText(/^기타 \d/)).closest('details')).toBeTruthy());
  fireEvent.click(screen.getByText(/^기타 \d/));
  fireEvent.click(await screen.findByRole('menuitem', { name: /^AI 초안 보기/ }));
  return within(await screen.findByRole('dialog'));
}
async function approveAndSend(dialog: ReturnType<typeof within>) {
  await waitFor(() => expect((dialog.getByLabelText('보낼 사서함') as HTMLSelectElement).value).toBe(MAILBOX));
  fireEvent.click(dialog.getByLabelText(/고객에게 메일이 나가는 것을 승인합니다/));
  fireEvent.click(dialog.getByRole('button', { name: '승인하고 보내기' }));
}

test('초안을 검토해 승인하면 읽은 판본·사서함·받는 사람·초안 연결을 담아 보내고, 넘긴 결과를 안내한다', async () => {
  const api = server('IN_REVIEW', 5);
  view(); await screen.findByText('검토 중', { selector: 'strong' });
  const dialog = await openDraft();
  expect((dialog.getByLabelText('받는 사람') as HTMLInputElement).value).toBe('buyer@example.com');
  await approveAndSend(dialog);
  await screen.findByText(/승인해 플랫폼에 넘겼습니다/);
  expect(api.posts).toHaveLength(1);
  expect(api.posts[0].url).toMatch(/\/workflow\/send$/);
  expect(body(api.posts[0])).toMatchObject({ version: 5, kind: 'INFO_REQUEST', mailbox_id: MAILBOX, to: ['buyer@example.com'], body_type: 'text', draft_id: 'd1', draft_option: 'INFO_FIRST', requested_info_codes: ['label_photo'] });
  expect(api.posts[0].init.headers).toMatchObject({ 'X-CSRF-Token': 'synthetic-csrf' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('플랫폼이 거부하거나 연결하지 못하거나 응답이 없으면 각각 다른 안내를 하고 다시 보내지 않는다', async () => {
  for (const [status, text] of [['REJECTED', /플랫폼이 발송을 거부했습니다\(RECIPIENT_REJECTED\)/], ['REQUEST_FAILED', /플랫폼에 연결하지 못해/], ['REQUEST_UNKNOWN', /새로 승인하지 말고/]] as const) {
    const api = server('IN_REVIEW', 5, {}, () => jsonResponse(200, flow('IN_REVIEW', 6, { customer_sends: [sentRow({ request_status: status, request_error: status === 'REJECTED' ? 'RECIPIENT_REJECTED' : null })] })));
    view(); await screen.findByText('검토 중', { selector: 'strong' });
    await approveAndSend(await openDraft());
    expect((await screen.findByRole('alert')).textContent).toMatch(text);
    expect(api.posts).toHaveLength(1);
    cleanup();
  }
});

test('결과가 정해지지 않은 발송이 있으면 새 발송을 막고, 대화의 상태 확인이 그 발송을 새로 읽는다', async () => {
  const api = server('IN_REVIEW', 5, { customer_sends: [sentRow()] }, () => jsonResponse(200, flow('ANSWERED', 6, { customer_sends: [sentRow({ send_status: 'ACCEPTED', accepted_at: '2026-10-28T00:41:00Z' })] })));
  view(); await screen.findByText('검토 중', { selector: 'strong' });
  const dialog = await openDraft();
  expect(await dialog.findByText(/결과가 정해지지 않은 발송이 있습니다/)).toBeTruthy();
  expect((dialog.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.keyDown(window, { key: 'Escape' });
  fireEvent.click(await screen.findByRole('button', { name: '상태 확인' }));
  await screen.findByText('발송 상태를 읽었습니다.');
  expect(api.posts[0].url).toMatch(/\/workflow\/sends\/s1\/refresh$/);
});

test('넘김 결과를 모르는 발송은 다시 요청으로 같은 판본을 보내 확인한다', async () => {
  const api = server('IN_REVIEW', 7, { customer_sends: [sentRow({ request_status: 'REQUEST_UNKNOWN', send_status: null })] },
    () => jsonResponse(200, flow('IN_REVIEW', 7, { customer_sends: [sentRow()] })));
  view(); await screen.findByText('검토 중', { selector: 'strong' });
  fireEvent.click(await screen.findByRole('button', { name: '다시 요청' }));
  await screen.findByText(/승인해 플랫폼에 넘겼습니다/);
  expect(api.posts[0].url).toMatch(/\/workflow\/sends\/s1\/retry$/);
  expect(body(api.posts[0])).toEqual({ version: 7 });
});

test('검토중이 아니거나 부서 답변이 확인 전이면 보내기를 막고 이유를 적는다', async () => {
  server('ANSWERED', 5);
  view(); await screen.findByText(/^기타 \d/);
  const dialog = await openDraft();
  expect(await dialog.findByText('검토중인 문의만 보낼 수 있습니다')).toBeTruthy();
});

test('안전 문의에 쓸 초안이 없으면 강조 경고를 보이고 직접 써서 보낼 수 있다', async () => {
  const api = server('IN_REVIEW', 5, {}, undefined, analysis('SAFETY', false));
  view(); await screen.findByText('검토 중', { selector: 'strong' });
  const dialog = await openDraft();
  expect(dialog.getByRole('alert').textContent).toContain('안전 문의입니다');
  fireEvent.change(dialog.getByLabelText('미리보기'), { target: { value: '현장 안전을 위해 즉시 사용을 중지해 주십시오.' } });
  await approveAndSend(dialog);
  await screen.findByText(/승인해 플랫폼에 넘겼습니다/);
  expect(body(api.posts[0])).toMatchObject({ kind: 'ANSWER', body: '현장 안전을 위해 즉시 사용을 중지해 주십시오.', requested_info_codes: [] });
  expect(body(api.posts[0])).not.toHaveProperty('draft_id');
});
