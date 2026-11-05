// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import type { ProjectDetail } from '../api/tft';
import { MOCK_WORKSPACE_REQUIREMENTS } from '../mocks/rfp-workspace';
import { jsonResponse, renderAt } from '../test/render';
import { clauseOf, Decide, TakeResponsibility, historyText, pendingReviews, queryState, tqNumbers, viewerRole, workspacePhase } from './RfpWorkspace';

vi.mock('./OriginalDocument', () => ({ OriginalDocument: ({ onSelectText }: { onSelectText?: (text: string) => void }) => <><button onClick={() => onSelectText?.('정격전압은  12 kV 이상')}>원문에서 고르기</button><button onClick={() => onSelectText?.('표 안의 글')}>표에서 고르기</button></> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

const project = { id: 'p1', name: 'RFP-91', sales_user_id: 'sales', responsible_user_id: 'boss', decision_gate: { undecided: 17, deviation_missing: 0, open_queries: 0, review_pending: 0 } } as unknown as ProjectDetail;
const show = (role: string) => renderAt('/tft/projects/p1', 'tft/projects/:projectId', <Decide project={project} documents={[]} rows={MOCK_WORKSPACE_REQUIREMENTS} role={role} actorId={role === 'responsible' ? 'boss' : 'sales'} />);
let bulkReply = (items: { requirement_id: string; action: string }[]) => jsonResponse(200, { data: { batch_id: 'b1', items: items.map((x, i) => ({ decision_id: `d${i}`, action: x.action, requirement_id: x.requirement_id, requirement_version: 2, summary: '결정' })) } });
let addReply = (_body: { allow_duplicate?: boolean }) => jsonResponse(200, { data: { action: 'add_requirement', decision_id: 'd9', requirement_id: 'new-r', requirement_version: 1, summary: '직접 추가' } });
const serve = (questions: unknown[] = []) => {
  const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/questions') && !url.includes('/questions/')) return jsonResponse(200, { data: { items: questions, next_cursor: null } });
    if (url.includes('/history')) return jsonResponse(200, { data: { items: [{ id: 'h1', at: '2026-11-04T09:00:00Z', kind: 'decision', actor_user_id: null, requirement_id: MOCK_WORKSPACE_REQUIREMENTS[15].requirement_id, requirement_version: 2, summary: '편차 기재', before: { status: 'SPEC_GAP' }, after: { status: 'CONDITIONAL' } }], next_cursor: url.includes('cursor=') ? null : 'c2' } });
    if (url.includes('/clauses')) return jsonResponse(200, { data: { items: [{ clause_id: 'c1', clause_no: '제6조.2', text: '정격전압은 12 kV 이상으로 한다.' }], next_cursor: null } });
    if (url.endsWith('/projects/p1/requirements') && init?.method === 'POST') return addReply(JSON.parse(String(init.body)));
    if (url.endsWith('/decisions/bulk')) { const items = JSON.parse(String(init?.body)).items as { requirement_id: string; action: string }[]; return bulkReply(items); }
    if (url.includes('/decisions/bulk/') && url.endsWith('/undo')) return jsonResponse(200, { data: { batch_id: 'b1', items: [] } });
    if (url.endsWith('/questions/sent')) return jsonResponse(200, { data: { items: [] } });
    if (url.endsWith('/equipment')) return jsonResponse(200, { data: { source: 'context', items: [{ tag: 'MV-SWG-01', name: '중전압 개폐반' }, { tag: null, name: '버스덕트' }] } });
    if (url.endsWith('/decision')) return jsonResponse(200, { data: { decision_id: 'd1', action: 'confirm_met', summary: '충족 확정', requirement_id: 'r', requirement_version: 2 } });
    if (url.includes('/comparison')) return jsonResponse(200, { data: { candidates: [] } });
    if (url.includes('/requirements/')) return jsonResponse(200, { data: { version: 1, judgement: { reason: '' }, origin: {} } });
    return jsonResponse(200, { data: { items: [] } });
  });
  vi.stubGlobal('fetch', f);
  setApiMode('live');
  return f;
};
const bulks = (f: ReturnType<typeof serve>) => (f.mock.calls as unknown as [string, RequestInit][]).filter(([u]) => String(u).endsWith('/decisions/bulk')).map(([, init]) => JSON.parse(String(init.body)).items);
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
    fireEvent.change(screen.getByLabelText('설비'), { target: { value: 'TR' } });
    expect(screen.queryByRole('button', { name: '질의서에 넣기' })).toBeNull();
    fireEvent.change(screen.getByLabelText('요구 검색'), { target: { value: '임피던스' } });
    expect(screen.getAllByRole('button', { name: '임피던스' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '형식' })).toBeNull();
  });

  it('충족 확인 묶음을 한 번 확인으로 일괄 경로 한 번에 보내고 묶음으로 되돌린다', async () => {
    const f = serve();
    show('sales');
    fireEvent.click(screen.getByRole('button', { name: '모두 충족 확정' }));
    fireEvent.click(screen.getByRole('button', { name: '확인' }));
    await waitFor(() => expect(bulks(f)).toHaveLength(1));
    expect(bulks(f)[0].map((x: { action: string }) => x.action)).toEqual(['confirm_met', 'confirm_met', 'confirm_met', 'confirm_met']);
    expect(decisions(f)).toEqual([]);
    fireEvent.click(await screen.findByRole('button', { name: '한 번에 되돌리기' }));
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u).endsWith('/projects/p1/decisions/bulk/b1/undo'))).toBe(true));
  });

  it('일괄 결정이 막히면 첫 실패 건을 알리고 아무것도 저장하지 않았다고 말한다', async () => {
    serve();
    bulkReply = items => jsonResponse(409, { error: { code: 'TFT_VERSION_CONFLICT', message: '판본이 바뀌었습니다', details: [{ index: 1, requirement_id: items[1].requirement_id, reason: 'TFT_VERSION_CONFLICT' }] } });
    show('sales');
    fireEvent.click(screen.getByRole('button', { name: '모두 충족 확정' }));
    fireEvent.click(screen.getByRole('button', { name: '확인' }));
    expect((await screen.findByText(/2번째 요구\(TR-01 형식\)에서 막혔습니다/)).textContent).toContain('아무것도 저장하지 않았습니다');
    bulkReply = items => jsonResponse(200, { data: { batch_id: 'b1', items: items.map((x, i) => ({ decision_id: `d${i}`, action: x.action, requirement_id: x.requirement_id, requirement_version: 2, summary: '결정' })) } });
  });

  it('배정 묶음은 체크한 행만 같은 부서로 일괄 배정한다', async () => {
    const f = serve();
    show('sales');
    fireEvent.click(screen.getByLabelText('공장시험 선택'));
    fireEvent.click(screen.getByLabelText('현장시험 선택'));
    fireEvent.click(screen.getByRole('button', { name: '자사 수행으로 일괄 배정' }));
    await waitFor(() => expect(bulks(f)).toHaveLength(1));
    expect(bulks(f)[0]).toEqual([
      { requirement_id: MOCK_WORKSPACE_REQUIREMENTS[25].requirement_id, version: 1, action: 'assign', holder: 'OWN', owner: 'TECH' },
      { requirement_id: MOCK_WORKSPACE_REQUIREMENTS[26].requirement_id, version: 1, action: 'assign', holder: 'OWN', owner: 'TECH' },
    ]);
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

it('질의 상태는 질의서에 넣음 → 보냄 → 회신 기다림 n일째 → 회신 받음', () => {
  expect(queryState({ status: 'DRAFT', sent_at: null, waiting_days: null })).toBe('질의서에 넣음');
  expect(queryState({ status: 'OPEN', sent_at: null, waiting_days: null })).toBe('질의서에 넣음');
  expect(queryState({ status: 'OPEN', sent_at: '2026-11-04T09:00:00Z', waiting_days: 1 })).toBe('보냄, 회신 기다림 1일째');
  expect(queryState({ status: 'OPEN', sent_at: '2026-11-01T09:00:00Z', waiting_days: 4 })).toBe('회신 기다림 4일째');
  expect(queryState({ status: 'REPLIED', sent_at: '2026-11-01T09:00:00Z', waiting_days: null })).toBe('회신 받음');
});

describe('도면 확인·자사 보증값·설비 목록·발송 기록 (KAN-227·228)', () => {
  it('도면 OCR 요구를 도면 확인 묶음으로 모으고 맞음은 confirm_drawing, 다름은 질의서에 넣는다', async () => {
    const f = serve();
    show('sales');
    const groups = [...document.querySelectorAll('tr.g')].map(g => g.textContent?.split(' ').slice(0, 2).join(' '));
    expect(groups.at(-1)).toContain('도면 확인');
    expect(screen.getAllByRole('button', { name: '도면 대조' })).toHaveLength(4);
    const panel = screen.getByRole('region', { name: '선택한 요구와 원문' });
    fireEvent.click(screen.getAllByRole('button', { name: '도면 대조' })[0]);
    fireEvent.click(await within(panel).findByRole('button', { name: '다름, 질의서에 넣기' }));
    await waitFor(() => expect(decisions(f).some(b => b.action === 'add_query' && /TR-01 용량, 결선 값이 도면과 규격서에서 다릅니다/.test(b.text))).toBe(true));
    fireEvent.click(screen.getAllByRole('button', { name: '도면 대조' })[1]);
    fireEvent.click(await within(panel).findByRole('button', { name: '맞음' }));
    await waitFor(() => expect(decisions(f)).toContainEqual({ version: 1, action: 'confirm_drawing' }));
  });
  it('자사 보증값 열은 목록 응답의 own_value를 보인다', () => {
    show('sales');
    const row = screen.getByRole('button', { name: '임피던스' }).closest('tr')!;
    expect(within(row).getByText('6 % @120 °C')).toBeTruthy();
    expect(within(row).getByText('TRF-DRY-630')).toBeTruthy();
  });
  it('보내지 않은 고객 질의를 보낸 것으로 기록한다', async () => {
    const f = serve([{ question_id: 'q1', requirement_id: MOCK_WORKSPACE_REQUIREMENTS[5].requirement_id, target: 'CUSTOMER', status: 'OPEN', question_text: '값을 알려 주십시오', sent_at: null, waiting_days: null }]);
    show('sales');
    fireEvent.click(screen.getByRole('tab', { name: /출력/ }));
    fireEvent.click(screen.getByRole('tab', { name: /질의서/ }));
    fireEvent.click(await screen.findByRole('button', { name: '보낸 것으로 기록 1건' }));
    await waitFor(() => expect((f.mock.calls as unknown as [string, RequestInit][]).filter(([u]) => u.endsWith('/projects/p1/questions/sent')).map(([, i]) => JSON.parse(String(i.body)))).toEqual([{ question_ids: ['q1'], mail_ref: null }]));
  });
});


describe('변경 이력', () => {
  it('요구 이력은 이력 링크로 열고 더 보기로 다음 쪽을 부른다', async () => {
    const f = serve();
    show('sales');
    fireEvent.click(await screen.findByRole('button', { name: '이력 1+건' }));
    expect((await screen.findAllByText(/판단 .+ → .+/)).length).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: '더 보기' }));
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u).includes('/requirements/') && String(u).includes('/history?') && String(u).includes('cursor=c2'))).toBe(true));
  });
  it('책임자도 출력 탭에서 프로젝트 이력을 보고 종류로 거른다', async () => {
    const f = serve();
    show('responsible');
    fireEvent.click(screen.getByRole('tab', { name: /변경 이력/ }));
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
    // 근거 문장은 회신 안에서 강조한다.
    show('sales');
    expect(await screen.findByText('회신 도착')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '상용주파 내전압' }));
    expect(await screen.findByText(/회신에서 읽은 값: 28 kV, 1분/)).toBeTruthy();
    expect(document.querySelector('mark.ws-mark')?.textContent).toBe('28 kV, 1분');
    expect((screen.getByLabelText('새 요구값') as HTMLInputElement).value).toBe('28');
    expect(screen.queryByText('AI가 읽음')).toBeNull();
    expect(f.mock.calls.some(([u]) => String(u).includes('/proposals'))).toBe(false);
  });
  it('못 읽으면 사유를 사람 말로 쓰고 후속 질의에 원래 질의를 잇는다', async () => {
    const f = serve([replied({ value_candidate: { method: 'none', reason: 'CONDITIONAL' } })]);
    show('sales');
    await screen.findByText('회신 도착');
    fireEvent.click(screen.getByRole('button', { name: '상용주파 내전압' }));
    expect(await screen.findByText(/조건에 따라 값이 다릅니다/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '다시 질의' }));
    await waitFor(() => expect((f.mock.calls as unknown as [string, RequestInit][]).some(([u, init]) => u.endsWith('/projects/p1/questions') && init?.method === 'POST' && JSON.parse(String(init.body)).follows_question_id === 'q1')).toBe(true));
  });
  it('AI 읽기를 기다리는 동안 알리고, 접수하지 못했으면 AI로 다시 읽기를 부른다', async () => {
    serve([replied({ value_candidate: { method: 'none', reason: 'CORRECTION', ai: 'QUEUED' } })]);
    show('sales');
    await screen.findByText('회신 도착');
    fireEvent.click(screen.getByRole('button', { name: '상용주파 내전압' }));
    expect(await screen.findByText('AI가 회신을 읽는 중입니다.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'AI로 다시 읽기' })).toBeNull();
    cleanup();
    const f = serve([replied({ value_candidate: { method: 'none', reason: 'CORRECTION', ai: 'UNAVAILABLE' } })]);
    show('sales');
    await screen.findByText('회신 도착');
    fireEvent.click(screen.getByRole('button', { name: '상용주파 내전압' }));
    expect(await screen.findByText(/AI 읽기를 시작하지 못했습니다/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'AI로 다시 읽기' }));
    await waitFor(() => expect((f.mock.calls as unknown as [string, RequestInit][]).some(([u, init]) => u.endsWith('/questions/q1/reply/read') && init?.method === 'POST')).toBe(true));
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
    expect(await within(form).findByRole('option', { name: '버스덕트' })).toBeTruthy();
    fireEvent.change(within(form).getByLabelText('속성'), { target: { value: '정격전압' } });
    fireEvent.change(within(form).getByLabelText('값'), { target: { value: '13' } });
    expect(within(form).getByText(/값이 고른 글에 없습니다/)).toBeTruthy();
    expect((within(form).getByRole('button', { name: '추가하고 비교' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(form).getByLabelText('값'), { target: { value: '12' } });
    fireEvent.change(within(form).getByLabelText('단위'), { target: { value: 'kV' } });
    fireEvent.click(within(form).getByRole('button', { name: '추가하고 비교' }));
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

  it('모두 확인은 일괄 결정 경로를 한 번 부르고 기다리는 동안 건수를 보인다', async () => {
    const f = serve();
    const pend = (i: number, id: string) => ({ ...MOCK_WORKSPACE_REQUIREMENTS[i], decision: { needed: true, decided: true, reviewed: false, decision_id: id, summary: '충족 확정', excluded: false } }) as never;
    const rows = [pend(0, 'd1'), pend(1, 'd2'), pend(2, 'd3'), ...MOCK_WORKSPACE_REQUIREMENTS.slice(3)];
    renderAt('/tft/projects/p1', 'tft/projects/:projectId', <Decide project={project} documents={[]} rows={rows} role="responsible" actorId="boss" />);
    const reply = bulkReply;
    let release = () => {};
    bulkReply = items => new Promise<Response>(done => { release = () => done(reply(items)); }) as never;
    fireEvent.click(screen.getByRole('button', { name: '모두 확인' }));
    expect(await screen.findByRole('button', { name: '확인 중 (3건)' })).toBeTruthy();
    release();
    bulkReply = reply;
    await waitFor(() => expect(bulks(f)).toHaveLength(1));
    expect(bulks(f)[0].map((x: { action: string; decision_id: string }) => [x.action, x.decision_id])).toEqual([['review_confirm', 'd1'], ['review_confirm', 'd2'], ['review_confirm', 'd3']]);
    expect(decisions(f)).toEqual([]);
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

describe('보드 맞춤', () => {
  it('회신 받은 질의 행은 버튼 대신 회신 받음을 보이고, 설비별로 보기는 남은 것만 설비로 묶는다', async () => {
    serve([replied({})]);
    show('sales');
    const row = (await screen.findByText('회신 받음', { selector: 'td span' })).closest('tr')!;
    expect(within(row).queryByRole('button', { name: '질의서에 넣기' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '설비별로 보기' }));
    const groups = [...document.querySelectorAll('tr.g')].map(g => g.textContent?.split(' ')[0]);
    // KAN-231: 맥락 태그가 없으면 설비 계열(MV·TR·LV·공통)로 묶는다(보드와 같다).
    expect(groups).toEqual(['MV', 'TR', 'LV', '공통']);
    expect(screen.queryByText('충족 14건 펼치기')).toBeNull();
  });
  it('엑셀은 보이는 요구를 CSV로 내려받는다', () => {
    const create = vi.fn(() => 'blob:x'); vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    show('sales');
    fireEvent.click(screen.getByRole('button', { name: '보이는 요구 CSV' }));
    expect(create).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });
});

describe('출력 탭 서버 출력 (KAN-221)', () => {
  const EXPORT = '33333333-3333-4333-8333-333333333333', JOB = '22222222-2222-4222-8222-222222222222';
  const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  const server = ({ reviews = [{ review_version: 2 }], exports = [] as unknown[], job = 'SUCCEEDED' } = {}) => {
    const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'sales' } }), { headers: { 'X-CSRF-Token': 'csrf' } });
      if (url.endsWith('/draft/review-versions')) return jsonResponse(200, { data: { items: reviews } });
      if (url.endsWith('/draft/exports') && init?.method === 'POST') return jsonResponse(202, { data: { export_id: EXPORT, job_id: JOB, status: 'QUEUED', status_url: `/api/v1/platform/jobs/${JOB}` } });
      if (url.endsWith('/draft/exports')) return jsonResponse(200, { data: { items: exports } });
      if (url.endsWith(`/platform/jobs/${JOB}`)) return jsonResponse(200, { data: { job_id: JOB, project_id: 'p1', owner: 'tft', kind: 'rfp.export', status: job } });
      if (url.endsWith(`/exports/${EXPORT}/file`)) return new Response('xlsx-bytes', { headers: { 'Content-Type': XLSX, 'Content-Disposition': "attachment; filename=\"x.xlsx\"; filename*=UTF-8''RFP-91%20%EB%8C%80%EC%9D%91%20%ED%98%84%ED%99%A9%ED%91%9C.xlsx" } });
      return jsonResponse(200, { data: { items: [] } });
    });
    vi.stubGlobal('fetch', f);
    setApiMode('live');
    return f;
  };
  const posts = (f: ReturnType<typeof server>) => (f.mock.calls as unknown as [string, RequestInit][]).filter(([u, i]) => u.endsWith('/draft/exports') && i?.method === 'POST').map(([, i]) => JSON.parse(String(i.body)));
  const openOutput = () => { show('sales'); fireEvent.click(screen.getByRole('tab', { name: /출력/ })); };
  const saved = () => { const names: string[] = []; vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { names.push(this.download); }); vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: () => undefined })); return names; };

  it('초안이 낡았으면 저장 전에 모델 없이 초안을 다시 만들고 그 판본으로 저장한다', async () => {
    const f = server({ reviews: [] });
    const base = f.getMockImplementation()!;
    f.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/draft/regenerate')) return jsonResponse(200, { data: { project_id: 'p1', version: 6, sections: [] } });
      if (url.endsWith('/draft/review-versions') && init?.method === 'POST') return jsonResponse(201, { data: { review_version: 1 } });
      if (url.endsWith('/projects/p1/draft')) return jsonResponse(200, { data: { project_id: 'p1', version: 5, freshness: { status: 'STALE' }, sections: [] } });
      return base(input, init);
    });
    const ready = { ...project, decision_gate: { undecided: 0, deviation_missing: 0, open_queries: 0, review_pending: 0 } } as ProjectDetail;
    renderAt('/tft/projects/p1', 'tft/projects/:projectId', <Decide project={ready} documents={[]} rows={MOCK_WORKSPACE_REQUIREMENTS} role="sales" actorId="sales" />);
    fireEvent.click(screen.getByRole('tab', { name: /출력/ }));
    const save = screen.getByRole('button', { name: '검토본 저장' }) as HTMLButtonElement;
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    const calls = f.mock.calls as unknown as [string, RequestInit][];
    await waitFor(() => expect(calls.some(([u, i]) => u.endsWith('/draft/review-versions') && i?.method === 'POST')).toBe(true));
    const order = calls.filter(([, i]) => i?.method === 'POST').map(([u]) => u.split('/').pop());
    expect(order.indexOf('regenerate')).toBeLessThan(order.indexOf('review-versions'));
    const body = JSON.parse(String(calls.find(([u, i]) => u.endsWith('/draft/review-versions') && i?.method === 'POST')![1].body));
    expect(body.version).toBe(6);
  });

  it('검토본이 없으면 버튼을 끄고 먼저 저장하라고 알린다', async () => {
    server({ reviews: [] });
    openOutput();
    expect(await screen.findByText(/검토본을 먼저 저장하세요/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '대응 현황표 XLSX' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '요약서 DOCX' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('최신 검토본으로 내보내기를 만들고 끝나면 서버 파일 이름으로 내려받는다', async () => {
    const f = server();
    const names = saved();
    openOutput();
    const button = screen.getByRole('button', { name: '대응 현황표 XLSX' }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(names).toEqual(['RFP-91 대응 현황표.xlsx']));
    expect(posts(f)).toEqual([{ review_version: 2, format: 'XLSX' }]);
    const post = (f.mock.calls as unknown as [string, RequestInit][]).find(([u, i]) => u.endsWith('/draft/exports') && i?.method === 'POST')!;
    expect(new Headers(post[1].headers).get('Idempotency-Key')).toBeTruthy();
    vi.restoreAllMocks();
  });

  it('같은 판본의 성공한 내보내기가 있으면 다시 만들지 않고 내려받는다', async () => {
    const f = server({ exports: [{ export_id: EXPORT, review_version: 2, format: 'XLSX', status: 'SUCCEEDED', file_name: 'old.xlsx' }] });
    const names = saved();
    openOutput();
    const button = screen.getByRole('button', { name: '대응 현황표 XLSX' }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(names).toHaveLength(1));
    expect(posts(f)).toEqual([]);
    vi.restoreAllMocks();
  });

  it('작업이 실패하면 오류 문구를 보이고 파일을 받지 않는다', async () => {
    server({ job: 'FAILED' });
    const names = saved();
    openOutput();
    const button = screen.getByRole('button', { name: '요약서 DOCX' }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    expect((await screen.findByText(/DOCX 출력을 만들지 못했습니다/)).getAttribute('role')).toBe('alert');
    expect(names).toEqual([]);
    vi.restoreAllMocks();
  });
});

describe('책임자 맡기 (manager)', () => {
  it('담당은 그대로 두고 자기 자신을 책임자로 지정한다', async () => {
    const f = serve();
    renderAt('/tft/projects/p1', 'tft/projects/:projectId', <TakeResponsibility project={{ ...project, version: 3, responsible_user_id: null } as ProjectDetail} actorId="mgr" />);
    fireEvent.click(screen.getByRole('button', { name: '책임자 맡기' }));
    await waitFor(() => expect((f.mock.calls as unknown as [string, RequestInit][]).some(([u]) => String(u).endsWith('/projects/p1/assignment'))).toBe(true));
    const [, init] = (f.mock.calls as unknown as [string, RequestInit][]).find(([u]) => String(u).endsWith('/assignment'))!;
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toMatchObject({ version: 3, sales_user_id: 'sales', responsible_user_id: 'mgr' });
  });
});
