// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import ImprovementTransitions from './ImprovementTransitions';
import type { ImprovementDetail } from '../api/cs-improvements';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
type Detail = ImprovementDetail['data'];
const ID = 'dddddddd-dddd-4ddd-8ddd-ddddddddddd1';
const dept = (over: Record<string, unknown> = {}) => ({ round: 1, dept_code: 'TS_AUTO', status: 'SENT', carried_over: false, sent_version: 2, reply_due_at: '2026-11-08T00:00:00Z', rejection_reviewed_at: null, rejection_reviewed_by: null, ...over });
const detail = (status: string, departments: unknown[] = [], over: Record<string, unknown> = {}) => ({
  request: { id: ID, version: 4, status, proposed_depts: ['TS_AUTO'], receiver_unconfirmed: false, ...over }, departments, evidence: [], next_owners: [], replies: [], actions: [], rounds: [],
}) as unknown as Detail;
type Post = { url: string; body: Record<string, unknown>; token: string | null };

function server(roles: string[], dept: string | undefined, reply: (post: Post) => Response = () => jsonResponse(200, { data: detail('SENT') })) {
  const posts: Post[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', roles, dept } }), { status: 200, headers: { 'X-CSRF-Token': 'tok' } });
    const post = { url: path, body: JSON.parse(String(init?.body ?? '{}')), token: new Headers(init?.headers).get('X-CSRF-Token') };
    posts.push(post);
    return reply(post);
  }));
  return posts;
}
function view(data: Detail, onChanged = vi.fn(), live = true) {
  setApiMode(live ? 'live' : 'mock');
  renderAt('/x', '/x', <ImprovementTransitions detail={data} onChanged={onChanged} />);
  return onChanged;
}
const button = (name: string | RegExp) => screen.getByRole('button', { name }) as HTMLButtonElement;
const ready = async (name: string | RegExp) => { await waitFor(() => expect(button(name).disabled).toBe(false)); };

test('초안은 검토 완료로 읽은 판본을 보내고, 제안 부서를 바꾸지 않았으면 부서를 보내지 않는다', async () => {
  const posts = server(['cs_agent'], undefined);
  const changed = view(detail('DRAFT'));
  await ready('검토 완료');
  fireEvent.click(button('검토 완료'));
  fireEvent.submit(screen.getByRole('form', { name: '검토 완료 입력' }));
  await screen.findByText('검토를 완료했습니다.');
  expect(posts[0].url).toMatch(/\/improvement-requests\/dddddddd-.*\/review$/);
  expect(posts[0].body).toEqual({ version: 4 });
  expect(posts[0].token).toBe('tok');
  expect(changed).toHaveBeenCalledWith(expect.objectContaining({ data: expect.anything() }));
});

test('제안 부서를 바꿔 검토 완료하면 바뀐 부서를 보낸다', async () => {
  const posts = server(['cs_agent'], undefined);
  view(detail('DRAFT'));
  await ready('검토 완료');
  fireEvent.click(button('검토 완료'));
  fireEvent.click(screen.getByLabelText(/펌웨어 개발/));
  fireEvent.submit(screen.getByRole('form', { name: '검토 완료 입력' }));
  await screen.findByText('검토를 완료했습니다.');
  expect(posts[0].body).toEqual({ version: 4, proposed_depts: ['TS_AUTO', 'RND_FW'] });
});

test('전달은 받는 부서를 하나 이상 골라야 하고, 부서·회신 기한·판본을 보낸다. 전달이 메일을 보내지 않는다고 안내한다', async () => {
  const posts = server(['cs_agent'], undefined);
  view(detail('REVIEWED', [], { proposed_depts: [], receiver_unconfirmed: true }));
  await ready('부서에 전달');
  fireEvent.click(button('부서에 전달'));
  expect(screen.getByText(/받는 부서가 확인되지 않은 문서 요청입니다/)).toBeTruthy();
  expect(screen.getByText(/부서에 메일을 보내지 않습니다/)).toBeTruthy();
  const submit = screen.getAllByRole('button', { name: '전달' })[0] as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText(/인증 \(CERT\)/));
  fireEvent.change(screen.getByLabelText('회신 기한(일)'), { target: { value: '10' } });
  fireEvent.submit(screen.getByRole('form', { name: '전달 입력' }));
  await screen.findByText('부서에 전달했습니다.');
  expect(posts[0].url).toMatch(/\/send$/);
  expect(posts[0].body).toEqual({ version: 4, dept_codes: ['CERT'], reply_due_days: 10 });
});

test('C/S 담당자는 부서 상태에 따라 다시 요청·반려 확인을 하고 부서 동작(수신 확인·회신·조치)은 보이지 않는다', async () => {
  const posts = server(['cs_agent'], undefined);
  view(detail('INFO_REQUESTED', [dept({ status: 'INFO_REQUESTED' }), dept({ dept_code: 'CERT', status: 'REJECTED' })]));
  await waitFor(() => expect(screen.getAllByRole('button', { name: '다시 요청' })).toHaveLength(2));
  expect(screen.getAllByRole('button', { name: '반려 확인' })).toHaveLength(1);
  expect(screen.queryByRole('button', { name: '수신 확인' })).toBeNull();
  expect(screen.queryByRole('button', { name: '회신' })).toBeNull();
  await ready('반려 확인');
  fireEvent.click(button('반려 확인'));
  await screen.findByText('반려를 확인했습니다.');
  expect(posts[0].url).toMatch(/\/departments\/CERT\/review-rejection$/);
  expect(posts[0].body).toEqual({ version: 4 });
  fireEvent.click(screen.getAllByRole('button', { name: '다시 요청' })[0]);
  fireEvent.change(screen.getByLabelText('사유'), { target: { value: '자료를 보완했습니다' } });
  fireEvent.submit(screen.getByRole('form', { name: '다시 요청 입력' }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].url).toMatch(/\/departments\/TS_AUTO\/resend$/);
  expect(posts[1].body).toEqual({ version: 4, reason: '자료를 보완했습니다', reply_due_days: 7 });
});

test('부서 담당자는 자기 부서 행에서만 수신 확인·회신·조치를 한다', async () => {
  const posts = server(['dept_staff'], 'TS_AUTO');
  view(detail('SENT', [dept(), dept({ dept_code: 'CERT' })]));
  await waitFor(() => expect(screen.getAllByRole('button', { name: '수신 확인' })).toHaveLength(1));
  expect(screen.queryByRole('button', { name: '다시 요청' })).toBeNull();
  fireEvent.click(button('수신 확인'));
  await screen.findByText('수신을 확인했습니다.');
  expect(posts[0].url).toMatch(/\/departments\/TS_AUTO\/acknowledge$/);
  expect(posts[0].body).toEqual({ version: 4 });
});

test('회신은 반려·추가 정보 요청에 사유가 있어야 보내고, 채택은 메모 없이 보낸다', async () => {
  const posts = server(['dept_staff'], 'TS_AUTO');
  view(detail('ACKNOWLEDGED', [dept({ status: 'ACKNOWLEDGED' })]));
  await ready('회신');
  fireEvent.click(button('회신'));
  fireEvent.change(screen.getByLabelText('회신'), { target: { value: 'REJECTED' } });
  expect((screen.getAllByRole('button', { name: '회신' }).at(-1) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('사유(필수)'), { target: { value: '우리 부서 소관이 아님' } });
  fireEvent.submit(screen.getByRole('form', { name: '회신 입력' }));
  await screen.findByText('회신을 남겼습니다.');
  expect(posts[0].url).toMatch(/\/departments\/TS_AUTO\/reply$/);
  expect(posts[0].body).toEqual({ version: 4, reply_type: 'REJECTED', reason: '우리 부서 소관이 아님' });
  fireEvent.click(button('회신'));
  fireEvent.submit(screen.getByRole('form', { name: '회신 입력' }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].body).toEqual({ version: 4, reply_type: 'ADOPTED', reason: null });
});

test('채택한 부서는 조치를 적용 범위·실제 적용일·근거와 함께 기록한다', async () => {
  const posts = server(['dept_staff'], 'TS_AUTO');
  view(detail('ADOPTED', [dept({ status: 'ADOPTED' })]));
  await ready('조치 입력');
  fireEvent.click(button('조치 입력'));
  const submit = screen.getAllByRole('button', { name: '조치 기록' })[0] as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('조치 내용'), { target: { value: '펌웨어 1.13 배포' } });
  fireEvent.change(screen.getByLabelText('적용 대상'), { target: { value: '출하 1000대' } });
  fireEvent.change(screen.getByLabelText('적용 범위 구분'), { target: { value: 'FIRMWARE_VERSION' } });
  fireEvent.change(screen.getByLabelText('적용 범위 값(선택)'), { target: { value: '1.13' } });
  fireEvent.change(screen.getByLabelText('실제 적용일'), { target: { value: '2026-11-01' } });
  fireEvent.change(screen.getByLabelText('근거'), { target: { value: '릴리스 노트 RN-113' } });
  fireEvent.submit(screen.getByRole('form', { name: '조치 입력' }));
  await screen.findByText('조치를 기록했습니다.');
  expect(posts[0].url).toMatch(/\/departments\/TS_AUTO\/action$/);
  expect(posts[0].body).toEqual({ version: 4, action: '펌웨어 1.13 배포', action_scope: '출하 1000대', action_date: '2026-11-01', action_evidence: '릴리스 노트 RN-113', scope_kind: 'FIRMWARE_VERSION', scope_value: '1.13' });
});

test('조치 뒤에는 후속 확인 계획, 그 뒤에는 완료 처리를 각각 글을 적어야 보낸다', async () => {
  const posts = server(['cs_agent'], undefined);
  view(detail('ACTION_TAKEN', [dept({ status: 'ACTION_TAKEN' })]));
  await ready('후속 확인 계획');
  fireEvent.click(button('후속 확인 계획'));
  expect((screen.getAllByRole('button', { name: '후속 확인 계획' }).at(-1) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('후속 확인 계획'), { target: { value: '  2주 뒤 재발 점검  ' } });
  fireEvent.submit(screen.getByRole('form', { name: '후속 확인 계획 입력' }));
  await screen.findByText('후속 확인 계획을 남겼습니다.');
  expect(posts[0].url).toMatch(/\/followup-plan$/);
  expect(posts[0].body).toEqual({ version: 4, followup_plan: '2주 뒤 재발 점검' });
  cleanup();
  view(detail('FOLLOW_UP', [dept({ status: 'ACTION_TAKEN' })]));
  await ready('완료 처리');
  fireEvent.click(button('완료 처리'));
  fireEvent.change(screen.getByLabelText('후속 확인 결과'), { target: { value: '재발 없음' } });
  fireEvent.submit(screen.getByRole('form', { name: '완료 처리 입력' }));
  await screen.findByText('완료 처리했습니다.');
  expect(posts[1].url).toMatch(/\/complete$/);
  expect(posts[1].body).toEqual({ version: 4, followup_result: '재발 없음' });
});

test('판본 충돌(409)과 서버 거부(4xx)는 이유를 보이고 최신 내용을 다시 읽는다', async () => {
  server(['cs_agent'], undefined, () => jsonResponse(409, errorBody('CS_REQUEST_VERSION_CONFLICT', '판본이 다릅니다')));
  const changed = view(detail('REVIEWED'));
  await ready('부서에 전달');
  fireEvent.click(button('부서에 전달'));
  fireEvent.submit(screen.getByRole('form', { name: '전달 입력' }));
  expect((await screen.findByRole('alert')).textContent).toContain('다른 사용자가 먼저 바꿨습니다');
  expect(changed).toHaveBeenCalledWith();
  cleanup();
  server(['cs_agent'], undefined, () => jsonResponse(422, errorBody('CS_DEPARTMENT_REQUIRED', '받는 부서를 정해야 합니다')));
  view(detail('REVIEWED'));
  await ready('부서에 전달');
  fireEvent.click(button('부서에 전달'));
  fireEvent.submit(screen.getByRole('form', { name: '전달 입력' }));
  expect((await screen.findByRole('alert')).textContent).toContain('받는 부서를 정해야 합니다');
});

test('응답을 알 수 없는 오류(5xx)는 같은 요청을 다시 보내지 않고 최신 내용을 읽는다', async () => {
  const posts = server(['cs_agent'], undefined, () => jsonResponse(503, errorBody('UNAVAILABLE', '일시 장애')));
  const changed = view(detail('REVIEWED'));
  await ready('부서에 전달');
  fireEvent.click(button('부서에 전달'));
  fireEvent.submit(screen.getByRole('form', { name: '전달 입력' }));
  expect((await screen.findByRole('alert')).textContent).toContain('같은 요청을 다시 보내지 않고');
  expect(posts).toHaveLength(1);
  expect(changed).toHaveBeenCalledWith();
});

test('모의 응답 모드와 완료된 요청에서는 실행하지 않고, 역할이 없으면 안내한다', async () => {
  server(['cs_agent'], undefined);
  view(detail('DRAFT'), vi.fn(), false);
  expect(screen.getByText('모의 응답에서는 실행하지 않습니다.')).toBeTruthy();
  cleanup();
  server(['cs_agent'], undefined);
  view(detail('COMPLETED'));
  expect(await screen.findByText('완료된 요청은 고칠 수 없습니다.')).toBeTruthy();
  cleanup();
  server(['viewer'], undefined);
  view(detail('DRAFT'));
  expect(await screen.findByText(/C\/S 담당자나 부서 담당자가 아니라 실행할 수 없습니다/)).toBeTruthy();
});
