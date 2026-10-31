// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import { MOCK_DETAIL } from '../mocks/rfp-table';
import { errorBody, jsonResponse, renderApp } from '../test/render';

const PROJECT = '33333333-3333-4333-8333-333333333333';
const ID = MOCK_DETAIL.requirement_id;
const PATH = `/tft/projects/${PROJECT}/requirements/${ID}`;
const ok = (data: unknown, status = 200) => jsonResponse(status, { data, meta: { request_id: 'x' } });
const row = (requirement_id: string, code: string) => ({ requirement_id, code });
const clause = (clause_no: string, text: string, extra = {}) => ({ clause_id: `c-${clause_no}`, clause_no, title: null, depth: 1, page: 3, text, deleted_values: [], ...extra });
const question = (id: string, extra = {}) => ({
  question_id: id, version: 2, requirement_id: ID, issue_type: 'CONDITION_MISSING', question_text: `질문 ${id}`, status: 'OPEN', target: 'CUSTOMER', target_dept: null,
  internal_assumption: null, reply_received_at: null, ...extra,
});
type Call = { url: string; method: string; body: Record<string, unknown>; key: string | null };
let calls: Call[];
let detail: unknown;
let questions: unknown[];
let write: (call: Call) => Response;
let pages: Record<string, { items: unknown[]; next_cursor: string | null }>;
let clausePages: Record<string, { items: unknown[]; next_cursor: string | null }>;

beforeEach(() => {
  calls = []; detail = MOCK_DETAIL; questions = [question('q1')];
  write = () => ok({ ...question('q1'), created_proposal: null });
  pages = { '': { items: [row('a', 'R-1'), row(ID, 'R-2')], next_cursor: 'p2' }, p2: { items: [row('b', 'R-3')], next_cursor: null } };
  clausePages = { '': { items: [clause('1', '첫 조항'), clause('2.1.A', '현행 문구', { deleted_values: [{ text: '25 kA', position: 1 }] })], next_cursor: 'c2' }, c2: { items: [clause('9', '끝 조항')], next_cursor: null } };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET';
    const call = { url, method, body: JSON.parse((init?.body as string) || '{}'), key: new Headers(init?.headers).get('Idempotency-Key') };
    calls.push(call);
    if (method === 'POST') return write(call);
    const cursor = new URL(url, 'http://x').searchParams.get('cursor') ?? '';
    if (url.includes('/platform/me')) return new Response(JSON.stringify({ data: { display_name: '합성', dept: null, roles: ['sales'] }, meta: { request_id: 'x' } }), { status: 200, headers: { 'X-CSRF-Token': 'c' } });
    if (url.includes('/clauses')) return ok(clausePages[cursor]);
    if (url.endsWith('/documents')) return ok({ items: [{ document_id: 'd1', document_version_id: MOCK_DETAIL.origin.document_version_id, title: '사양서', version: 2 }] });
    if (url.includes('/questions')) return ok({ items: questions });
    if (url.includes('/requirements?')) return ok(url.includes('needs_rereview') ? { items: [row('z', 'R-9')], next_cursor: null } : pages[cursor]);
    if (url.includes('/requirements/')) return ok(detail);
    return ok({ name: '합성', review_departments: ['RND_FW', 'SCM'], requirements_count: 1, unresolved_mandatory_count: 0, requirement_counts: { needs_rereview: 0 } });
  }));
  setApiMode('live');
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); setApiMode('mock'); });
const open = async () => { renderApp(PATH); await screen.findByRole('table', { name: '이 프로젝트 질문' }); await screen.findByText('질문 q1'); };
const posts = (part: string) => calls.filter(c => c.method === 'POST' && c.url.includes(part));
const withOrigin = (origin: object) => ({ ...MOCK_DETAIL, origin: { ...MOCK_DETAIL.origin, ...origin } });

describe('요구 상세', () => {
  it('요구·판단·연결을 보여 주고 옛 값을 지어내지 않는다', async () => {
    await open();
    expect(screen.getByText('이상 1500 kVA')).toBeTruthy();
    expect(screen.getByText('원문 2.1.A 정격 용량 요구 1500 kVA 이상, 자사 1000 kVA: 미달.')).toBeTruthy();
    expect(document.querySelector('.kv-next')?.textContent).toContain('변경 제안 또는 질문 작성');
    expect(screen.getByText('이 프로젝트 질문 1건, 회신 대기 1')).toBeTruthy();
  });

  it('속성은 어휘면 한글이고 원문 속성을 함께 보인다', async () => {
    detail = { ...MOCK_DETAIL, spec: { ...MOCK_DETAIL.spec, attribute: 'rating', attribute_key: 'rated_current' } };
    await open();
    expect(screen.getByText('정격 전류')).toBeTruthy();
    expect(screen.getByText('rating')).toBeTruthy();
  });

  it('요구를 못 읽으면 오류를 보인다', async () => {
    detail = undefined;
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, errorBody('X', '실패'))));
    renderApp(PATH);
    expect((await screen.findAllByRole('alert'))[0].textContent).toContain('실패');
  });

  it('업무 UI가 꺼졌으면 요구를 부르지 않는다', async () => {
    vi.stubEnv('DEV', false);
    renderApp(PATH);
    await waitFor(() => expect(document.querySelector('.ls-root')).not.toBeNull());
    expect(calls.some(c => c.url.includes('/requirements/'))).toBe(false);
  });
});

describe('제목 줄', () => {
  it('F6은 변경 제안을 열고 Esc는 폼이 없을 때만 대응표로 돌아간다', async () => {
    await open();
    fireEvent.keyDown(window, { key: 'F6' });
    await screen.findByRole('button', { name: '제안 저장' });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByRole('button', { name: '제안 저장' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '취소' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('group', { name: '순회' })).toBeNull());
  });

  it('Esc는 입력 요소와 열린 메뉴 안에서는 화면을 떠나지 않는다', async () => {
    await open();
    fireEvent.keyDown(screen.getByLabelText('순회 조건'), { key: 'Escape' });
    expect(screen.getByRole('group', { name: '순회' })).toBeTruthy();
    const more = document.querySelector('.kv-more') as HTMLDetailsElement;
    more.open = true;
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByRole('group', { name: '순회' })).toBeTruthy();
    more.open = false;
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('group', { name: '순회' })).toBeNull());
  });

  it('연결 표의 보완 경로는 코드가 아니라 라벨이다', async () => {
    detail = { ...MOCK_DETAIL, links: { ...MOCK_DETAIL.links, supplement_summary: 'EXTERNAL_PRODUCT' } };
    await open();
    const table = screen.getByRole('table', { name: '연결' });
    expect(table.textContent).toContain('외부 제품');
    expect(table.textContent).not.toContain('EXTERNAL_PRODUCT');
  });

  it('프로젝트·문서·조항 띠와 원문 내려받기를 문서 ID로 잇는다', async () => {
    await open();
    await screen.findByText('사양서 v2');
    expect(screen.getByRole('link', { name: '원문 내려받기' }).getAttribute('href')).toBe('/api/v1/tft/documents/d1/file');
  });
});

describe('원문', () => {
  it('인용과 삭제 값을 나눠 보여 준다', async () => {
    detail = withOrigin({ quote: '인용 문구', deleted_values: [{ text: '25 kA', position: 1 }] });
    await open();
    expect(screen.getByText('인용 문구').textContent).not.toContain('25 kA');
    expect(screen.getByText('25 kA').tagName).toBe('DEL');
    expect(screen.getByText(/이전 판본 값/)).toBeTruthy();
  });

  it('조항을 고르면 현행 문구를 보이고 요구 원문으로 돌아온다', async () => {
    detail = withOrigin({ quote: '인용 문구', clause_id: 'c-2.1.A' });
    await open();
    fireEvent.click(await screen.findByRole('button', { name: /^1p/ }));
    expect(screen.getByText('첫 조항')).toBeTruthy();
    expect(screen.queryByText('인용 문구')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '요구 원문으로' }));
    expect(screen.getByText('인용 문구')).toBeTruthy();
  });

  it('조항은 쪽 단위로 더 읽고 판본 ID로 부른다', async () => {
    await open();
    fireEvent.click(await screen.findByRole('button', { name: '조항 더 보기' }));
    await screen.findByRole('button', { name: /^9p/ });
    expect(calls.some(c => c.url.includes(`/documents/${MOCK_DETAIL.origin.document_version_id}/clauses?limit=100&cursor=c2`))).toBe(true);
    expect(screen.queryByRole('button', { name: '조항 더 보기' })).toBeNull();
  });

  it('요구의 조항이 뒤쪽 쪽에 있으면 찾을 때까지 읽어 표시한다', async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    detail = withOrigin({ clause_id: 'c-9' });
    await open();
    const row = await screen.findByRole('button', { name: /^9p/ });
    expect(row.getAttribute('aria-current')).toBe('true');
    await waitFor(() => expect(scroll).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: '조항 더 보기' })).toBeNull();
  });

  it('조항을 못 읽어도 요구는 보인다', async () => {
    clausePages[''] = undefined as never;
    await open();
    expect(screen.getByText('이상 1500 kVA')).toBeTruthy();
  });
});

describe('순회', () => {
  const walk = () => within(screen.getByRole('group', { name: '순회' }));
  it('쪽을 넘겨 끝까지 읽고 앞뒤 요구로 간다', async () => {
    await open();
    await walk().findByText(/전체 3건 중 2번째/);
    expect(walk().getByRole('link', { name: '이전 요구 R-1' }).getAttribute('href')).toBe(`/tft/projects/${PROJECT}/requirements/a`);
    expect(walk().getByRole('link', { name: '다음 요구 R-3' }).getAttribute('href')).toBe(`/tft/projects/${PROJECT}/requirements/b`);
  });

  it('처음과 끝에서는 막힌다', async () => {
    pages = { '': { items: [row(ID, 'R-2')], next_cursor: null } };
    await open();
    await walk().findByText(/전체 1건 중 1번째/);
    expect((walk().getByRole('button', { name: '이전 요구' }) as HTMLButtonElement).disabled).toBe(true);
    expect((walk().getByRole('button', { name: '다음 요구' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('조건을 바꾸면 서버 필터로 읽고 이 요구가 없으면 첫 요구로 간다', async () => {
    await open();
    fireEvent.change(walk().getByLabelText('순회 조건'), { target: { value: 'rereview' } });
    await walk().findByText(/조건에 맞는 요구 1건, 이 요구는 없음/);
    expect(calls.some(c => c.url.includes('needs_rereview=true'))).toBe(true);
    expect(walk().getByRole('link', { name: '다음 요구 R-9' })).toBeTruthy();
  });

  it('미해결 필수만은 서버 필터로 읽는다', async () => {
    await open();
    fireEvent.change(walk().getByLabelText('순회 조건'), { target: { value: 'unresolved' } });
    await waitFor(() => expect(calls.some(c => c.url.includes('unresolved_mandatory=true'))).toBe(true));
  });

  it('제목 줄 다음 요구가 순회와 같은 요구로 간다', async () => {
    await open();
    await walk().findByText(/전체 3건 중 2번째/);
    expect(within(document.querySelector('.kv-btns') as HTMLElement).getByRole('link', { name: '다음 요구' }).getAttribute('href')).toBe(`/tft/projects/${PROJECT}/requirements/b`);
  });

  it('요구를 옮기면 열린 폼을 닫는다', async () => {
    await open();
    await walk().findByText(/전체 3건 중 2번째/);
    fireEvent.click(screen.getAllByRole('button', { name: '질문 작성' })[0]);
    fireEvent.click(walk().getByRole('link', { name: '이전 요구 R-1' }));
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/requirements/a'))).toBe(true));
    await screen.findByText('이상 1500 kVA');
    expect(screen.queryByRole('button', { name: '질문 저장' })).toBeNull();
  });
});

describe('질문', () => {
  const form = async () => { await open(); fireEvent.click(screen.getAllByRole('button', { name: '질문 작성' })[0]); };

  it('질문을 저장하면 가정값 없이 고객 질문으로 보낸다', async () => {
    await form();
    const submit = screen.getByRole('button', { name: '질문 저장' }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('유형'), { target: { value: 'CONFLICT' } });
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: ' 확인 부탁드립니다 ' } });
    fireEvent.click(submit);
    await waitFor(() => expect(posts('/questions')).toHaveLength(1));
    expect(posts('/questions')[0].body).toEqual({
      requirement_id: ID, origin: { clause_id: null, page: 3 }, issue_type: 'CONFLICT', question_text: '확인 부탁드립니다', target: 'CUSTOMER', target_dept: null, internal_assumption: null,
    });
    expect(posts('/questions')[0].key).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole('button', { name: '질문 저장' })).toBeNull());
  });

  it('가정값은 근거와 함께 보내되 승인자 없이는 초안에 표시하지 않는다', async () => {
    await form();
    expect(screen.queryByLabelText('근거 규격')).toBeNull();
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '질문' } });
    fireEvent.change(screen.getByLabelText('가정값'), { target: { value: '50 kA' } });
    fireEvent.change(screen.getByLabelText('근거 규격'), { target: { value: 'IEC' } });
    fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
    await waitFor(() => expect(posts('/questions')).toHaveLength(1));
    expect(posts('/questions')[0].body.internal_assumption).toEqual({ value: '50 kA', basis_standard: 'IEC', basis_clause: null, approver_user_id: null, shown_in_draft: false });
  });

  it('고객 질문 유형만 고른다', async () => {
    await form();
    expect(within(screen.getByLabelText('유형')).getAllByRole('option').map(o => o.textContent)).toEqual(['값 없음', '상충', '범위 불명확', '조건 없음']);
  });

  it('저장 실패는 입력을 지킨다', async () => {
    write = () => jsonResponse(403, errorBody('X', '권한 없음'));
    await form();
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '남는 질문' } });
    fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
    await screen.findByRole('alert');
    expect((screen.getByLabelText('질문') as HTMLTextAreaElement).value).toBe('남는 질문');
  });

  it('초안 질문은 확인해야 회신을 받는다', async () => {
    questions = [question('q1', { status: 'DRAFT' })];
    await open();
    expect(screen.queryByRole('button', { name: '회신 기록' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '질문 확인' }));
    await waitFor(() => expect(posts('/confirm')).toHaveLength(1));
    expect(posts('/confirm')[0].url).toContain(`/projects/${PROJECT}/questions/q1/confirm`);
    expect(posts('/confirm')[0].key).toBeTruthy();
  });

  it('이미 확인된 질문은 알리고 다시 읽는다', async () => {
    questions = [question('q1', { status: 'DRAFT' })];
    write = () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'x'));
    await open();
    fireEvent.click(screen.getByRole('button', { name: '질문 확인' }));
    expect((await screen.findByRole('alert')).textContent).toContain('이미 확인됐습니다');
    questions = [question('q1')];
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    await screen.findByRole('button', { name: '회신 기록' });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('이 요구의 질문을 앞에 두고 다른 요구는 잇는다', async () => {
    questions = [question('q0', { requirement_id: 'other' }), question('q1')];
    await open();
    const body = screen.getByRole('table', { name: '이 프로젝트 질문' }).querySelector('tbody') as HTMLElement;
    expect(within(body).getAllByRole('row')[0].textContent).toContain('질문 q1');
    expect(within(body).getByRole('link', { name: '보기' }).getAttribute('href')).toBe(`/tft/projects/${PROJECT}/requirements/other`);
  });

  it('회신된 질문은 다시 기록하지 않고 받은 일시를 보인다', async () => {
    questions = [question('q1', { status: 'REPLIED', reply_received_at: '2026-10-27T00:30:00Z' })];
    await open();
    expect(screen.getByText('회신 기록됨')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '회신 기록' })).toBeNull();
  });
});

describe('회신', () => {
  const form = async () => {
    await open();
    fireEvent.click(screen.getByRole('button', { name: '회신 기록' }));
    fireEvent.change(screen.getByLabelText('회신 내용'), { target: { value: ' 가능합니다 ' } });
  };
  const send = () => fireEvent.click(screen.getByLabelText('회신 내용').closest('form')!.querySelector('button[type=submit]') as HTMLElement);

  it('받은 일시가 있어야 기록되고 읽은 판본과 UTC 시각을 보낸다', async () => {
    await form();
    expect((screen.getByLabelText('회신 내용').closest('form')!.querySelector('button[type=submit]') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('받은 일시'), { target: { value: '2026-10-27T09:30' } });
    send();
    await waitFor(() => expect(posts('/reply')).toHaveLength(1));
    expect(posts('/reply')[0].url).toContain('/tft/questions/q1/reply');
    expect(posts('/reply')[0].body).toEqual({ version: 2, reply_text: '가능합니다', received_at: new Date('2026-10-27T09:30').toISOString(), source_ref: null });
  });

  it.each([
    [{ proposal_id: 'p' }, '변경 제안이 생겼고 승인 전에는 요구가 바뀌지 않습니다'],
    [null, '변경 제안은 생기지 않았습니다'],
  ])('제안 생성 %j 를 사실대로 알린다', async (created_proposal, text) => {
    write = () => ok({ ...question('q1'), created_proposal });
    await form();
    fireEvent.change(screen.getByLabelText('받은 일시'), { target: { value: '2026-10-27T09:30' } });
    send();
    expect((await screen.findByRole('status')).textContent).toContain(text);
  });

  it('다시 읽어 이미 회신된 질문이면 폼을 닫는다', async () => {
    write = () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'x'));
    await form();
    fireEvent.change(screen.getByLabelText('받은 일시'), { target: { value: '2026-10-27T09:30' } });
    fireEvent.click(screen.getByLabelText('회신 내용').closest('form')!.querySelector('button[type=submit]') as HTMLElement);
    await screen.findByRole('alert');
    questions = [question('q1', { status: 'REPLIED' })];
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    await waitFor(() => expect(screen.queryByLabelText('회신 내용')).toBeNull());
    expect(screen.getByText('회신 기록됨')).toBeTruthy();
  });

  it('충돌은 입력을 지키고 다시 읽는다', async () => {
    write = () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'x'));
    await form();
    fireEvent.change(screen.getByLabelText('받은 일시'), { target: { value: '2026-10-27T09:30' } });
    send();
    expect((await screen.findByRole('alert')).textContent).toContain('입력한 내용은 남습니다');
    questions = [question('q1', { version: 3 })];
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    await waitFor(() => expect(calls.filter(c => c.url.includes('/questions')).length).toBeGreaterThan(1));
    expect((screen.getByLabelText('회신 내용') as HTMLTextAreaElement).value).toBe(' 가능합니다 ');
  });
});

describe('내부 질문', () => {
  const form = async () => { await open(); fireEvent.click(screen.getAllByRole('button', { name: '질문 작성' })[0]); await within(screen.getByLabelText('대상')).findByRole('option', { name: 'R&D' }); };
  const options = () => within(screen.getByLabelText('유형')).getAllByRole('option').map(o => o.textContent);

  it('검토 부서만 대상으로 고른다', async () => {
    await form();
    expect(within(screen.getByLabelText('대상')).getAllByRole('option').map(o => o.textContent)).toEqual(['고객', 'R&D', '구매팀']);
  });

  it('R&D 확인은 R&D 부서에만 낸다', async () => {
    await form();
    fireEvent.change(screen.getByLabelText('대상'), { target: { value: 'RND_FW' } });
    expect(options()).toEqual(['R&D 개발 가능성', '기술 자문', '구매 조건']);
    fireEvent.change(screen.getByLabelText('대상'), { target: { value: 'SCM' } });
    expect(options()).toEqual(['기술 자문', '구매 조건']);
    fireEvent.change(screen.getByLabelText('대상'), { target: { value: 'CUSTOMER' } });
    expect(options()).toEqual(['값 없음', '상충', '범위 불명확', '조건 없음']);
  });

  it('내부 질문은 부서와 함께 보내고 가정값을 받지 않는다', async () => {
    await form();
    fireEvent.change(screen.getByLabelText('가정값'), { target: { value: '50 kA' } });
    fireEvent.change(screen.getByLabelText('대상'), { target: { value: 'RND_FW' } });
    expect(screen.queryByLabelText('가정값')).toBeNull();
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '자사 개발 가능합니까' } });
    fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
    await waitFor(() => expect(posts('/questions')).toHaveLength(1));
    expect(posts('/questions')[0].body).toMatchObject({ target: 'INTERNAL', target_dept: 'RND_FW', issue_type: 'RND_FEASIBILITY', internal_assumption: null });
  });
});

describe('R&D 회신', () => {
  const RND = { target: 'INTERNAL', target_dept: 'RND_FW', issue_type: 'RND_FEASIBILITY' };
  const submit = () => screen.getByLabelText('회신 내용').closest('form')!.querySelector('button[type=submit]') as HTMLButtonElement;
  const form = async () => {
    questions = [question('q1', RND)];
    await open();
    fireEvent.click(screen.getByRole('button', { name: '회신 기록' }));
    fireEvent.change(screen.getByLabelText('회신 내용'), { target: { value: '가능' } });
    fireEvent.change(screen.getByLabelText('받은 일시'), { target: { value: '2026-10-27T09:30' } });
  };

  it('개발 가능성이 있어야 기록된다', async () => {
    await form();
    expect(submit().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('개발 가능성'), { target: { value: 'FEASIBLE' } });
    expect(submit().disabled).toBe(false);
    fireEvent.click(submit());
    await waitFor(() => expect(posts('/reply')).toHaveLength(1));
    expect(posts('/reply')[0].body).toMatchObject({ rnd_feasibility: 'FEASIBLE', earliest_available_on: null, schedule_fits: null });
    expect((await screen.findByRole('status')).textContent).toBe('회신을 기록했습니다.');
  });

  it('일정 조건부는 납기 부합까지 있어야 기록되고 시점을 함께 보낸다', async () => {
    await form();
    fireEvent.change(screen.getByLabelText('개발 가능성'), { target: { value: 'FEASIBLE_WITH_SCHEDULE' } });
    expect(submit().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('가능 시점'), { target: { value: '2027-03-01' } });
    fireEvent.change(screen.getByLabelText('납기 부합'), { target: { value: 'no' } });
    fireEvent.click(submit());
    await waitFor(() => expect(posts('/reply')).toHaveLength(1));
    expect(posts('/reply')[0].body).toMatchObject({ rnd_feasibility: 'FEASIBLE_WITH_SCHEDULE', earliest_available_on: '2027-03-01', schedule_fits: false });
  });

  it('조건부를 풀면 일정 값을 보내지 않는다', async () => {
    await form();
    fireEvent.change(screen.getByLabelText('개발 가능성'), { target: { value: 'FEASIBLE_WITH_SCHEDULE' } });
    fireEvent.change(screen.getByLabelText('납기 부합'), { target: { value: 'yes' } });
    fireEvent.change(screen.getByLabelText('개발 가능성'), { target: { value: 'NOT_FEASIBLE' } });
    fireEvent.click(submit());
    await waitFor(() => expect(posts('/reply')).toHaveLength(1));
    expect(posts('/reply')[0].body).toMatchObject({ rnd_feasibility: 'NOT_FEASIBLE', earliest_available_on: null, schedule_fits: null });
  });

  it('판단 불가는 대기로 남는다고 알린다', async () => {
    await form();
    fireEvent.change(screen.getByLabelText('개발 가능성'), { target: { value: 'UNDETERMINED' } });
    expect(screen.getByText(/대기로 남습니다/)).toBeTruthy();
  });

  it('다른 내부 질문과 고객 질문에는 R&D 칸을 보내지 않는다', async () => {
    questions = [question('q1', { target: 'INTERNAL', target_dept: 'SCM', issue_type: 'PURCHASE_TERMS' })];
    await open();
    expect(screen.getByText('구매팀')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '회신 기록' }));
    expect(screen.queryByLabelText('개발 가능성')).toBeNull();
    fireEvent.change(screen.getByLabelText('회신 내용'), { target: { value: '답변' } });
    fireEvent.change(screen.getByLabelText('받은 일시'), { target: { value: '2026-10-27T09:30' } });
    fireEvent.click(submit());
    await waitFor(() => expect(posts('/reply')).toHaveLength(1));
    expect(Object.keys(posts('/reply')[0].body).sort()).toEqual(['received_at', 'reply_text', 'source_ref', 'version']);
  });

  it('R&D 확인 상태를 판단에 보이고 확인이 필요 없으면 감춘다', async () => {
    detail = { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'PENDING' } };
    await open();
    expect(screen.getByText('R&D 확인 대기')).toBeTruthy();
    cleanup();
    detail = { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED' } };
    await open();
    expect(screen.queryByText('확인 불필요')).toBeNull();
  });
});

describe('변경 제안', () => {
  it('이 화면에서 제안을 저장한다', async () => {
    write = () => ok(MOCK_DETAIL);
    await open();
    fireEvent.click(screen.getAllByRole('button', { name: '변경 제안' })[0]);
    fireEvent.change(screen.getByLabelText('상태', { selector: '#proposal-status' }), { target: { value: 'CONDITIONAL' } });
    fireEvent.change(screen.getByLabelText('제안 사유'), { target: { value: '사유' } });
    fireEvent.click(screen.getByRole('button', { name: '제안 저장' }));
    await waitFor(() => expect(posts('/proposals')).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole('button', { name: '제안 저장' })).toBeNull());
  });
});
