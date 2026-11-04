// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useNavigate } from 'react-router-dom';
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
    // 시스템 발송은 메일로 들어온 문의만 한다(11/3 팀 결정). 다른 채널은 문안 복사만 있다.
    return jsonResponse(200, { data: { ...base, channel: 'EMAIL', workflow_state: state } });
  }));
  return { posts };
}
function view() { setApiMode('live'); return renderAt('/cs/tickets/x', '/cs/tickets/:ticketId', <TicketDetail />); }
const body = (call: Post) => JSON.parse(call.init.body as string);
const ready = (label: string) => screen.findByText(label, { selector: 'strong.state' });
// 초안 비교 창은 회신 상자의 [다른 초안]으로 연다(KAN-210).
async function openDraft() {
  fireEvent.click(await screen.findByRole('button', { name: '다른 초안' }));
  return within(await screen.findByRole('dialog'));
}
async function approveAndSend(dialog: ReturnType<typeof within>) {
  await waitFor(() => expect((dialog.getByLabelText('보낼 사서함') as HTMLSelectElement).value).toBe(MAILBOX));
  fireEvent.click(dialog.getByLabelText(/고객에게 메일이 나가는 것을 승인합니다/));
  fireEvent.click(dialog.getByRole('button', { name: '승인하고 보내기' }));
}

test('초안을 검토해 승인하면 읽은 판본·사서함·받는 사람·초안 연결을 담아 보내고, 넘긴 결과를 안내한다', async () => {
  const api = server('IN_REVIEW', 5);
  view(); await ready('검토중');
  const dialog = await openDraft();
  // 회신 상자의 기본 버전과 다른 버전도 창에서 골라 보낼 수 있다.
  fireEvent.click(dialog.getByLabelText(/부족한 정보 먼저/));
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
    view(); await ready('검토중');
    await approveAndSend(await openDraft());
    expect((await screen.findByRole('alert')).textContent).toMatch(text);
    expect(api.posts).toHaveLength(1);
    cleanup();
  }
});

test('결과가 정해지지 않은 발송이 있으면 새 발송을 막고, 대화의 상태 확인이 그 발송을 새로 읽는다', async () => {
  const api = server('IN_REVIEW', 5, { customer_sends: [sentRow()] }, () => jsonResponse(200, flow('ANSWERED', 6, { customer_sends: [sentRow({ send_status: 'ACCEPTED', accepted_at: '2026-10-28T00:41:00Z' })] })));
  view(); await ready('검토중');
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
  view(); await ready('검토중');
  fireEvent.click(await screen.findByRole('button', { name: '다시 요청' }));
  await screen.findByText(/승인해 플랫폼에 넘겼습니다/);
  expect(api.posts[0].url).toMatch(/\/workflow\/sends\/s1\/retry$/);
  expect(body(api.posts[0])).toEqual({ version: 7 });
});

test('검토중이 아니거나 부서 답변이 확인 전이면 보내기를 막고 이유를 적는다', async () => {
  server('ANSWERED', 5);
  view(); await ready('답변 완료');
  const dialog = await openDraft();
  expect(await dialog.findByText('검토중인 문의만 보낼 수 있습니다')).toBeTruthy();
});

test('안전 문의에 쓸 초안이 없으면 강조 경고를 보이고 직접 써서 보낼 수 있다', async () => {
  const api = server('IN_REVIEW', 5, {}, undefined, analysis('SAFETY', false));
  view(); await ready('검토중');
  const dialog = await openDraft();
  expect(dialog.getByRole('alert').textContent).toContain('안전 문의입니다');
  fireEvent.change(dialog.getByLabelText('미리보기'), { target: { value: '현장 안전을 위해 즉시 사용을 중지해 주십시오.' } });
  await approveAndSend(dialog);
  await screen.findByText(/승인해 플랫폼에 넘겼습니다/);
  expect(body(api.posts[0])).toMatchObject({ kind: 'ANSWER', body: '현장 안전을 위해 즉시 사용을 중지해 주십시오.', requested_info_codes: [] });
  expect(body(api.posts[0])).not.toHaveProperty('draft_id');
});

test('플랫폼에 닿지 못한 발송(REQUEST_FAILED)은 새 발송을 막지 않고 같은 내용 다시 요청만 더한다', async () => {
  server('IN_REVIEW', 5, { customer_sends: [sentRow({ request_status: 'REQUEST_FAILED', send_status: null })] });
  view(); await screen.findByRole('button', { name: '다시 요청' });
  const dialog = await openDraft();
  await waitFor(() => expect((dialog.getByLabelText('보낼 사서함') as HTMLSelectElement).value).toBe(MAILBOX));
  expect(dialog.queryByText(/결과가 정해지지 않은 발송이 있습니다/)).toBeNull();
  fireEvent.click(dialog.getByLabelText(/고객에게 메일이 나가는 것을 승인합니다/));
  expect((dialog.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement).disabled).toBe(false);
});

test('서버가 미확정 발송으로 거부하면(409) 원인을 안내하고 다시 보내지 않는다', async () => {
  const api = server('IN_REVIEW', 5, {}, () => jsonResponse(409, { error: { code: 'CS_CUSTOMER_SEND_PENDING', message: '발송 대기 중', retryable: false }, meta: { request_id: '11111111-1111-4111-8111-111111111199' } }));
  view(); await ready('검토중');
  await approveAndSend(await openDraft());
  expect((await screen.findByRole('alert')).textContent).toContain('결과가 정해지지 않은 발송이 있습니다');
  expect(api.posts).toHaveLength(1);
});

test('회신 상자 안 보내기 확인으로 고른 초안을 승인해 보내고, 승인 전에는 보내지 않는다(KAN-210)', async () => {
  const api = server('IN_REVIEW', 5);
  view(); await ready('검토중');
  fireEvent.click(await screen.findByRole('button', { name: '답변 보내기' }));
  const box = await screen.findByText('보내기 확인');
  const send = () => screen.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement;
  await waitFor(() => expect((screen.getByLabelText('보낼 사서함') as HTMLSelectElement).value).toBe(MAILBOX));
  expect((screen.getByLabelText('받는 사람') as HTMLInputElement).value).toBe('buyer@example.com');
  expect(send().disabled).toBe(true);
  fireEvent.click(screen.getByLabelText(/고객에게 메일이 나가는 것을 승인합니다/));
  await waitFor(() => expect(send().disabled).toBe(false));
  const button = send(); fireEvent.click(button); fireEvent.click(button);
  await screen.findByText(/승인해 플랫폼에 넘겼습니다/);
  expect(api.posts).toHaveLength(1);
  expect(body(api.posts[0])).toMatchObject({ version: 5, kind: 'ANSWER', mailbox_id: MAILBOX, to: ['buyer@example.com'], draft_id: 'd1', draft_option: 'CONCISE', requested_info_codes: [] });
  expect(box).toBeTruthy();
});

test('메일이 아닌 채널 문의는 시스템 발송 없이 문안 복사만 둔다', async () => {
  const api = server('IN_REVIEW', 5);
  vi.mocked(fetch).mockImplementation(async (url: RequestInfo | URL) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', roles: ['cs_agent'] } }), { status: 200, headers: { 'X-CSRF-Token': 'synthetic-csrf' } });
    if (path.endsWith('/workflow')) return jsonResponse(200, flow('IN_REVIEW', 5));
    if (path.endsWith('/analysis')) return jsonResponse(200, analysis());
    return jsonResponse(200, { data: { ...base, channel: 'PHONE', workflow_state: 'IN_REVIEW' } });
  });
  view(); await ready('검토중');
  expect(await screen.findByRole('button', { name: '문안 복사' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: '답변 보내기' })).toBeNull();
  const dialog = await openDraft();
  expect(dialog.queryByRole('button', { name: '승인하고 보내기' })).toBeNull();
  expect(api.posts).toHaveLength(0);
});

test('다른 문의로 옮기면 보내기 확인·승인·받는 사람이 남지 않는다(앞 문의 내용으로 발송되지 않는다)', async () => {
  server('IN_REVIEW', 5);
  const Go = () => { const go = useNavigate(); return <button onClick={() => go('/cs/tickets/other')}>다른 문의</button>; };
  setApiMode('live'); renderAt('/cs/tickets/x', '/cs/tickets/:ticketId', <><TicketDetail /><Go /></>);
  await ready('검토중');
  fireEvent.click(await screen.findByRole('button', { name: '답변 보내기' }));
  await screen.findByText('보내기 확인');
  fireEvent.change(screen.getByLabelText('받는 사람'), { target: { value: 'previous@example.com' } });
  fireEvent.click(screen.getByLabelText(/고객에게 메일이 나가는 것을 승인합니다/));
  fireEvent.click(screen.getByRole('button', { name: '다른 문의' }));
  await waitFor(() => expect(screen.queryByText('보내기 확인')).toBeNull());
  await ready('검토중');
  fireEvent.click(await screen.findByRole('button', { name: '답변 보내기' }));
  await screen.findByText('보내기 확인');
  expect((screen.getByLabelText('받는 사람') as HTMLInputElement).value).toBe('buyer@example.com');
  expect((screen.getByLabelText(/고객에게 메일이 나가는 것을 승인합니다/) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement).disabled).toBe(true);
});

test('승인한 뒤 본문을 부서 문안으로 바꾸면 승인이 풀리고 다시 확인해야 보낸다', async () => {
  const api = server('IN_REVIEW', 5, {
    transfers: [{ id: 't1', dept_code: 'TS_TRF', status: 'RESULT_SUBMITTED', round: 1, reason: '검토', transferred_at: '2026-10-28T00:30:00Z', transferred_by: 'u1', ended_at: null, end_reason: null }],
    dept_results: [{ id: 'r1', transfer_id: 't1', dept_code: 'TS_TRF', version: 1, review_status: 'ACCEPTED', result: '교체 필요', reply_content: 'We will replace the unit.', submitted_at: '2026-10-28T01:00:00Z', submitted_by: 'u2' }],
  });
  view(); await ready('검토중');
  fireEvent.click(await screen.findByRole('button', { name: '답변 보내기' }));
  await screen.findByText('보내기 확인');
  await waitFor(() => expect((screen.getByLabelText('보낼 사서함') as HTMLSelectElement).value).toBe(MAILBOX));
  const approve = () => screen.getByLabelText(/고객에게 메일이 나가는 것을 승인합니다/) as HTMLInputElement;
  const send = () => screen.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement;
  fireEvent.click(approve());
  await waitFor(() => expect(send().disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '이 문안 쓰기' }));
  await waitFor(() => expect(approve().checked).toBe(false));
  expect(send().disabled).toBe(true);
  fireEvent.click(approve());
  await waitFor(() => expect(send().disabled).toBe(false));
  fireEvent.click(send());
  await screen.findByText(/승인해 플랫폼에 넘겼습니다/);
  expect(api.posts).toHaveLength(1);
  expect(body(api.posts[0]).body).toBe('We will replace the unit.');
});

// 기획서 4.1 ②: 추가 정보 요청은 처리를 막는 누락만 묻는다(정책표 blocking_fields).
const withMissing = (missing: string[], blocking: string[]) => { const body = analysis(); (body.data as Record<string, unknown>).missing_info = { ai: null, error: null,
  effective: { category_code: 'DEFECT', required: ['error_code', 'operating_condition', 'serial_no'], provided: ['serial_no'], already_asked: [], missing, blocking_missing: blocking } }; return body; };
const suggested = async () => { const group = await screen.findByRole('radiogroup', { name: '다음 행동' }); await waitFor(() => expect(within(group).getByText('제안')).toBeTruthy()); return within(group).getByText('제안').closest('label')!.textContent; };

test('막는 정보가 빠졌을 때만 추가 정보 요청을 제안하고, 나머지 누락은 답변에서 묻도록 안내한다', async () => {
  server('IN_REVIEW', 1, {}, undefined, withMissing(['operating_condition'], []));
  view();
  expect(await suggested()).toContain('답변');
  expect(screen.getByText(/꼭 필요한 정보는 있어 답변을 제안합니다\. 빠진 정보\(.+\)는 답변에서 같이 물어보세요/)).toBeTruthy();
  cleanup();
  server('IN_REVIEW', 1, {}, undefined, withMissing(['error_code', 'operating_condition'], ['error_code']));
  view();
  expect(await suggested()).toContain('추가 정보 요청');
  expect(screen.getByText(/처리에 꼭 필요한 정보 1개\(.+\)가 빠져 있어 추가 정보 요청을 제안합니다/)).toBeTruthy();
});

// KAN-162 QA: 한국어 요약은 줄마다 번호를 붙인 항목으로 보인다. 줄이 없던 이전 요약은 문장 끝에서 나눈다.
test.each([
  ['TRF-OIL-1000 개스킷 누유가 반복됨\n1. 보증 처리 가능 여부를 문의함', ['TRF-OIL-1000 개스킷 누유가 반복됨', '보증 처리 가능 여부를 문의함']],
  ['개스킷 누유가 있다고 함. 보증 여부를 묻는다.', ['개스킷 누유가 있다고 함.', '보증 여부를 묻는다.']],
])('한국어 요약을 항목으로 나눠 번호를 붙인다 (%s)', async (summary, items) => {
  server('IN_REVIEW', 1);
  const inner = (globalThis.fetch as unknown as (url: RequestInfo | URL, init?: RequestInit) => Promise<Response>);
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => String(url).endsWith('/summary')
    ? jsonResponse(200, { data: { ticket_id: 'x', provenance: 'ai_draft', latest_attempt: null, summary: { id: 's', summary_ko: summary, created_at: '2026-11-03T00:00:00Z', requested_model: 'm', model_version: null, prompt_version: 'v2', input_version: 'v', stale: false } } })
    : inner(url, init)));
  view();
  const first = await screen.findByText(items[0]);
  const box = first.closest('.sum')!;
  expect([...box.querySelectorAll('p')].map(p => [p.querySelector('.n')?.textContent, p.textContent?.replace(p.querySelector('.n')?.textContent ?? '', '')])).toEqual(items.map((text, index) => [String(index + 1), text]));
});

// 고객 원문: 원문은 항상 보이고 '한국어 번역 보기'로 번역을 원문 위에 덧붙인다. 번역이 없으면 만들기 단추. 원문이 한국어면 번역을 권하지 않는다.
const translationView = (translation: Record<string, unknown> | null) => ({ data: { ticket_id: 'x', provenance: 'ai_draft', latest_attempt: null, translation } });
const translated = (over: Record<string, unknown> = {}) => ({ id: 't', subject_ko: '변압기 누유', body_ko: '개스킷에서 기름이 샙니다.\n보증 대상인가요?', created_at: '2026-11-03T00:00:00Z', requested_model: 'm', model_version: null, prompt_version: 'v1', input_version: 'v', stale: false, ...over });
function withTranslation(answer: () => Response, language = 'es') {
  const { posts } = server('IN_REVIEW', 1, {}, call => call.url.endsWith('/translation') ? jsonResponse(201, translationView(translated())) : jsonResponse(200, flow('IN_REVIEW', 2)));
  const inner = (globalThis.fetch as unknown as (url: RequestInfo | URL, init?: RequestInit) => Promise<Response>);
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    const get = (init?.method ?? 'GET') === 'GET';
    if (get && path.endsWith('/translation')) return answer();
    if (get && /\/cs\/tickets\/x$/.test(path)) return jsonResponse(200, { data: { ...base, channel: 'EMAIL', workflow_state: 'IN_REVIEW', body_language: language } });
    return inner(url, init);
  }));
  return posts;
}
const toKorean = async () => { view(); await ready('검토중'); fireEvent.click(screen.getByRole('button', { name: '한국어 번역 보기' })); };

test('번역이 없으면 한국어 번역 단추를 보이고, 누르면 POST한 뒤 번역을 메일 카드로 보인다', async () => {
  let made = false;
  const posts = withTranslation(() => jsonResponse(200, translationView(made ? translated() : null)));
  await toKorean();
  expect(await screen.findByText(/아직 번역이 없습니다/)).toBeTruthy();
  made = true;
  fireEvent.click(screen.getByRole('button', { name: '✦ 한국어 번역' }));
  const card = (await screen.findByText('변압기 누유')).closest('.mail-card') as HTMLElement;
  expect(card.querySelector('.mail')?.textContent).toBe('개스킷에서 기름이 샙니다.\n보증 대상인가요?');
  expect(within(card).getByText('✦ AI 번역')).toBeTruthy();
  expect(screen.getByText(/원문이 기준입니다/)).toBeTruthy();
  // 번역을 덧붙여도 원문 카드는 그대로 있다.
  expect(document.querySelectorAll('.mail-card').length).toBeGreaterThanOrEqual(2);
  expect(posts.filter(call => call.url.endsWith('/translation'))).toHaveLength(1);
  expect(screen.queryByRole('button', { name: '✦ 한국어 번역' })).toBeNull();
  // 처리 기록에 AI 행으로 남는다.
  expect(screen.getByText('한국어 번역')).toBeTruthy();
});

test('원문이 바뀐 번역은 그대로 보이고 다시 번역 단추를 둔다', async () => {
  withTranslation(() => jsonResponse(200, translationView(translated({ stale: true }))));
  await toKorean();
  expect(await screen.findByText('변압기 누유')).toBeTruthy();
  expect(screen.getByText(/원문이 바뀌어 다시 번역할 수 있습니다/)).toBeTruthy();
  expect(screen.getByRole('button', { name: '✦ 한국어 번역' })).toBeTruthy();
});

test('원문이 한국어면 번역 보기 단추를 두지 않고 원문만 보인다', async () => {
  const posts = withTranslation(() => jsonResponse(200, translationView(null)), 'ko');
  view(); await ready('검토중');
  expect(screen.queryByRole('button', { name: '한국어 번역 보기' })).toBeNull();
  expect(screen.queryByRole('button', { name: '✦ 한국어 번역' })).toBeNull();
  expect(document.querySelector('.mail-card .mail')?.textContent).toBe(base.body);
  expect(posts.filter(call => call.url.endsWith('/translation'))).toHaveLength(0);
});

test('번역 기능이 꺼진 배포(404)는 안내만 한다', async () => {
  withTranslation(() => jsonResponse(404, { error: { code: 'NOT_FOUND', message: 'Not Found', retryable: false }, meta: { request_id: 'r' } }));
  await toKorean();
  expect(await screen.findByText('번역 기능이 이 배포에서 꺼져 있습니다')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '✦ 한국어 번역' })).toBeNull();
});
