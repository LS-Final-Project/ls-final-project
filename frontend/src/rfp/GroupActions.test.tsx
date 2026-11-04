// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ApiError } from '../api/client';
import { createQuestionsBatch, listProjectCandidates, listQuestions, listRequirements, selectSupplementBatch, setRfpReviewDepartments, type Question } from '../api/rfp';
import { me } from '../api/platform';
import { getProject } from '../api/tft';
import { getSupplementOptions, getSupplementRequirement, type RequirementSummary } from '../api/rfp-supplement';
import { MOCK_DETAIL } from '../mocks/rfp-table';
import { GroupSummary, ProjectQuestions, RequestDialog, SelectionDialog } from './GroupActions';
import { batchProblems, draftQuestion, groupSummary, outboxText } from './group-actions';

vi.mock('../api/rfp', async original => ({ ...await original<object>(), createQuestionsBatch: vi.fn(), listQuestions: vi.fn(), listRequirements: vi.fn(), listProjectCandidates: vi.fn(), selectSupplementBatch: vi.fn(), setRfpReviewDepartments: vi.fn() }));
vi.mock('../api/tft', async original => ({ ...await original<object>(), getProject: vi.fn() }));
vi.mock('../api/platform', async original => ({ ...await original<object>(), me: vi.fn() }));
vi.mock('../api/rfp-supplement', async original => ({ ...await original<object>(), getSupplementRequirement: vi.fn(), getSupplementOptions: vi.fn() }));

const id = (n: number) => `44444444-4444-4444-8444-${String(n).padStart(12, '0')}`;
const row = (n: number, over: Partial<RequirementSummary> = {}): RequirementSummary => ({
  requirement_id: id(n), code: `abcd1234:1.3.2:${n}`, equipment: 'GIS', task: null, attribute: '정격 전류', attribute_key: null, value_text: `${n}00 A`,
  necessity: 'MANDATORY', status: 'INSUFFICIENT_EVIDENCE', path: null, open_questions: 0, needs_rereview: false, needs_review: false,
  is_designated_item: false, rnd_check: 'NOT_REQUIRED', version: 1, provenance_kind: 'ai_draft', ...over,
});
const question = (n: number, over: Partial<Question> = {}) => ({
  question_id: `q-${n}`, requirement_id: id(n), status: 'OPEN', target: 'INTERNAL', target_dept: 'TS_MV', issue_type: 'TECH_ADVICE', question_text: `질문 ${n}`,
  version: 1, origin: { clause_id: null, page: null }, created_at: '2026-11-04T00:00:00Z', reply_received_at: null, ...over,
}) as unknown as Question;
const project = (departments: string[], version = 3) => ({ data: { id: 'p', name: 'D22', version, review_departments: departments, sales_user_id: 'actor', responsible_user_id: 'other' } }) as never;
const detail = (n: number) => ({ data: { ...MOCK_DETAIL, requirement_id: id(n), code: `abcd1234:1.3.2:${n}`, version: 1, origin: { ...MOCK_DETAIL.origin, clause_id: `c-${n}`, page: n, quote: `원문 ${n}` }, spec: { ...MOCK_DETAIL.spec, value: `${n}00`, unit: 'A' } } }) as never;

beforeEach(() => {
  vi.mocked(listRequirements).mockResolvedValue({data:{items:[],next_cursor:null}});
  vi.mocked(me).mockResolvedValue({ data: { id: 'actor' } } as never);
  vi.mocked(getProject).mockResolvedValue(project(['TS_MV']));
  vi.mocked(listQuestions).mockResolvedValue({ data: { items: [] } });
  vi.mocked(listProjectCandidates).mockResolvedValue({ data: { items: [] } } as never);
  vi.mocked(getSupplementRequirement).mockImplementation(async requirementId => detail(Number(requirementId.slice(-12))));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

test('group summary counts situations and lists concrete actions in order', () => {
  const rows = [
    row(1), row(2), row(3, { open_questions: 1 }), row(4, { value_text: null }), row(5, { status: 'EVIDENCE_FOUND' }),
    row(6, { status: 'CONDITIONAL' }), row(7, { status: 'SPEC_GAP' }), row(8, { task: '현장 시험', status: 'INSUFFICIENT_EVIDENCE' }),
    row(9, { task: '교육', path: 'TASK_SUBCONTRACT' }), row(1),
  ];
  const summary = groupSummary(rows, [question(3), question(4, { status: 'REPLIED' }), question(99)]);
  expect(summary.total).toBe(9);
  expect(summary.counts).toEqual({ evidence: 1, conditional: 1, gap: 1, material: 4, task: 2 });
  expect(summary.actions.map(a => [a.title, a.questions?.length ?? a.rows.length, a.next])).toEqual([
    ['자료 부족', 2, '기술 담당에게 자료 요청서 만들기'],
    ['요구값 없음', 1, '발주처 질의 목록 만들기'],
    ['과업', 1, '수행 주체 정하기'],
    ['사양 차이', 1, '보완 방안 고르기'],
    ['남은 조건', 1, '적용 조건 확인하기'],
    ['근거 있음', 1, '적용 조건 검토 후 제안 범위에 반영'],
    ['작성한 질문', 1, '받은 회신 기록하기'],
  ]);
});

test('group summary renders the action list and a list action filters the rows', async () => {
  const onShowRows = vi.fn();
  wrap(<GroupSummary projectId="p" actorId="actor" name="GIS" rows={[row(1), row(7, { status: 'SPEC_GAP' })]} onShowRows={onShowRows} />);
  const first = await screen.findByRole('button', { name: /자료 부족 1건/ });
  expect(first.textContent).toContain('기술 담당에게 자료 요청서 만들기');
  await waitFor(() => expect(screen.getByText('협력사 후보').nextElementSibling?.textContent).toBe('0곳'));
  fireEvent.click(screen.getByRole('button', { name: /사양 차이 1건/ }));
  expect(onShowRows).toHaveBeenCalledWith('gap');
  fireEvent.click(first);
  expect(await screen.findByRole('dialog', { name: /자료 요청서 만들기 · GIS/ })).toBeTruthy();
});

test('request dialog checks the first 50 rows, registers a department inline, then records every item in one batch', async () => {
  vi.mocked(getProject).mockResolvedValue(project([]));
  vi.mocked(setRfpReviewDepartments).mockResolvedValue(project(['TS_MV'], 4));
  vi.mocked(createQuestionsBatch).mockImplementation(async (_p, items) => ({ data: { items: items.map(item => question(Number(item.requirement_id!.slice(-12)), { question_text: item.question_text })) } }));
  const rows = Array.from({ length: 52 }, (_, i) => row(i + 1));
  wrap(<RequestDialog projectId="p" actorId="actor" kind="material" rows={rows} groupName="GIS" onClose={vi.fn()} />);
  expect(screen.getByText('50건 선택 · 한 번에 50건까지')).toBeTruthy();
  expect((screen.getByRole('checkbox', { name: '요구 1.3.2-51 선택' }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole('checkbox', { name: '요구 1.3.2-51 선택' }) as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '모두 해제' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '요구 1.3.2-2 선택' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '요구 1.3.2-3 선택' }));
  fireEvent.click(screen.getByRole('button', { name: '문안 확인' }));
  // 등록된 부서가 없으면 그 자리에서 등록한다. 등록 전에는 기록하지 않는다.
  expect(await screen.findByText(/등록된 검토 부서가 없습니다/)).toBeTruthy();
  const record = await screen.findByRole('button', { name: '질문 2건 기록' }) as HTMLButtonElement;
  await waitFor(() => expect(screen.getByRole('textbox', { name: '요구 1.3.2-2 질문' })).toBeTruthy());
  expect(record.disabled).toBe(true);
  fireEvent.change(screen.getByRole('combobox', { name: '등록할 부서' }), { target: { value: 'TS_MV' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /이 부서 담당자도/ }));
  fireEvent.click(screen.getByRole('button', { name: '부서 등록' }));
  await waitFor(() => expect(setRfpReviewDepartments).toHaveBeenCalledWith('p', 3, ['TS_MV'], 'actor'));
  await waitFor(() => expect(record.disabled).toBe(false));
  const text = screen.getByRole('textbox', { name: '요구 1.3.2-3 질문' }) as HTMLTextAreaElement;
  expect(text.value).toContain('자사 자료에서 근거를 찾지 못했습니다.');
  expect(text.closest('article')?.textContent).toContain('고객 요구: 300 A');
  fireEvent.change(text, { target: { value: '고친 질문' } });
  fireEvent.click(record);
  await screen.findByText('질문 2건을 기록했습니다. 아래 내용을 복사해 직접 보내세요.');
  const [, items, key] = vi.mocked(createQuestionsBatch).mock.calls[0];
  expect(items.map(i => [i.requirement_id, i.target, i.target_dept, i.issue_type, i.origin.clause_id, i.question_text.slice(0, 5)])).toEqual([
    [id(2), 'INTERNAL', 'TS_MV', 'TECH_ADVICE', 'c-2', '자사 자료'], [id(3), 'INTERNAL', 'TS_MV', 'TECH_ADVICE', 'c-3', '고친 질문'],
  ]);
  expect(key).toEqual(expect.any(String));
  const outbox = screen.getByRole('textbox', { name: '중압기기 기술지원에 보낼 글' }) as HTMLTextAreaElement;
  expect(outbox.value).toContain('2. 요구 1.3.2-3 · GIS · 정격 전류');
  expect(screen.getByText(/앱은 메일을 보내지 않습니다/)).toBeTruthy();
});

test('request dialog shows an all-or-nothing rejection on the item it belongs to', async () => {
  vi.mocked(createQuestionsBatch).mockRejectedValue(new ApiError(422, 'Some questions are invalid.', 'r', 'VALIDATION_ERROR', [{ index: '1', code: 'VALIDATION_ERROR', message: 'Ask a review department of the project.' }]));
  wrap(<RequestDialog projectId="p" actorId="actor" kind="material" rows={[row(1), row(2)]} groupName="GIS" onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '문안 확인' }));
  fireEvent.change(await screen.findByRole('combobox', { name: '받는 부서' }), { target: { value: 'TS_MV' } });
  await waitFor(() => expect(screen.getByRole('textbox', { name: '요구 1.3.2-2 질문' })).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: '질문 2건 기록' }));
  expect(await screen.findByText('1건에 문제가 있어 아무것도 기록하지 않았습니다. 표시된 요구를 고치거나 선택에서 빼고 다시 기록하세요.')).toBeTruthy();
  const items = screen.getAllByRole('article');
  expect(within(items[1]).getByRole('alert').textContent).toBe('받는 부서가 이 프로젝트의 검토 부서가 아닙니다.');
  expect(within(items[0]).queryByRole('alert')).toBeNull();
});

test('a server without the batch route says so instead of a raw error', async () => {
  vi.mocked(createQuestionsBatch).mockRejectedValue(new ApiError(405, 'HTTP 405', undefined, 'METHOD_NOT_ALLOWED'));
  wrap(<RequestDialog projectId="p" actorId="actor" kind="customer-value" rows={[row(1, { value_text: null })]} groupName="GIS" onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole('textbox', { name: '요구 1.3.2-1 질문' })).toBeTruthy());
  expect(screen.getByText('받는 곳: 발주처')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '질문 1건 기록' }));
  expect(await screen.findByText('이 서버는 아직 여러 건을 한 번에 기록하지 못합니다. 요구를 하나씩 열어 기록하세요.')).toBeTruthy();
  expect(vi.mocked(createQuestionsBatch).mock.calls[0][1][0]).toMatchObject({ target: 'CUSTOMER', target_dept: null, issue_type: 'EMPTY_VALUE' });
});

test('batch problems read string indexes and request-shape fields', () => {
  const problems = batchProblems(new ApiError(422, 'x', undefined, 'VALIDATION_ERROR', [{ index: '0', code: 'TFT_REQUIREMENT_NOT_FOUND', message: 'The requirement was not found.' }, { field: 'body.items.2.question_text', type: 'string_too_short' }]));
  expect([...problems]).toEqual([[0, '요구를 찾을 수 없습니다.'], [2, '입력값을 확인하세요.']]);
});

test('same-path recording runs a dry run, shows what will be recorded and blocked, and saves only after confirmation', async () => {
  const options = (n: number, allowed: boolean) => ({ data: { requirement_id: id(n), version: 1, options: [
    { path: 'TASK_SUBCONTRACT', rank: 2, allowed, candidate_codes: [], conditions_to_confirm: [], required_expertise: [], gate: { requires: 'NONE', status: 'NOT_REQUIRED' } },
    { path: 'OWN_DEVELOPMENT', rank: 1, allowed: true, candidate_codes: [], conditions_to_confirm: [], required_expertise: [], gate: { requires: 'NONE', status: 'NOT_REQUIRED' } },
    { path: 'NONE', rank: 9, allowed: true, candidate_codes: [], conditions_to_confirm: [], required_expertise: [], gate: { requires: 'NONE', status: 'NOT_REQUIRED' } },
  ] } }) as never;
  vi.mocked(getSupplementOptions).mockImplementation(async requirementId => options(Number(requirementId.slice(-12)), requirementId !== id(2)));
  vi.mocked(selectSupplementBatch).mockImplementation(async (_p, body) => ({ data: body.dry_run
    ? { saved: [], allowed: [{ requirement_id: id(1) }], blocked: [{ requirement_id: id(2), code: 'TFT_INVALID_TRANSITION', message: 'Path not allowed.' }] }
    : { saved: [{ requirement_id: id(1), selection: {} as never }], allowed: [], blocked: [] } }));
  const tasks = [row(1, { task: '현장 시험' }), row(2, { task: '교육' })];
  wrap(<SelectionDialog projectId="p" actorId="actor" rows={tasks} task groupName="GIS" onClose={vi.fn()} />);
  const paths = await screen.findByRole('group', { name: '이 과업을 누가 수행합니까?' });
  await waitFor(() => expect(within(paths).getAllByRole('radio')).toHaveLength(2));
  // 서버 순서(rank), 보완 없음은 묶음 선택지에서 뺀다.
  expect([...paths.querySelectorAll('label b')].map(b => b.textContent)).toEqual(['자사 수행', '협력사 위탁']);
  expect(paths.textContent).toContain('2건 중 1건 가능');
  fireEvent.click(within(paths).getAllByRole('radio')[1]);
  fireEvent.change(screen.getByRole('textbox', { name: /수행 담당과 업무 범위/ }), { target: { value: '협력사가 시험 수행' } });
  fireEvent.click(screen.getByRole('button', { name: '기록 전 확인' }));
  expect(await screen.findByText(/기록될 요구 1건 · 막힌 요구 1건/)).toBeTruthy();
  expect(screen.getByText('이 요구에는 고를 수 없는 보완 경로입니다.')).toBeTruthy();
  expect(vi.mocked(selectSupplementBatch).mock.calls).toHaveLength(1);
  expect(vi.mocked(selectSupplementBatch).mock.calls[0][1]).toMatchObject({ dry_run: true, path: 'TASK_SUBCONTRACT', role_split: '협력사가 시험 수행', items: [{ requirement_id: id(1), version: 1 }, { requirement_id: id(2), version: 1 }] });
  expect(vi.mocked(selectSupplementBatch).mock.calls[0][2]).toBeUndefined();
  fireEvent.click(screen.getByRole('button', { name: '요구 1건에 기록' }));
  expect(await screen.findByText("요구 1건에 '협력사 위탁'을(를) 기록했습니다.")).toBeTruthy();
  const [, body, key] = vi.mocked(selectSupplementBatch).mock.calls[1];
  expect(body).toMatchObject({ dry_run: false, items: [{ requirement_id: id(1), version: 1 }] });
  expect(key).toEqual(expect.any(String));
});

test('the text to send groups numbered items with value, quote, question and a reply instruction', () => {
  const text = outboxText({ recipient: '중압기기 기술지원', customer: false, projectName: 'D22', items: [
    { no: '1.3.2-4', subject: 'GIS · 정격 전류', value: '400 A', quote: '정격 전류는 400 A 이상', question: '근거 자료를 알려 주세요.' },
    { no: '1.3.2-5', subject: 'GIS · 적용 표준', value: '', quote: '', question: '확인해 주세요.' },
  ] });
  expect(text).toBe([
    '받는 곳: 중압기기 기술지원', '제목: [D22] RFP 검토 자료 요청 2건', '',
    '안녕하세요.', 'D22 RFP를 검토하면서 자사 자료로 확인하지 못한 항목이 있습니다.', '항목별로 확인 내용과 근거 자료를 알려 주세요.', '',
    '1. 요구 1.3.2-4 · GIS · 정격 전류', '   고객 요구: 400 A', '   원문: "정격 전류는 400 A 이상"', '   질문: 근거 자료를 알려 주세요.', '',
    '2. 요구 1.3.2-5 · GIS · 적용 표준', '   질문: 확인해 주세요.', '',
    '회신하실 때 항목 번호를 함께 적어 주세요. 받은 회신은 RFP 검토 화면에서 질문별로 기록합니다.', '',
  ].join('\n'));
  const customer = outboxText({ recipient: '발주처', customer: true, projectName: 'D22', items: [{ no: '2', subject: 'GIS', value: '', quote: '', question: '값을 알려 주십시오.' }] });
  expect(customer).toContain('제목: [D22] RFP 질의 1건');
  expect(customer).toContain('   질의: 값을 알려 주십시오.');
  expect(customer).not.toContain('RFP 검토 화면');
});


test('보내는 질문은 분석 과정 메모를 빼고 받는 부서가 확인할 구체 항목만 남긴다', () => {
  const detail = (unresolved: string[]) => ({ spec: { undetermined_items: [], conditions: [] }, judgement: { unresolved } }) as unknown as Parameters<typeof draftQuestion>[1];
  const text = draftQuestion('material', detail(['요구 장비 계열 확인', '요구 속성(사양 항목) 확인', 'IEEE C37.90 보유 여부 확인']));
  expect(text).toContain('확인할 것: IEEE C37.90 보유 여부 확인.');
  expect(text).not.toContain('요구 장비 계열');
  expect(draftQuestion('material', detail(['요구 장비 계열 확인']))).not.toContain('확인할 것');
});

test('project questions include unlinked and replied records without implying delivery', async () => {
  vi.mocked(listQuestions).mockResolvedValue({data:{items:[question(1,{requirement_id:null}),question(2,{status:'REPLIED'}),question(3,{status:'DRAFT'})]}});
  wrap(<ProjectQuestions projectId="p" actorId="actor"/>);
  fireEvent.click(screen.getByRole('button',{name:'전체 질문 보기'}));
  const table=await screen.findByRole('table',{name:'작성한 질문'});
  expect(table.textContent).toContain('프로젝트 질문');
  expect(table.textContent).toContain('질문 2');
  expect(table.textContent).toContain('회신 기록됨');
  expect(table.textContent).toContain('초안');
  expect(within(table).getAllByRole('button',{name:'회신 기록'})).toHaveLength(1);
  fireEvent.click(screen.getByRole('button',{name:'보낼 내용 보기'}));
  const text=await screen.findByRole('textbox',{name:/에 보낼 글/});
  expect((text as HTMLTextAreaElement).value).toContain('질문 1');
  expect((text as HTMLTextAreaElement).value).not.toContain('질문 2');
  expect(screen.getByText(/발송 여부는 앱에서 확인하지 않습니다/)).toBeTruthy();
  expect(createQuestionsBatch).not.toHaveBeenCalled();
});
