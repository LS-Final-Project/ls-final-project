// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import type { ProjectDetail } from '../api/tft';
import { MOCK_WORKSPACE_REQUIREMENTS } from '../mocks/rfp-workspace';
import { jsonResponse, renderAt } from '../test/render';
import { clauseOf, Decide, historyText, pendingReviews, queryState, tqNumbers, viewerRole, workspacePhase } from './RfpWorkspace';

vi.mock('./OriginalDocument', () => ({ OriginalDocument: ({ onSelectText }: { onSelectText?: (text: string) => void }) => <><button onClick={() => onSelectText?.('정격전압은  12 kV 이상')}>원문에서 고르기</button><button onClick={() => onSelectText?.('표 안의 글')}>표에서 고르기</button></> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

const project = { id: 'p1', name: 'RFP-91', sales_user_id: 'sales', responsible_user_id: 'boss', decision_gate: { undecided: 17, deviation_missing: 0, open_queries: 0, review_pending: 0 } } as unknown as ProjectDetail;
const show = (role: string) => renderAt('/tft/projects/p1', 'tft/projects/:projectId', <Decide project={project} documents={[]} rows={MOCK_WORKSPACE_REQUIREMENTS} role={role} actorId={role === 'responsible' ? 'boss' : 'sales'} />);
let addReply = (_body: { allow_duplicate?: boolean }) => jsonResponse(200, { data: { action: 'add_requirement', decision_id: 'd9', requirement_id: 'new-r', requirement_version: 1, summary: '직접 추가' } });
const serve = (questions: unknown[] = []) => {
  const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/questions') && !url.includes('/questions/')) return jsonResponse(200, { data: { items: questions, next_cursor: null } });
    if (url.includes('/history')) return jsonResponse(200, { data: { items: [{ id: 'h1', at: '2026-11-04T09:00:00Z', kind: 'decision', actor_user_id: null, requirement_id: MOCK_WORKSPACE_REQUIREMENTS[15].requirement_id, requirement_version: 2, summary: '편차 기재', before: { status: 'SPEC_GAP' }, after: { status: 'CONDITIONAL' } }], next_cursor: url.includes('cursor=') ? null : 'c2' } });
    if (url.includes('/clauses')) return jsonResponse(200, { data: { items: [{ clause_id: 'c1', clause_no: '제6조.2', text: '정격전압은 12 kV 이상으로 한다.' }], next_cursor: null } });
    if (url.endsWith('/projects/p1/requirements') && init?.method === 'POST') return addReply(JSON.parse(String(init.body)));
    if (url.endsWith('/decision')) return jsonResponse(200, { data: { decision_id: 'd1', action: 'confirm_met', summary: '충족 확정', requirement_id: 'r', requirement_version: 2 } });
    if (url.includes('/comparison')) return jsonResponse(200, { data: { candidates: [] } });
    if (url.includes('/requirements/')) return jsonResponse(200, { data: { version: 1, judgement: { reason: '' }, origin: {} } });
    return jsonResponse(200, { data: { items: [] } });
  });
  vi.stubGlobal('fetch', f);
  setApiMode('live');
  return f;
};
const decisions = (f: ReturnType<typeof serve>) => (f.mock.calls as unknown as [string, RequestInit][]).filter(([u]) => String(u).endsWith("/decision")).map(([, init]) => JSON.parse(String(init.body)));

describe('화면 단계와 보는 사람', () => {
  it('작업이 돌면 분석 중, 요구가 있으면 결정, 실패와 0건을 구분한다', () => {
    expect(workspacePhase(0, 0)).toBe('empty');
    expect(workspacePhase(1, 0)).toBe('ready');
    expect(workspacePhase(1, 5, 'RUNNING')).toBe('running');
    expect(workspacePhase(1, 5, 'SUCCEEDED')).toBe('done');
    expect(workspacePhase(1, 0, 'FAILED')).toBe('failed');
    expect(workspacePhase(1, 0, 'SUCCEEDED')).toBe('none');
  });
  it('담당 sales만 결정하고 책임자는 확인만 한다', () => {
    expect(viewerRole('sales', project)).toBe('sales');
    expect(viewerRole('boss', project)).toBe('responsible');
    expect(viewerRole('other', project)).toBe('viewer');
  });
});

describe('결정 (담당)', () => {
  it('묶음은 질의, 편차, 보완, 배정, 충족 확인 순서이고 충족은 접힌다', () => {
    show('sales');
    const groups = [...document.querySelectorAll('tr.g')].map(row => row.textContent?.split(' ')[0]);
    expect(groups.slice(0, 2)).toEqual(['질의', '편차']);
    expect(screen.getByRole('button', { name: '충족 14건 펼치기' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: '편차 기재' }).length).toBe(2);
  });

  it('설비 선택과 검색어가 실제로 거른다', () => {
    show('sales');
    fireEvent.change(screen.getByLabelText('설비'), { target: { value: 'TR-01' } });
    expect(screen.queryByRole('button', { name: '질의서에 넣기' })).toBeNull();
    fireEvent.change(screen.getByLabelText('요구 검색'), { target: { value: '임피던스' } });
    expect(screen.getAllByRole('button', { name: '임피던스' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '형식' })).toBeNull();
  });

  it('충족 확인 묶음을 한 번 확인으로 건별 결정한다', async () => {
    const f = serve();
    show('sales');
    fireEvent.click(screen.getByRole('button', { name: '모두 충족 확정' }));
    fireEvent.click(screen.getByRole('button', { name: '확인' }));
    await waitFor(() => expect(decisions(f).filter(b => b.action === 'confirm_met').length).toBe(4));
    expect(await screen.findByRole('button', { name: '한 번에 되돌리기' })).toBeTruthy();
  });

  it('배정 묶음은 체크한 행만 같은 부서로 일괄 배정한다', async () => {
    const f = serve();
    show('sales');
    fireEvent.click(screen.getByLabelText('공장시험 선택'));
    fireEvent.click(screen.getByLabelText('현장시험 선택'));
    fireEvent.click(screen.getByRole('button', { name: '자사 수행으로 일괄 배정' }));
    await waitFor(() => expect(decisions(f)).toEqual([
      { version: 1, action: 'assign', holder: 'OWN', owner: 'TECH' }, { version: 1, action: 'assign', holder: 'OWN', owner: 'TECH' },
    ]));
  });

  it('결정 칸은 결정 경로를 부른다', async () => {
    const f = serve();
    show('sales');
    fireEvent.click(screen.getAllByRole('button', { name: '충족 확정' })[0]);
    const panel = screen.getByRole('region', { name: '선택한 요구와 원문' });
    fireEvent.click(within(panel).getByRole('button', { name: '충족 확정' }));
    await waitFor(() => expect(decisions(f)[0]).toEqual({ version: 1, action: 'confirm_met' }));
  });
});

describe('책임자', () => {
  it('출력 탭이 먼저 열리고 결정·일괄 버튼이 없다', () => {
    show('responsible');
    expect(screen.getAllByRole('tab', { selected: true })[0].textContent).toContain('출력');
    expect(screen.getByRole('region', { name: '책임자 확인' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '검토본 저장' })).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: '검토' }));
    expect(screen.queryByRole('button', { name: '편차 기재' })).toBeNull();
    expect(screen.queryByRole('button', { name: '모두 충족 확정' })).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.getByText('결정은 담당자가 합니다.')).toBeTruthy();
  });
});

it('질의 상태는 질의서에 넣음, 회신 기다림 n일째, 회신 받음', () => {
  const now = Date.parse('2026-11-04T09:00:00Z');
  expect(queryState({ status: 'DRAFT', created_at: '2026-11-01T09:00:00Z' }, now)).toBe('질의서에 넣음');
  expect(queryState({ status: 'OPEN', created_at: '2026-11-01T09:00:00Z' }, now)).toBe('회신 기다림 3일째');
  expect(queryState({ status: 'REPLIED', created_at: '2026-11-01T09:00:00Z' }, now)).toBe('회신 받음');
});

describe('변경 이력', () => {
  it('요구 이력은 이력 링크로 열고 더 보기로 다음 쪽을 부른다', async () => {
    const f = serve();
    show('sales');
    fireEvent.click(screen.getByRole('button', { name: '이력' }));
    expect((await screen.findAllByText(/판단 .+ → .+/)).length).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: '더 보기' }));
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u).includes('/requirements/') && String(u).includes('/history?') && String(u).includes('cursor=c2'))).toBe(true));
  });
  it('책임자도 출력 탭에서 프로젝트 이력을 보고 종류로 거른다', async () => {
    const f = serve();
    show('responsible');
    const table = await screen.findByRole('region', { name: '변경 이력' });
    expect(await within(table).findByText('제7조.8')).toBeTruthy();
    fireEvent.click(within(table).getByRole('button', { name: '회신' }));
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u).includes('/projects/p1/history?') && decodeURIComponent(String(u)).includes('kind=reply_recorded,reply_applied'))).toBe(true));
  });
});

const QID = MOCK_WORKSPACE_REQUIREMENTS[5].requirement_id;
const replied = (extra: Record<string, unknown>) => ({ question_id: 'q1', requirement_id: QID, target: 'CUSTOMER', status: 'REPLIED', question_text: '상용주파 내전압 값을 알려 주십시오.', created_at: '2026-11-01T00:00:00Z', origin: { clause_id: null, page: null }, issue_type: 'EMPTY_VALUE', reply: { reply_text: '28 kV, 1분 적용 바랍니다.' }, ...extra });

describe('고객 회신 반영', () => {
  it('규칙으로 읽은 값을 보여 주고 미리 채우되 자동 반영하지 않는다', async () => {
    const f = serve([replied({ value_candidate: { method: 'rule', value: '28', unit: 'kV', conditions: [{ name: 'duration', value: '1', unit: 'min' }], quote: '28 kV, 1분', reason: null } })]);
    show('sales');
    expect(await screen.findByText('회신 도착')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '질의서에 넣기' }));
    expect(await screen.findByText(/회신에서 읽은 값: 28 kV, 1 min/)).toBeTruthy();
    expect((screen.getByLabelText('새 요구값') as HTMLInputElement).value).toBe('28');
    expect(screen.queryByText('AI가 읽음')).toBeNull();
    expect(f.mock.calls.some(([u]) => String(u).includes('/proposals'))).toBe(false);
  });
  it('못 읽으면 사유를 사람 말로 쓰고 후속 질의에 원래 질의를 잇는다', async () => {
    const f = serve([replied({ value_candidate: { method: 'none', reason: 'CONDITIONAL' } })]);
    show('sales');
    fireEvent.click(await screen.findByRole('button', { name: '질의서에 넣기' }));
    expect(await screen.findByText(/조건에 따라 값이 다릅니다/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '후속 질의 보내기' }));
    await waitFor(() => expect((f.mock.calls as unknown as [string, RequestInit][]).some(([u, init]) => u.endsWith('/projects/p1/questions') && init?.method === 'POST' && JSON.parse(String(init.body)).follows_question_id === 'q1')).toBe(true));
  });
  it('후속 질의는 원래 질의 아래 TQ-n-2로 묶는다', () => {
    const q = (id: string, follows?: string) => ({ question_id: id, follows_question_id: follows ?? null }) as never;
    expect(tqNumbers([q('a'), q('b'), q('c', 'a')]).map(x => x.no)).toEqual(['TQ-1', 'TQ-1-2', 'TQ-2']);
  });
});

const DOCS = [{ document_version_id: 'dv1', document_id: 'dv1', title: 'RFP', version: 1, mime: 'application/pdf' }] as never;
const showDoc = (role: string) => renderAt('/tft/projects/p1', 'tft/projects/:projectId', <Decide project={project} documents={DOCS} rows={MOCK_WORKSPACE_REQUIREMENTS} role={role} actorId={role === 'responsible' ? 'boss' : 'sales'} />);
const bodies = (f: ReturnType<typeof serve>, end: string) => (f.mock.calls as unknown as [string, RequestInit][]).filter(([u, i]) => u.endsWith(end) && i?.method === 'POST').map(([, i]) => JSON.parse(String(i.body)));

describe('원문에서 요구 추가와 빼기 (KAN-225)', () => {
  it('고른 글이 든 조항을 찾고, 표 글이나 없는 글은 찾지 않는다', () => {
    const clauses = [{ clause_id: 'c1', clause_no: '6.2', text: '정격전압은 12 kV 이상으로 한다.' }];
    expect(clauseOf(clauses, '정격전압은   12 KV 이상')?.clause_id).toBe('c1');
    expect(clauseOf(clauses, '표 안의 글')).toBeUndefined();
    expect(clauseOf(clauses, '')).toBeUndefined();
  });

  it('고른 글로 요구를 추가하고, 값이 인용에 없으면 막고, 중복이면 다시 눌러 추가한다', async () => {
    const f = serve();
    addReply = body => body.allow_duplicate ? jsonResponse(200, { data: { action: 'add_requirement', decision_id: 'd9', requirement_id: 'new-r', requirement_version: 1, summary: '직접 추가' } }) : jsonResponse(409, { error: { code: 'TFT_REQUIREMENT_DUPLICATE', message: '같은 요구가 있습니다', details: [] } });
    showDoc('sales');
    fireEvent.click(await screen.findByRole('button', { name: '표에서 고르기' }));
    expect(screen.queryByRole('button', { name: '요구로 추가' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '원문에서 고르기' }));
    fireEvent.click(await screen.findByRole('button', { name: '요구로 추가' }));
    const form = screen.getByLabelText('새 요구(직접 추가)');
    expect(within(form).getByText('제6조.2')).toBeTruthy();
    fireEvent.change(within(form).getByLabelText('속성'), { target: { value: '정격전압' } });
    fireEvent.change(within(form).getByLabelText('값'), { target: { value: '13' } });
    expect(within(form).getByText(/값이 고른 글에 없습니다/)).toBeTruthy();
    expect((within(form).getByRole('button', { name: '요구로 추가' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(form).getByLabelText('값'), { target: { value: '12' } });
    fireEvent.change(within(form).getByLabelText('단위'), { target: { value: 'kV' } });
    fireEvent.click(within(form).getByRole('button', { name: '요구로 추가' }));
    fireEvent.click(await within(form).findByRole('button', { name: '그래도 추가' }));
    await waitFor(() => expect(bodies(f, '/projects/p1/requirements')).toHaveLength(2));
    expect(bodies(f, '/projects/p1/requirements')[1]).toMatchObject({ clause_id: 'c1', quote: '정격전압은  12 kV 이상', kind: 'SPEC', attribute: '정격전압', value: '12', unit: 'kV', allow_duplicate: true });
  });

  it('요구에서 빼기는 사유가 있어야 보내고, 책임자에게는 추가·빼기가 없다', async () => {
    const f = serve();
    showDoc('sales');
    fireEvent.click(await screen.findByRole('button', { name: '요구에서 빼기' }));
    const send = screen.getAllByRole('button', { name: '요구에서 빼기' }).at(-1) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('뺀 사유'), { target: { value: '공급 범위 밖' } });
    fireEvent.click(send);
    await waitFor(() => expect(decisions(f)).toContainEqual({ version: 1, action: 'exclude_requirement', text: '공급 범위 밖' }));
    cleanup();
    serve();
    showDoc('responsible');
    fireEvent.click(screen.getByRole('tab', { name: '검토' }));
    fireEvent.click(await screen.findByRole('button', { name: '원문에서 고르기' }));
    expect(screen.queryByRole('button', { name: '요구로 추가' })).toBeNull();
    expect(screen.queryByRole('button', { name: '요구에서 빼기' })).toBeNull();
  });

  it('책임자 확인 목록에 직접 추가·빼기·되살리기를 같은 요구의 결정과 따로 넣는다', () => {
    const r = { ...MOCK_WORKSPACE_REQUIREMENTS[0], decision: { needed: true, decided: true, reviewed: false, decision_id: 'd1', summary: '충족 확정', change_id: 'c1', change_action: 'add_requirement', change_reviewed: false, excluded: false, manually_added: true } } as never;
    const gone = { ...MOCK_WORKSPACE_REQUIREMENTS[1], decision: { needed: false, decided: true, reviewed: false, decision_id: 'd2', change_id: 'c2', change_action: 'exclude_requirement', change_reviewed: false, excluded: true, manually_added: false } } as never;
    expect(pendingReviews([r, gone]).map(p => [p.decisionId, p.label])).toEqual([['d1', '충족 확정'], ['c1', '직접 추가'], ['c2', '뺌']]);
  });

  it('이력은 직접 추가·빼기·되살리기를 그 말로, 판본 변화는 한국어 칸 이름으로 보인다', () => {
    expect(historyText({ kind: 'decision', summary: '공급 범위 밖', after: { action: 'exclude_requirement' }, before: null } as never).head).toBe('뺌: 공급 범위 밖');
    expect(historyText({ kind: 'ai_verdict', summary: 'AI 판정', before: { value: '12' }, after: { value: '13', unit: 'kV' } } as never).change).toBe('값 12 → 13, 단위 없음 → kV');
  });
});
