// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sessionCsrfToken, setApiMode } from '../api/client';
import { MOCK_COMPARISON, MOCK_DETAIL, MOCK_PROPOSALS, MOCK_REQUIREMENTS } from '../mocks/rfp-table';
import { errorBody, jsonResponse, renderApp } from '../test/render';

const PROJECT = '33333333-3333-4333-8333-333333333333';
const PATH = `/tft/projects/${PROJECT}/table`;
const ok = (data: unknown) => jsonResponse(200, { data, meta: { request_id: 'x' } });
type Call = { url: string; method: string; body: Record<string, unknown>; key: string | null; csrf: string | null };
let calls: Call[];
let write: (call: Call) => Response;
let detail: unknown;
let proposals: unknown[];
let requirements: unknown[];
let nextCursor: string | null;

function server() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const call = { url, method, body: JSON.parse((init?.body as string) || '{}'), key: new Headers(init?.headers).get('Idempotency-Key'), csrf: new Headers(init?.headers).get('X-CSRF-Token') };
    calls.push(call);
    if (method === 'POST') return write(call);
    if (url.includes('/platform/me')) return new Response(JSON.stringify({ data: { display_name: '합성', dept: null, roles: ['sales'] }, meta: { request_id: 'x' } }), { status: 200, headers: { 'X-CSRF-Token': 'csrf-1' } });
    if (url.includes('/comparison')) return ok(MOCK_COMPARISON);
    if (url.includes('/proposals')) return ok({ items: proposals });
    if (url.endsWith('/documents')) return ok({ items: [{ document_version_id: 'd1', title: '사양서 v2' }] });
    if (url.includes('/requirements?')) return ok({ items: url.includes('cursor=next') ? [{ ...MOCK_REQUIREMENTS[3], requirement_id: 'more', code: 'R-99' }] : requirements, next_cursor: url.includes('cursor=next') ? null : nextCursor });
    if (url.includes('/requirements/')) return ok(detail);
    return ok({ name: '합성 프로젝트', requirements_count: 48, unresolved_mandatory_count: 3, requirement_counts: { needs_rereview: 2 } });
  }));
  setApiMode('live');
}

beforeEach(() => {
  calls = [];
  proposals = MOCK_PROPOSALS;
  requirements = MOCK_REQUIREMENTS;
  nextCursor = null;
  write = () => ok(MOCK_DETAIL);
  detail = MOCK_DETAIL;
  server();
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); setApiMode('mock'); });

const open = async () => {
  renderApp(PATH);
  fireEvent.click(await screen.findByRole('button', { name: 'R-01' }));
  await screen.findByRole('table', { name: '자사 비교' });
};
const dept = () => {
  const inner = vi.mocked(globalThis.fetch);
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input).includes('/platform/me')
    ? jsonResponse(200, { data: { display_name: '합성', dept: 'RND_FW', roles: ['dept_staff'] }, meta: { request_id: 'x' } })
    : inner(input, init)));
};
const posts = (part: string) => calls.filter(c => c.method === 'POST' && c.url.includes(part));

describe('자사 대응표', () => {
  it('보드의 말과 열로 요구를 보여 주고 고른 행을 표시한다', async () => {
    renderApp(PATH);
    const table = await screen.findByRole('table', { name: '자사 대응표' });
    await within(table).findByText('사양 차이');
    expect(within(table).getByText('근거 있음')).toBeTruthy();
    expect(within(table).getByText('조건부 검토')).toBeTruthy();
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent)).toEqual(
      ['No', '요구', '장비', '작업', '속성', '요구값', '성격', '상태', '보완 경로', '질문', '재검토'],
    );
    expect(table.classList.contains('free')).toBe(true);
    expect(screen.getByRole('tab').textContent).toContain('자사 대응표');
    await screen.findByText(/합성 프로젝트, 요구 48건, 미해결 필수 3, 재검토 2/);
    await screen.findByText('표시 4건 / 조회 48건');
    const button = screen.getByRole('button', { name: 'R-01' });
    fireEvent.click(button);
    await waitFor(() => expect(button.closest('tr')?.classList.contains('sel')).toBe(true));
  });

  it('조회조건을 서버 필터로 보내고 선택을 푼다', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('상태'), { target: { value: 'SPEC_GAP' } });
    fireEvent.change(screen.getByLabelText('재검토'), { target: { value: 'false' } });
    await waitFor(() => expect(calls.some(c => c.url.includes('status=SPEC_GAP') && c.url.includes('needs_rereview=false'))).toBe(true));
    expect(screen.queryByRole('table', { name: '자사 비교' })).toBeNull();
    fireEvent.change(screen.getByLabelText('재검토'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('상태'), { target: { value: 'CONDITIONAL' } });
    await waitFor(() => expect(calls.some(c => c.url.includes('status=CONDITIONAL'))).toBe(true));
    expect(calls.find(c => c.url.includes('status=CONDITIONAL'))?.url).not.toContain('needs_rereview');
  });

  it('값이 그대로면 선택과 입력 중인 제안을 지키고 다시 부르지 않는다', async () => {
    await open();
    fireEvent.click(screen.getAllByRole('button', { name: '변경 제안' })[0]);
    await screen.findByRole('button', { name: '제안 저장' });
    const lists = () => calls.filter(c => c.url.includes('/requirements?')).length;
    const before = lists();
    fireEvent.blur(screen.getByLabelText('장비'));
    fireEvent.submit(screen.getByRole('form', { name: '조회조건' }));
    expect(screen.getByRole('table', { name: '자사 비교' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '제안 저장' })).toBeTruthy();
    expect(lists()).toBe(before);
  });

  it('입력칸을 떠나는 것만으로는 조회하지 않고, 조건을 바꿔도 표를 비우지 않는다', async () => {
    renderApp(PATH);
    const row = await screen.findByRole('button', { name: 'R-01' });
    fireEvent.change(screen.getByLabelText('장비'), { target: { value: '변압기' } });
    fireEvent.blur(screen.getByLabelText('장비'));
    expect(calls.some(c => decodeURIComponent(c.url).includes('equipment='))).toBe(false);
    fireEvent.submit(screen.getByRole('form', { name: '조회조건' }));
    expect(screen.getByRole('button', { name: 'R-01' })).toBe(row);
    await waitFor(() => expect(calls.some(c => decodeURIComponent(c.url).includes('equipment=변압기'))).toBe(true));
  });

  it('조건을 걸면 프로젝트 전체 건수를 표시하지 않는다', async () => {
    renderApp(PATH);
    await screen.findByText('표시 4건 / 조회 48건');
    fireEvent.change(screen.getByLabelText('상태'), { target: { value: 'SPEC_GAP' } });
    await screen.findByText('표시 4건');
  });

  it('문서 판본·장비·작업도 서버 필터다', async () => {
    renderApp(PATH);
    await screen.findByRole('button', { name: 'R-01' });
    await screen.findByRole('option', { name: '사양서 v2' });
    fireEvent.change(screen.getByLabelText('문서 판본'), { target: { value: 'd1' } });
    fireEvent.change(screen.getByLabelText('장비'), { target: { value: ' 변압기 ' } });
    fireEvent.submit(screen.getByRole('form', { name: '조회조건' }));
    await waitFor(() => expect(calls.some(c => c.url.includes('document_version_id=d1') && decodeURIComponent(c.url).includes('equipment=변압기'))).toBe(true));
  });

  it('더 보기는 뒤에 이어 붙이고 끝을 알린다', async () => {
    nextCursor = 'next';
    renderApp(PATH);
    fireEvent.click(await screen.findByRole('button', { name: '50건 더 보기' }));
    await screen.findByRole('button', { name: 'R-99' });
    expect(screen.getByRole('button', { name: 'R-01' })).toBeTruthy();
    expect(screen.getByText('표시 5건 / 조회 48건')).toBeTruthy();
    expect(screen.getByText('마지막입니다')).toBeTruthy();
    expect(calls.some(c => c.url.includes('limit=50') && c.url.includes('cursor=next'))).toBe(true);
  });

  it('한 번에 볼 건수를 바꾼다', async () => {
    renderApp(PATH);
    await screen.findByRole('button', { name: 'R-01' });
    fireEvent.change(screen.getByLabelText('한 번에'), { target: { value: '100' } });
    await waitFor(() => expect(calls.some(c => c.url.includes('limit=100'))).toBe(true));
  });

  it('빈 결과를 표 안에 보여 준다', async () => {
    requirements = [];
    renderApp(PATH);
    await screen.findByText('조건에 맞는 요구가 없습니다.');
  });

  it('목록이 실패하면 빈 표로 보이지 않는다', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).includes('/requirements') ? jsonResponse(500, errorBody('TFT_X', '실패')) : jsonResponse(401, errorBody('X', '세션'))));
    renderApp(PATH);
    expect((await screen.findByRole('alert')).textContent).toContain('실패');
    expect(screen.queryByText('조건에 맞는 요구가 없습니다.')).toBeNull();
  });

  it('업무 UI가 꺼졌으면 요구를 부르지 않는다', async () => {
    vi.stubEnv('DEV', false);
    renderApp(PATH);
    await waitFor(() => expect(document.querySelector('.ls-root')).not.toBeNull());
    expect(calls.some(c => c.url.includes('/requirements'))).toBe(false);
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('요구를 고르기 전에는 변경 제안을 열 수 없다', async () => {
    renderApp(PATH);
    await screen.findByRole('button', { name: 'R-01' });
    expect((screen.getByRole('button', { name: '변경 제안' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('승인 대기 건수로 승인 화면에 간다', async () => {
    renderApp(PATH);
    const link = await screen.findByRole('link', { name: '승인 대기 1' });
    expect(link.getAttribute('href')).toBe(`${PATH}/approvals`);
  });

  it('부서 검토 계정은 승인 대기를 막고 이유를 보인다', async () => {
    dept();
    renderApp(PATH);
    const button = await screen.findByRole('button', { name: /승인 대기/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe('담당 또는 책임자만 승인합니다');
  });
});

describe('자사 비교와 판단', () => {
  it('비교 근거와 판단을 나란히 보여 준다', async () => {
    await open();
    const table = screen.getByRole('table', { name: '자사 비교' });
    await within(table).findByText('합성-TRF-1000');
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent)).toEqual(
      ['자사 제품', '변형', '수행 범위', '상태', '자사 값', '비교 가능', '결과', '근거'],
    );
    expect(within(table).getByText('다름')).toBeTruthy();
    expect(within(table).getByText('현행')).toBeTruthy();
    expect(within(table).getByText('합성 데이터시트 2.2')).toBeTruthy();
    expect(screen.getByText(/1500 kVA minimum/)).toBeTruthy();
    expect(screen.getByRole('heading', { name: /판단, R-01/ }).textContent).toContain('AI 작성');
    await screen.findByText('검색: 문서 2건, 실패 없음. 가격과 표준 납기는 비교하지 않음');
  });
});

describe('변경 제안', () => {
  const form = async () => {
    await open();
    fireEvent.click(screen.getAllByRole('button', { name: '변경 제안' })[0]);
    await screen.findByRole('heading', { name: /변경 제안, R-01/ });
    return screen.getByRole('button', { name: '제안 저장' }) as HTMLButtonElement;
  };

  it('바꾸는 칸만 보내고, 바뀌는 칸과 사유가 없으면 저장할 수 없다', async () => {
    write = () => ok(MOCK_PROPOSALS[0]);
    const submit = await form();
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('상태', { selector: '#proposal-status' }), { target: { value: 'CONDITIONAL' } });
    expect(submit.disabled).toBe(true); // 사유가 없다
    fireEvent.change(screen.getByLabelText('상태', { selector: '#proposal-status' }), { target: { value: 'SPEC_GAP' } });
    fireEvent.change(screen.getByLabelText('제안 사유'), { target: { value: '근거' } });
    expect(submit.disabled).toBe(true); // 지금 상태와 같아 바뀌는 칸이 없다
    fireEvent.change(screen.getByLabelText('상태', { selector: '#proposal-status' }), { target: { value: 'CONDITIONAL' } });
    fireEvent.change(screen.getByLabelText('근거'), { target: { value: '조항 2.1, 성적서' } });
    fireEvent.click(submit);
    await waitFor(() => expect(posts('/proposals')).toHaveLength(1));
    expect(posts('/proposals')[0].body).toEqual({
      requirement_id: MOCK_DETAIL.requirement_id, proposed: { status: 'CONDITIONAL' }, rationale: '근거', evidence_refs: ['조항 2.1', '성적서'],
    });
    await waitFor(() => expect(screen.queryByRole('button', { name: '제안 저장' })).toBeNull());
  });

  it('미확인을 바꾸면 목록으로 보낸다', async () => {
    write = () => ok(MOCK_PROPOSALS[0]);
    const submit = await form();
    fireEvent.change(screen.getByLabelText('미확인'), { target: { value: '시험 성적서, 고객 허용' } });
    fireEvent.change(screen.getByLabelText('제안 사유'), { target: { value: '근거' } });
    fireEvent.click(submit);
    await waitFor(() => expect(posts('/proposals')).toHaveLength(1));
    expect(posts('/proposals')[0].body.proposed).toEqual({ unresolved: ['시험 성적서', '고객 허용'] });
  });

  it('다시 불러온 뒤 손대지 않은 미확인은 최신 값을 보이고 제안에 넣지 않는다', async () => {
    const withUnresolved = (unresolved: string[]) => ({ ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, unresolved } });
    detail = withUnresolved(['A']);
    write = () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'conflict'));
    await form();
    fireEvent.change(screen.getByLabelText('상태', { selector: '#proposal-status' }), { target: { value: 'CONDITIONAL' } });
    fireEvent.change(screen.getByLabelText('제안 사유'), { target: { value: '쓴 사유' } });
    fireEvent.click(screen.getByRole('button', { name: '제안 저장' }));
    await screen.findByRole('alert');
    detail = withUnresolved(['A', 'B']);
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    const field = screen.getByLabelText('미확인') as HTMLInputElement;
    await waitFor(() => expect(field.value).toBe('A, B'));
    write = () => ok(MOCK_DETAIL);
    fireEvent.click(screen.getByRole('button', { name: '제안 저장' }));
    await waitFor(() => expect(posts('/proposals')).toHaveLength(2));
    expect(posts('/proposals')[1].body.proposed).toEqual({ status: 'CONDITIONAL' });
  });

  it('직접 고친 미확인은 다시 불러와도 남고 제안에 들어간다', async () => {
    const withUnresolved = (unresolved: string[]) => ({ ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, unresolved } });
    detail = withUnresolved(['A']);
    write = () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'conflict'));
    await form();
    fireEvent.change(screen.getByLabelText('미확인'), { target: { value: 'A, 내 항목' } });
    fireEvent.change(screen.getByLabelText('제안 사유'), { target: { value: '쓴 사유' } });
    fireEvent.click(screen.getByRole('button', { name: '제안 저장' }));
    await screen.findByRole('alert');
    detail = withUnresolved(['A', 'B']);
    const reads = () => calls.filter(c => c.method === 'GET' && c.url.includes('/requirements/')).length;
    const before = reads();
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
    await screen.findByRole('button', { name: '제안 저장' });
    expect((screen.getByLabelText('미확인') as HTMLInputElement).value).toBe('A, 내 항목');
    write = () => ok(MOCK_DETAIL);
    fireEvent.click(screen.getByRole('button', { name: '제안 저장' }));
    await waitFor(() => expect(posts('/proposals')).toHaveLength(2));
    expect(posts('/proposals')[1].body.proposed).toEqual({ unresolved: ['A', '내 항목'] });
  });

  it('요구 상세로 간다', async () => {
    await form();
    expect(screen.getByRole('link', { name: '요구 상세' }).getAttribute('href')).toBe(`/tft/projects/${PROJECT}/requirements/${MOCK_DETAIL.requirement_id}`);
  });

  it('저장 충돌은 폼과 입력을 지키고 최신 내용을 다시 읽는다', async () => {
    write = () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'conflict'));
    await form();
    fireEvent.change(screen.getByLabelText('상태', { selector: '#proposal-status' }), { target: { value: 'CONDITIONAL' } });
    fireEvent.change(screen.getByLabelText('제안 사유'), { target: { value: '쓴 사유' } });
    fireEvent.click(screen.getByRole('button', { name: '제안 저장' }));
    expect((await screen.findByRole('alert')).textContent).toContain('입력한 내용은 남습니다');
    const reads = () => calls.filter(c => c.url.endsWith(MOCK_DETAIL.requirement_id) || c.url.includes('/requirements/')).length;
    const before = reads();
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
    expect((screen.getByLabelText('제안 사유') as HTMLTextAreaElement).value).toBe('쓴 사유');
    expect(screen.getByRole('button', { name: '제안 저장' })).toBeTruthy();
  });

  it('취소하면 닫힌다', async () => {
    await form();
    fireEvent.click(screen.getByRole('button', { name: '취소' }));
    expect(screen.queryByRole('button', { name: '제안 저장' })).toBeNull();
  });
});

describe('승인 화면', () => {
  const APPROVALS = `${PATH}/approvals`;
  const open = async () => {
    renderApp(APPROVALS);
    fireEvent.click(await screen.findByRole('button', { name: MOCK_PROPOSALS[0].proposal_id.slice(0, 8) }));
    await screen.findByRole('heading', { name: /제안, 요구/ });
  };

  it('세 입구가 한 목록이고 기본은 승인 대기다', async () => {
    renderApp(APPROVALS);
    const table = await screen.findByRole('table', { name: '변경 제안' });
    await within(table).findByText('화면 수정');
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent)).toEqual(
      ['No', '제안', '들어온 곳', '요구', '항목', '현재', '제안', '제안자', '일시', '상태'],
    );
    expect(within(table).getByText(/사양 차이/)).toBeTruthy();
    expect(within(table).getByText(/조건부 검토/)).toBeTruthy();
    await within(table).findByText('R-01');
    expect(calls.some(c => c.url.includes('/proposals?status=PENDING'))).toBe(true);
    fireEvent.change(screen.getByLabelText('들어온 곳'), { target: { value: 'customer_reply' } });
    await waitFor(() => expect(calls.some(c => c.url.includes('origin=customer_reply'))).toBe(true));
  });

  it('승인은 제안 판본·메모·멱등 키를 보내고 결과를 알린다', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('메모'), { target: { value: ' 확인함 ' } });
    fireEvent.click(screen.getByRole('button', { name: '승인' }));
    await waitFor(() => expect(posts('/approve')).toHaveLength(1));
    const [call] = posts('/approve');
    expect(call.body).toEqual({ version: MOCK_PROPOSALS[0].version, note: '확인함' });
    expect(call.key).toBeTruthy();
    await screen.findByText(/승인 완료, 요구 R-01 판본 1/);
  });

  it('세션의 CSRF 토큰을 쓰기 요청에 싣는다', async () => {
    await open();
    await waitFor(() => expect(sessionCsrfToken()).toBe('csrf-1'));
    fireEvent.click(screen.getByRole('button', { name: '승인' }));
    await waitFor(() => expect(posts('/approve')).toHaveLength(1));
    expect(posts('/approve')[0].csrf).toBe('csrf-1');
  });

  it('5xx 뒤 입력을 바꾸면 새 키로 보낸다', async () => {
    write = () => jsonResponse(500, errorBody('TFT_X', '실패'));
    await open();
    fireEvent.change(screen.getByLabelText('기각 사유'), { target: { value: 'A' } });
    fireEvent.click(screen.getByRole('button', { name: '기각' }));
    await waitFor(() => expect(posts('/reject')).toHaveLength(1));
    await screen.findByRole('alert');
    fireEvent.change(screen.getByLabelText('기각 사유'), { target: { value: 'B' } });
    fireEvent.click(screen.getByRole('button', { name: '기각' }));
    await waitFor(() => expect(posts('/reject')).toHaveLength(2));
    const [a, b] = posts('/reject');
    expect((a.body as { reason: string }).reason).toBe('A');
    expect((b.body as { reason: string }).reason).toBe('B');
    expect(a.key).not.toBe(b.key);
  });

  it('승인 뒤 프로젝트 건수를 다시 읽는다', async () => {
    await open();
    const reads = () => calls.filter(c => c.url.endsWith(`/tft/projects/${PROJECT}`)).length;
    const before = reads();
    fireEvent.click(screen.getByRole('button', { name: '승인' }));
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
  });

  it('메모가 없으면 version만 보낸다', async () => {
    await open();
    fireEvent.click(screen.getByRole('button', { name: '승인' }));
    await waitFor(() => expect(posts('/approve')).toHaveLength(1));
    expect(posts('/approve')[0].body).toEqual({ version: 1 });
  });

  it('판본 충돌은 다음 행동을 알리고 입력을 지키며 기각은 그대로 된다', async () => {
    write = () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'conflict'));
    await open();
    const reason = screen.getByLabelText('기각 사유') as HTMLInputElement;
    fireEvent.change(reason, { target: { value: '근거 부족' } });
    fireEvent.change(screen.getByLabelText('메모'), { target: { value: '메모' } });
    fireEvent.click(screen.getByRole('button', { name: '승인' }));
    expect((await screen.findByRole('alert')).textContent).toContain('다른 사람이 먼저 바꿨습니다');
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    await waitFor(() => expect(reason.value).toBe('근거 부족'));
    expect((screen.getByLabelText('메모') as HTMLInputElement).value).toBe('메모');
    expect((screen.getByRole('button', { name: '기각' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('이미 처리된 제안은 다시 불러오게 한다', async () => {
    write = () => jsonResponse(409, errorBody('TFT_PROPOSAL_DECIDED', 'decided'));
    await open();
    fireEvent.click(screen.getByRole('button', { name: '승인' }));
    await screen.findByText(/이미 처리된/);
    const before = calls.length;
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(before));
  });

  it('기각은 사유가 있어야 하고 사유를 보낸다', async () => {
    write = () => ok(MOCK_PROPOSALS[0]);
    await open();
    const reject = screen.getByRole('button', { name: '기각' }) as HTMLButtonElement;
    expect(reject.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('기각 사유'), { target: { value: '  원문과 다름 ' } });
    fireEvent.click(reject);
    await waitFor(() => expect(posts('/reject')).toHaveLength(1));
    expect(posts('/reject')[0].body).toEqual({ version: 1, reason: '원문과 다름' });
  });

  it('서버 오류 재전송은 같은 키, 거절 뒤 새 시도는 새 키다', async () => {
    let status = 500;
    write = () => jsonResponse(status, errorBody('TFT_X', '실패'));
    await open();
    const approve = screen.getByRole('button', { name: '승인' }) as HTMLButtonElement;
    fireEvent.click(approve);
    await waitFor(() => expect(posts('/approve')).toHaveLength(1));
    await screen.findByRole('alert');
    status = 403;
    fireEvent.click(approve);
    await waitFor(() => expect(posts('/approve')).toHaveLength(2));
    await waitFor(() => expect(approve.disabled).toBe(false));
    fireEvent.click(approve);
    await waitFor(() => expect(posts('/approve')).toHaveLength(3));
    const [a, b, c] = posts('/approve').map(x => x.key);
    expect(a).toBe(b);
    expect(c).not.toBe(b);
  });

  it('처리된 제안은 결정 단추 없이 기록만 보인다', async () => {
    proposals = [{ ...MOCK_PROPOSALS[0], status: 'APPROVED', decided_at: '2026-10-28T09:41:00Z' }];
    await open();
    await screen.findByText(/처리된 제안은 다시 바꿀 수 없습니다/);
    expect(screen.queryByRole('button', { name: '승인' })).toBeNull();
  });

  it('부서 검토 계정에는 승인·기각을 내지 않는다', async () => {
    dept();
    await open();
    await screen.findByText('담당 또는 책임자만 승인합니다.');
    expect(screen.queryByRole('button', { name: '승인' })).toBeNull();
    expect(screen.queryByRole('button', { name: '기각' })).toBeNull();
  });

  it('목록이 실패하면 빈 목록으로 보이지 않는다', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).includes('/proposals') ? jsonResponse(500, errorBody('TFT_X', '실패')) : jsonResponse(401, errorBody('X', '세션'))));
    renderApp(APPROVALS);
    expect((await screen.findByRole('alert')).textContent).toContain('실패');
    expect(screen.queryByText('조건에 맞는 제안이 없습니다.')).toBeNull();
  });
});

describe('셸 메뉴', () => {
  const links = () => [...document.querySelectorAll('.sidebar a')].map(a => [a.textContent, a.getAttribute('href')]);
  it('보드의 제안 지원 묶음이고 자사 대응표는 프로젝트를 연 뒤에 간다', async () => {
    renderApp(PATH);
    await waitFor(() => expect(links().map(l => l[0])).toEqual(expect.arrayContaining(['업무 홈', '접수 및 요구 검토', '자사 대응표', '보완 및 협력사 비교', '초안 및 검토 이력'])));
    expect(links().find(l => l[0] === '자사 대응표')?.[1]).toBe(PATH);
    expect([...document.querySelectorAll('.sidebar a[aria-current="page"]')].map(a => a.textContent)).toEqual(['자사 대응표']);
    cleanup();
    renderApp('/tft');
    await waitFor(() => expect(links().find(l => l[0] === '자사 대응표')).toBeTruthy());
    expect(links().find(l => l[0] === '자사 대응표')?.[1]).toBeNull();
  });
});
