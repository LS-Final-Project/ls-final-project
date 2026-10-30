// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import { MOCK_COMPARISON, MOCK_DETAIL, MOCK_PROPOSALS, MOCK_REQUIREMENTS } from '../mocks/rfp-table';
import { errorBody, jsonResponse, renderApp } from '../test/render';

const PROJECT = '33333333-3333-4333-8333-333333333333';
const PATH = `/tft/projects/${PROJECT}/table`;
const ok = (data: unknown) => jsonResponse(200, { data, meta: { request_id: 'x' } });
type Call = { url: string; method: string; body: Record<string, unknown>; key: string | null };
let calls: Call[];
let write: (call: Call) => Response;
let proposals: unknown[];
let requirements: unknown[];

function server() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const call = { url, method, body: JSON.parse((init?.body as string) || '{}'), key: new Headers(init?.headers).get('Idempotency-Key') };
    calls.push(call);
    if (method === 'POST') return write(call);
    if (url.includes('/platform/me')) return jsonResponse(401, errorBody('X', '세션'));
    if (url.includes('/comparison')) return ok(MOCK_COMPARISON);
    if (url.includes('/proposals')) return ok({ items: proposals });
    if (url.includes('/requirements?') || url.endsWith('/requirements')) return ok({ items: requirements, next_cursor: null });
    if (url.includes('/requirements/')) return ok(MOCK_DETAIL);
    return ok({ name: '합성 프로젝트' });
  }));
  setApiMode('live');
}

beforeEach(() => {
  calls = [];
  proposals = MOCK_PROPOSALS;
  requirements = MOCK_REQUIREMENTS;
  write = () => ok(MOCK_DETAIL);
  server();
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); setApiMode('mock'); });

const open = async () => {
  renderApp(PATH);
  fireEvent.click(await screen.findByRole('button', { name: 'R-01' }));
  await screen.findByRole('table', { name: '자사 비교' });
};
const posts = (part: string) => calls.filter(c => c.method === 'POST' && c.url.includes(part));

describe('자사 대응표 목록', () => {
  it('요구를 라벨로 보여 주고 고른 행을 표시한다', async () => {
    renderApp(PATH);
    const table = await screen.findByRole('table', { name: '자사 대응표' });
    await within(table).findByText('사양 차이');
    expect(within(table).getByText('근거 부족')).toBeTruthy();
    expect(table.classList.contains('free')).toBe(true);
    expect(screen.getByRole('tab').textContent).toContain('자사 대응표');
    await screen.findByText('합성 프로젝트');
    const button = screen.getByRole('button', { name: 'R-01' });
    fireEvent.click(button);
    await waitFor(() => expect(button.closest('tr')?.classList.contains('sel')).toBe(true));
  });

  it('조회조건을 서버 필터로 보내고 쪽과 선택을 처음으로 돌린다', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('상태'), { target: { value: 'SPEC_GAP' } });
    fireEvent.click(screen.getByLabelText('재검토만'));
    await waitFor(() => expect(calls.some(c => c.url.includes('status=SPEC_GAP') && c.url.includes('needs_rereview=true'))).toBe(true));
    expect(screen.queryByRole('table', { name: '자사 비교' })).toBeNull();
    fireEvent.click(screen.getByLabelText('재검토만'));
    fireEvent.change(screen.getByLabelText('상태'), { target: { value: 'CONDITIONAL' } });
    await waitFor(() => expect(calls.some(c => c.url.includes('status=CONDITIONAL'))).toBe(true));
    expect(calls.find(c => c.url.includes('status=CONDITIONAL'))?.url).not.toContain('needs_rereview');
  });

  it('빈 결과와 오류를 구분한다', async () => {
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
  });
});

describe('요구 상세', () => {
  it('원문 인용과 판단, 자사 비교 근거를 보여 준다', async () => {
    await open();
    expect(screen.getByText(/1500 kVA minimum/)).toBeTruthy();
    expect(screen.getByText('1500 kVA 이상', { selector: '.kv-box .ro' })).toBeTruthy();
    expect(screen.getByText(/AI 작성/)).toBeTruthy();
    const table = screen.getByRole('table', { name: '자사 비교' });
    await within(table).findByText('합성-TRF-1000');
    expect(within(table).getByText('차이')).toBeTruthy();
    expect(within(table).getByText('합성 데이터시트 2.2')).toBeTruthy();
  });
});

describe('변경 제안', () => {
  it('승인은 제안 판본과 멱등 키를 보내고 표를 새로 읽는다', async () => {
    await open();
    const before = calls.filter(c => c.url.includes('/requirements?') || c.url.endsWith('/requirements')).length;
    fireEvent.click(await screen.findByRole('button', { name: '승인' }));
    await waitFor(() => expect(posts('/approve')).toHaveLength(1));
    const [call] = posts('/approve');
    expect(call.body).toEqual({ version: MOCK_PROPOSALS[0].version });
    expect(call.key).toBeTruthy();
    await waitFor(() => expect(calls.filter(c => c.url.includes('/requirements?') || c.url.endsWith('/requirements')).length).toBeGreaterThan(before));
  });

  it('판본 충돌은 다음 행동을 알리고 입력을 지키며 기각은 그대로 된다', async () => {
    write = () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'conflict'));
    await open();
    const reason = await screen.findByLabelText('기각 사유') as HTMLInputElement;
    fireEvent.change(reason, { target: { value: '근거 부족' } });
    fireEvent.click(screen.getByRole('button', { name: '승인' }));
    expect((await screen.findByRole('alert')).textContent).toContain('다시 제안');
    expect(reason.value).toBe('근거 부족');
    expect((screen.getByRole('button', { name: '기각' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('이미 결정된 제안은 다시 불러오게 한다', async () => {
    write = () => jsonResponse(409, errorBody('TFT_PROPOSAL_DECIDED', 'decided'));
    await open();
    fireEvent.click(await screen.findByRole('button', { name: '승인' }));
    await screen.findByText(/이미 결정된/);
    const before = calls.length;
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(before));
  });

  it('기각은 사유가 있어야 하고 사유를 보낸다', async () => {
    write = () => ok(MOCK_PROPOSALS[0]);
    await open();
    const reject = await screen.findByRole('button', { name: '기각' }) as HTMLButtonElement;
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
    const approve = await screen.findByRole('button', { name: '승인' });
    fireEvent.click(approve);
    await waitFor(() => expect(posts('/approve')).toHaveLength(1));
    await screen.findByRole('alert');
    status = 403;
    fireEvent.click(approve);
    await waitFor(() => expect(posts('/approve')).toHaveLength(2));
    await waitFor(() => expect((approve as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(approve);
    await waitFor(() => expect(posts('/approve')).toHaveLength(3));
    const [a, b, c] = posts('/approve').map(x => x.key);
    expect(a).toBe(b);
    expect(c).not.toBe(b);
  });

  it('새 제안은 바뀌는 칸만 보내고, 바뀌는 칸과 근거가 없으면 보낼 수 없다', async () => {
    write = () => ok(MOCK_PROPOSALS[0]);
    proposals = [];
    await open();
    await screen.findByText('대기 중인 제안이 없습니다.');
    const submit = screen.getByRole('button', { name: '변경 제안' }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('바꿀 상태'), { target: { value: 'CONDITIONAL' } });
    expect(submit.disabled).toBe(true); // 근거가 없다
    fireEvent.change(screen.getByLabelText('바꿀 상태'), { target: { value: 'SPEC_GAP' } });
    fireEvent.change(screen.getByLabelText('제안 근거'), { target: { value: '근거' } });
    expect(submit.disabled).toBe(true); // 지금 상태와 같아 바뀌는 칸이 없다
    fireEvent.change(screen.getByLabelText('바꿀 상태'), { target: { value: 'CONDITIONAL' } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => expect(posts('/proposals')).toHaveLength(1));
    expect(posts('/proposals')[0].body).toEqual({ requirement_id: MOCK_DETAIL.requirement_id, proposed: { status: 'CONDITIONAL' }, rationale: '근거' });
    await waitFor(() => expect((screen.getByLabelText('제안 근거') as HTMLTextAreaElement).value).toBe(''));
  });

  it('부서 검토 계정에는 승인·기각을 내지 않는다', async () => {
    const roles = (url: string) => url.includes('/platform/me');
    const inner = vi.mocked(globalThis.fetch);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => roles(String(input))
      ? jsonResponse(200, { data: { display_name: '합성', dept: 'RND_FW', roles: ['dept_staff'] }, meta: { request_id: 'x' } })
      : inner(input, init)));
    await open();
    await screen.findByText(/기술 검토 의견/);
    await waitFor(() => expect(screen.queryByRole('button', { name: '승인' })).toBeNull());
    expect(screen.getByRole('button', { name: '변경 제안' })).toBeTruthy();
  });
});

it('옛 프로젝트 상세에서 대응표로 간다', async () => {
  renderApp(`/tft/projects/${PROJECT}`);
  expect((await screen.findByRole('link', { name: '자사 대응표' })).getAttribute('href')).toBe(PATH);
});
