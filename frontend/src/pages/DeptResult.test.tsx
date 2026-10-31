import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import DeptResult from './DeptResult';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const TICKET = '33333333-3333-4333-8333-333333333333';
const ticket = { data: { id: TICKET, external_id: 'CS26-1039', version: 1, data_origin: 'synthetic', derivation_kind: 'PROVIDED', received_at: '2026-10-27T02:05:00Z', known_at: '2026-10-27T02:05:00Z',
  channel: 'EMAIL', body_language: 'en', customer_external_id: 'CUST-0581', product_code: 'INV-LSLV0220-H100', subject: 'Same alarm repeats', body: 'Alarm OHT every 2-3 hours.', fw_version_reported: '1.12', error_code_reported: null, workflow_state: 'TRANSFERRED' }, meta: {} };
const transfer = (round: number, status: string, over: Record<string, unknown> = {}) => ({ id: `tr-${round}`, dept_code: 'QA_CLAIM', round, status, reason: round === 2 ? '설정 재검토로 해소되지 않음' : '1차 요청', transferred_at: `2026-10-2${round}T01:30:00Z`, transferred_by: '77777777-7777-4777-8777-777777777777', end_reason: null, ended_at: null, ended_by: null, ...over });
const workflow = (transfers: unknown[], results: unknown[] = [], version = 7) => ({ data: { ticket_id: TICKET, state: 'TRANSFERRED', version, owner_user_id: null, customer_sent_at: null, closed_at: null, closed_by: null,
  customer_replies: [], customer_sends: [], mail_origin: null, return_cause_type: null, return_non_manufacturing_reason: null, return_response_note: null, return_responsibility: null,
  return_subtype_confirmed: null, return_subtype_confirmed_at: null, return_subtype_confirmed_by: null, transfers, dept_results: results }, meta: {} });
const result1 = { id: 'res-1', dept_code: 'QA_CLAIM', transfer_id: 'tr-1', result: '과부하 설정값 상향 권고', reply_content: null, review_status: 'REWORK', review_note: '펌웨어 관점 검토 요청', reviewed_at: null, reviewed_by: null, submitted_at: '2026-10-22T01:00:00Z', submitted_by: 'u', version: 1 };
const staff = { data: { id: '00000000-0000-4000-8000-000000000009', display_name: '최민호', dept: 'QA_CLAIM', roles: ['dept_staff'] } };

type Post = (init: RequestInit) => Response;
const serve = (wf = workflow([transfer(1, 'RESULT_SUBMITTED'), transfer(2, 'OPEN')], [result1]), who: unknown = staff, post: Post = () => jsonResponse(200, workflow([], [], 8))) => vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
  const path = String(url);
  if (path.endsWith('/platform/me')) return new Response(JSON.stringify(who), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' } });
  if (path.endsWith('/workflow/dept-result')) return post(init ?? {});
  if (path.endsWith('/workflow')) return jsonResponse(200, wf);
  return jsonResponse(200, ticket);
});
const show = () => { setApiMode('live'); return renderAt(`/dept/result/${TICKET}`, '/dept/result/:ticketId', <DeptResult />); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); clearSessionCsrfToken(); });

test('요청 정보·사유·고객 원문과 이전 차수의 답변·다시 요청 사유를 보인다', async () => {
  vi.stubGlobal('fetch', serve());
  show();
  expect(await screen.findByText('CS26-1039')).toBeTruthy();
  expect(screen.getByText('2차')).toBeTruthy();
  expect(screen.getByText('설정 재검토로 해소되지 않음')).toBeTruthy();
  expect(screen.getByText('Alarm OHT every 2-3 hours.')).toBeTruthy();
  const previous = within(screen.getByRole('table', { name: '이전 차수' })).getAllByRole('row')[1];
  expect(previous.textContent).toContain('과부하 설정값 상향 권고');
  expect(previous.textContent).toContain('펌웨어 관점 검토 요청');
  expect((screen.getByRole('button', { name: '담당 아님 반송' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '임시 저장' }) as HTMLButtonElement).disabled).toBe(true);
});

test('답변을 보내면 읽은 판본과 함께 요청하고 보냈다는 안내를 보인다', async () => {
  const calls: RequestInit[] = [];
  vi.stubGlobal('fetch', serve(undefined, staff, init => { calls.push(init); return jsonResponse(200, workflow([], [], 8)); }));
  show();
  await screen.findByText('CS26-1039');
  fireEvent.change(screen.getByLabelText('답변'), { target: { value: '  펌웨어 1.13에서 보완됨  ' } });
  fireEvent.change(screen.getByLabelText('고객 회신 문안'), { target: { value: '' } });
  fireEvent.click(within(screen.getByRole('form', { name: '검토 답변 입력' })).getByRole('button', { name: '답변 보내기' }));
  expect(await screen.findByText('답변을 보냈습니다. C/S 담당자가 확인합니다.')).toBeTruthy();
  expect(calls).toHaveLength(1);
  expect((calls[0].headers as Record<string, string>)['X-CSRF-Token']).toBe('tok');
  expect(JSON.parse(String(calls[0].body))).toEqual({ version: 7, result: '펌웨어 1.13에서 보완됨', reply_content: null });
});

test('판본 충돌(409)은 안내하고 최신 상태를 다시 읽는다', async () => {
  const fetch = serve(undefined, staff, () => jsonResponse(409, errorBody('CS_WORKFLOW_VERSION_CONFLICT', '충돌')));
  vi.stubGlobal('fetch', fetch);
  show();
  await screen.findByText('CS26-1039');
  fireEvent.change(screen.getByLabelText('답변'), { target: { value: '답변' } });
  fireEvent.click(within(screen.getByRole('form', { name: '검토 답변 입력' })).getByRole('button', { name: '답변 보내기' }));
  expect((await screen.findByRole('alert')).textContent).toContain('다른 사용자가 먼저 상태를 바꿨습니다');
  const reads = () => fetch.mock.calls.filter(call => String(call[0]).endsWith('/workflow')).length;
  await waitFor(() => expect(reads()).toBeGreaterThanOrEqual(2));
});

test('답변이 비어 있으면 요청을 보내지 않는다', async () => {
  const fetch = serve();
  vi.stubGlobal('fetch', fetch);
  show();
  await screen.findByText('CS26-1039');
  fireEvent.click(within(screen.getByRole('form', { name: '검토 답변 입력' })).getByRole('button', { name: '답변 보내기' }));
  expect(fetch.mock.calls.some(call => String(call[0]).endsWith('/dept-result'))).toBe(false);
});

test('부서 담당자가 아니거나 이미 답변한 요청은 답변할 수 없다', async () => {
  vi.stubGlobal('fetch', serve(undefined, { data: { ...staff.data, dept: null, roles: ['cs_agent'] } }));
  show();
  await screen.findByText('CS26-1039');
  expect((screen.getByLabelText('답변') as HTMLTextAreaElement).disabled).toBe(true);
  expect(screen.getByText(/부서 담당자 계정만 답변할 수 있습니다/)).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', serve(workflow([transfer(1, 'RESULT_SUBMITTED')], [result1])));
  show();
  await screen.findByText('CS26-1039');
  expect((screen.getByLabelText('답변') as HTMLTextAreaElement).disabled).toBe(true);
  expect(screen.getByText(/이미 답변했거나 요청이 끝났습니다/)).toBeTruthy();
});

test('접근 거절(403)은 오류 화면으로 보인다', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).endsWith('/platform/me') ? jsonResponse(200, staff) : jsonResponse(403, errorBody('FORBIDDEN', '권한이 없습니다.'))));
  show();
  expect(await screen.findByText('권한이 없습니다')).toBeTruthy();
});
