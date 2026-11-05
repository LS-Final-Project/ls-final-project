// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ResponseDecision } from './ResponseDecision';
import { getComparison, listQuestions } from '../api/rfp';
import { getSupplementRequirement, getSupplementOptions, listSupplementCandidates, selectSupplement, type SupplementOptions, type PartnerCandidate, type RequirementSummary } from '../api/rfp-supplement';
import { MOCK_COMPARISON, MOCK_DETAIL, MOCK_REQUIREMENTS } from '../mocks/rfp-table';
vi.mock('../api/rfp', async original => ({ ...await original<object>(), getComparison: vi.fn(), listQuestions: vi.fn() }));
vi.mock('../api/rfp-supplement', async original => ({ ...await original<object>(), getSupplementRequirement: vi.fn(), getSupplementOptions: vi.fn(), listSupplementCandidates: vi.fn(), selectSupplement: vi.fn() }));
const requirement = MOCK_REQUIREMENTS[0] as RequirementSummary;
const options = { requirement_id: requirement.requirement_id, version: 1, options: [{ path: 'NONE', rank: 1, allowed: true, candidate_codes: [], conditions_to_confirm: [], required_expertise: [], gate: { requires: 'NONE', status: 'NOT_REQUIRED' } }] } as unknown as SupplementOptions;
const candidate = { candidate_code: 'P-A', display_name: '협력사 A', profile_version: 2, certifications: [], grades: [{ requirement_id: requirement.requirement_id, requirement_version: 1, profile_version: 2, grade: 'REVIEW', reasons: ['확인 필요'], exclusion_reasons: [], mandatory_check: { passed: [], failed: [], unknown: ['인증'] } }] } as unknown as PartnerCandidate;
beforeEach(() => {
  vi.mocked(listQuestions).mockResolvedValue({ data: { items: [] } });
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, spec: { ...MOCK_DETAIL.spec, attribute_key: 'rated_power' } } as never });
  vi.mocked(getComparison).mockResolvedValue({ data: MOCK_COMPARISON });
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: options, meta: { request_id: 'test' } });
  vi.mocked(listSupplementCandidates).mockResolvedValue({ data: { items: [candidate], manufacturer_list_basis: 'CURRENT_PROJECT_INPUT' }, meta: { request_id: 'test' } });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });
function show(canSelect = true, onSaved = vi.fn(), selectedRequirement = requirement, reviewDepartments?: string[]) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><ResponseDecision projectId="project" actorId="actor" requirement={selectedRequirement} canSelect={canSelect} onSaved={onSaved} reviewDepartments={reviewDepartments} /></MemoryRouter></QueryClientProvider>);
  return onSaved;
}
test('stale comparison and candidate profiles prevent saving', async () => {
  vi.mocked(getComparison).mockResolvedValue({ data: { ...MOCK_COMPARISON, requirement_version: 2 } });
  vi.mocked(listSupplementCandidates).mockResolvedValue({ data: { items: [{ ...candidate, profile_version: 3 }], manufacturer_list_basis: 'CURRENT_PROJECT_INPUT' }, meta: { request_id: 'test' } });
  show();
  expect(await screen.findByText(/현재 요구 판본의 자사 비교 결과가 아닙니다/)).toBeTruthy();
  expect(await screen.findByText(/현재 요구·프로필 판본과 연결된 등급/)).toBeTruthy();
  expect(screen.queryByText('합성-TRF-1000')).toBeNull();
  expect(screen.queryByRole('button', { name: '대응 방안 저장' })).toBeNull();
  expect(selectSupplement).not.toHaveBeenCalled();
});
test('read only access disables original selection form without opening mutations', async () => {
  show(false);
  fireEvent.click(screen.getByRole('button', { name: '대응 방안 기록' }));
  expect((await screen.findByRole('combobox', { name: '기록할 대응 방안' })).closest('fieldset')?.disabled).toBe(true);
  expect((screen.getByRole('button', { name: '대응 방안 저장' }) as HTMLButtonElement).disabled).toBe(true);
  expect(selectSupplement).not.toHaveBeenCalled();
});
test('explicit save uses original version and invokes parent refresh', async () => {
  vi.mocked(selectSupplement).mockResolvedValue({ data: { requirement_id: requirement.requirement_id, version: 2 } as never, meta: { request_id: 'test' } });
  const saved = show();
  fireEvent.click(screen.getByRole('button', { name: '대응 방안 기록' }));
  fireEvent.change(await screen.findByRole('combobox', { name: '기록할 대응 방안' }), { target: { value: 'NONE' } });
  fireEvent.change(screen.getByRole('textbox', { name: '담당자와 후속 확인 사항' }), { target: { value: '자사 확인 담당' } });
  expect(selectSupplement).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '대응 방안 저장' }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(selectSupplement).toHaveBeenCalledWith(requirement.requirement_id, expect.objectContaining({ version: 1, path: 'NONE' }), expect.any(String), 'actor');
  expect(getComparison).toHaveBeenCalledWith(requirement.requirement_id, { useMockFallback: false });
});

test('failed options refresh disables cached selection evidence', async () => {
  show();
  fireEvent.click(screen.getByRole('button', { name: '대응 방안 기록' }));
  await screen.findByRole('combobox', { name: '기록할 대응 방안' });
  vi.mocked(getSupplementOptions).mockRejectedValueOnce(new Error('조회 실패'));
  fireEvent.click(screen.getByRole('button', { name: '대응 근거 최신 조회' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: '대응 방안 저장' })).toBeNull());
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('default view compares typed customer and product values before opening evidence', async () => {
  show();
  const comparison = await screen.findByRole('region', { name: '선택 요구 현재 상태' });
  await waitFor(() => expect(comparison.textContent).toContain('합성-TRF-1000'));
  expect(comparison.textContent).toContain('1500 kVA');
  expect(comparison.textContent).toContain('1000 kVA');
  expect(comparison.textContent).not.toContain(MOCK_DETAIL.judgement.reason);
  expect(screen.queryByText('개발 검토')).toBeNull();
  expect(screen.queryByText('협력사 후보')).toBeNull();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('unknown development gate opens real selection without suggesting development', async () => {
  show();
  fireEvent.click(await screen.findByRole('button', { name: '대응 방안 기록' }));
  expect(screen.getByRole('region', { name: '대응 경로 선택' })).toBeTruthy();
  expect(screen.queryByText('개발팀에 확인할 질문 작성')).toBeNull();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('collapsing a selection keeps the unsaved role split and does not submit it', async () => {
  show();
  fireEvent.click(screen.getByRole('button', { name: '대응 방안 기록' }));
  await screen.findByRole('combobox', { name: '기록할 대응 방안' });
  fireEvent.change(screen.getByRole('textbox', { name: '담당자와 후속 확인 사항' }), { target: { value: '검토 중인 역할' } });
  fireEvent.click(screen.getByRole('button', { name: '비교 근거와 원문' }));
  expect(screen.queryByRole('textbox', { name: '담당자와 후속 확인 사항' })).toBeNull();
  expect(screen.getByRole('region', { name: '비교 근거와 원문' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '대응 방안 기록' }));
  expect(screen.queryByRole('region', { name: '비교 근거와 원문' })).toBeNull();
  expect((screen.getByRole('textbox', { name: '담당자와 후속 확인 사항' }) as HTMLTextAreaElement).value).toBe('검토 중인 역할');
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('detail-derived rereview stays visible and an AI path is not a completed human selection', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, needs_rereview: true, judgement: { ...MOCK_DETAIL.judgement, path: 'EXTERNAL_PRODUCT' } } as never });
  show();
  await screen.findByText(/판정에 기록된 경로/);
  const current = screen.getByRole('region', { name: '선택 요구 현재 상태' });
  expect(current.textContent).toContain('원문 확인·재검토 필요');
  expect(current.textContent).toContain('AI 제안');
  expect(screen.getByRole('region', { name: '선택 요구 현재 상태' }).textContent).toContain('판정에 기록된 경로');
  expect(screen.queryByText('선택 완료')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '원문·판정 재확인 →' }));
  expect(screen.getByRole('region', { name: '비교 근거와 원문' })).toBeTruthy();
});


test('validated detail state takes priority and list disagreement remains visible', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, status: 'EVIDENCE_FOUND' } } as never });
  show();
  const warning = await screen.findByText(/목록과 상세의 판정 상태가 다릅니다/);
  expect(warning.closest('details')).toBeNull();
  expect(screen.getByRole('region', { name: '선택 요구 현재 상태' }).textContent).toContain('근거 있음');
  expect(screen.getByRole('button', { name: '최신 근거 다시 조회 →' })).toBeTruthy();
});


test('unknown option candidate stays visible outside collapsed selection and asks for refresh', async () => {
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, options: [{ ...options.options[0], candidate_codes: ['missing'] }] }, meta: { request_id: 'test' } });
  show();
  const warning = await screen.findByText(/대응 경로와 후보의 연결이 일치하지 않습니다/);
  expect(warning.closest('details')).toBeNull();
  expect(screen.getByRole('button', { name: '최신 근거 다시 조회 →' })).toBeTruthy();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('commercial warranty compares typed values and opens terms selection without development or automatic save', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, spec: { ...MOCK_DETAIL.spec, attribute_key: 'warranty_months', value: '36', unit: '개월' } } as never });
  vi.mocked(getComparison).mockResolvedValue({ data: { ...MOCK_COMPARISON, candidates: [{ ...MOCK_COMPARISON.candidates[0], attribute_match: { ...MOCK_COMPARISON.candidates[0].attribute_match, matched_attribute: 'warranty_months', our_value: '24', our_unit: 'months' } }] } });
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, deficiency_cause: 'COMMERCIAL', options: [{ ...options.options[0], path: 'OWN_TERMS_REVIEW', conditions_to_confirm: ['영업·보증 담당 조건 변경 확인'] }] }, meta: { request_id: 'test' } });
  show();
  await screen.findByText('36 개월');
  const action = screen.getByRole('button', { name: '대응 방안 기록' });
  const current = screen.getByRole('region', { name: '선택 요구 현재 상태' });
  expect(current.textContent).toContain('36 개월');
  expect(current.textContent).toContain('24 개월');
  expect(current.textContent).toContain('12 개월 차이');
  expect(screen.queryByText('개발팀에 확인할 질문 작성')).toBeNull();
  fireEvent.click(action);
  expect(screen.getByRole('region', { name: '대응 경로 선택' })).toBeTruthy();
  expect(screen.getAllByText('영업·보증 담당 조건 변경 확인').length).toBeGreaterThan(0);
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('multiple products are individually identified and text or incompatible units never produce a gap', async () => {
  vi.mocked(getComparison).mockResolvedValue({ data: { ...MOCK_COMPARISON, candidates: [MOCK_COMPARISON.candidates[0], { ...MOCK_COMPARISON.candidates[0], product_code: '다른 제품', attribute_match: { ...MOCK_COMPARISON.candidates[0].attribute_match, our_value: '24', our_unit: 'months' } }] } });
  show();
  await screen.findByText('비교 후보별 조건입니다. 대응 제품은 아직 선택되지 않았습니다.');
  const current = screen.getByRole('region', { name: '선택 요구 현재 상태' });
  expect(current.textContent).toContain('다른 제품');
  expect(current.textContent).not.toContain('1476');
});

test('task server development gate preserves the actual feasibility request flow', async () => {
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, deficiency_cause: 'TASK', options: [{ ...options.options[0], path: 'OWN_DEVELOPMENT' }, { ...options.options[0], path: 'TASK_SUBCONTRACT', allowed: false, gate: { requires: 'OWN_DEVELOPMENT_CHECK', status: 'NOT_REQUIRED' } }] }, meta: { request_id: 'test' } });
  show();
  const action = await screen.findByRole('button', { name: /^자사 수행/ });
  expect(screen.queryByRole('button', { name: /^협력사 위탁/ })).toBeNull();
  fireEvent.click(action);
  expect(action.getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByRole('heading', { name: '개발팀 가능성·일정 확인' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' })).toBeTruthy();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('unknown comparison attribute never presents a product value as the current requirement condition', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: MOCK_DETAIL as never });
  show();
  await screen.findByText('이 요구와 비교할 속성 확인 필요');
  expect(screen.getByRole('region', { name: '선택 요구 현재 상태' }).textContent).not.toContain('1000 kVA');
});

test('a matching value keeps distinct customer and own conditions visible without a computed gap', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, spec: { ...MOCK_DETAIL.spec, attribute_key: 'rated_power', conditions: [{ name: '온도', value: '40', unit: '°C' }] }, judgement: { ...MOCK_DETAIL.judgement, status: 'CONDITIONAL' } } as never });
  vi.mocked(getComparison).mockResolvedValue({ data: { ...MOCK_COMPARISON, candidates: [{ ...MOCK_COMPARISON.candidates[0], attribute_match: { ...MOCK_COMPARISON.candidates[0].attribute_match, result: 'MEETS', our_conditions: [{ name: '온도', value: '25', unit: '°C' }] } }] } });
  show();
  await screen.findByText('적용 조건: 온도 · 40 °C');
  const current = screen.getByRole('region', { name: '선택 요구 현재 상태' });
  expect(current.textContent).toContain('자사 적용 조건: 온도 · 25 °C');
  expect(current.textContent).toContain('값 비교상 충족');
  expect(current.textContent).toContain('적용 조건 확인 필요');
  expect(current.textContent).not.toContain('500 kVA 차이');
});

test('an evidenced task without work paths opens its evidence instead of an empty selection', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, spec: { ...MOCK_DETAIL.spec, task: '시험 성적서 제출' }, judgement: { ...MOCK_DETAIL.judgement, status: 'EVIDENCE_FOUND' } } as never });
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, deficiency_cause: null, options: [] }, meta: { request_id: 'test' } });
  show(true, vi.fn(), { ...requirement, task: '시험 성적서 제출', status: 'EVIDENCE_FOUND' });
  fireEvent.click(await screen.findByRole('button', { name: '충족 근거 확인 →' }));
  expect(screen.getByRole('region', { name: '비교 근거와 원문' })).toBeTruthy();
  expect(screen.queryByRole('region', { name: '대응 경로 선택' })).toBeNull();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('a recorded development question remains available for commercial requirements without fabricating delivery', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'PENDING', rnd_question_id: 'recorded-question' } } as never });
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, deficiency_cause: 'COMMERCIAL', options: [{ ...options.options[0], path: 'OWN_TERMS_REVIEW' }] }, meta: { request_id: 'test' } });
  show();
  await screen.findByText(/현재 대응 조건과 별도로 개발 검토 기록/);
  fireEvent.click(screen.getByRole('button', { name: '개발팀 가능성·일정 확인' }));
  expect(await screen.findByText('이 요구의 개발 검토 요청 기록이 없습니다.')).toBeTruthy();
  expect(screen.getByText(/실제 이메일 전달 여부/)).toBeTruthy();
  expect(screen.queryByText('발송 완료')).toBeNull();
  expect(screen.queryByRole('region', { name: '대응 경로 선택' })).toBeNull();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('designated procurement stays separate from partner and development suggestions', async () => {
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, deficiency_cause: 'DESIGNATED_MODEL', options: [{ ...options.options[0], path: 'CUSTOMER_SPECIFIED_PROCUREMENT' }] }, meta: { request_id: 'test' } });
  show();
  fireEvent.click(await screen.findByRole('button', { name: '대응 방안 기록' }));
  expect(screen.getByRole('region', { name: '대응 경로 선택' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: '개발팀 가능성·일정 확인' })).toBeNull();
  expect(screen.queryByRole('button', { name: '협력사 후보 비교' })).toBeNull();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('switching evidence preserves an unsaved internal development question', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED' } } as never });
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, options: [{ ...options.options[0], gate: { requires: 'OWN_DEVELOPMENT_CHECK', status: 'NOT_REQUIRED' } }] }, meta: { request_id: 'test' } });
  show(true, vi.fn(), requirement, ['RND_FW']);
  // 사양 차이: 서버가 개발 확인을 요구하면 '자사 개발'이 선택지로 나온다.
  expect(await screen.findByText('어떻게 보완할까요?')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /^자사 개발/ }));
  await waitFor(() => expect((screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }));
  fireEvent.change(screen.getByLabelText('질문'), { target: { value: '고객 납기에 가능한지 검토 부탁드립니다.' } });
  fireEvent.click(screen.getByRole('button', { name: '비교 근거와 원문' }));
  expect(screen.queryByRole('textbox', { name: '질문' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /^자사 개발/ }));
  expect((screen.getByLabelText('질문') as HTMLTextAreaElement).value).toBe('고객 납기에 가능한지 검토 부탁드립니다.');
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('commercial default shows the question task, preserves its input across evidence, and records terms without a partner field', async () => {
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, deficiency_cause: 'COMMERCIAL', options: [{ ...options.options[0], path: 'OWN_TERMS_REVIEW' }] }, meta: { request_id: 'test' } });
  show(true, vi.fn(), requirement, ['SALES_OPS']);
  const question = await screen.findByRole('button', { name: '검토 질문 작성' });
  expect(screen.queryByRole('combobox', { name: '기록할 대응 방안' })).toBeNull();
  expect(screen.queryByRole('button', { name: '영업·보증 조건 변경 확인 →' })).toBeNull();
  fireEvent.click(question);fireEvent.change(screen.getByLabelText('대상'), { target: { value: 'SALES_OPS' } });
  fireEvent.change(screen.getByLabelText('질문'), { target: { value: '조건 변경 가능 여부 검토 중입니다.' } });
  fireEvent.click(screen.getByRole('button', { name: '비교 근거와 원문' }));
  expect(screen.queryByRole('textbox', { name: '질문' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '질문·회신 기록' }));
  expect((screen.getByLabelText('질문') as HTMLTextAreaElement).value).toBe('조건 변경 가능 여부 검토 중입니다.');
  fireEvent.click(screen.getByRole('button', { name: '대응 방안 기록' }));
  expect((screen.getByRole('combobox', { name: '기록할 대응 방안' }) as HTMLSelectElement).value).toBe('OWN_TERMS_REVIEW');
  expect(screen.queryByText('이 경로에 등록된 후보가 없습니다.')).toBeNull();
  expect(screen.queryByLabelText('후보')).toBeNull();
  expect(screen.getByRole('textbox', { name: '누가 어떤 조건을 확인할지' })).toBeTruthy();
  expect(screen.getByPlaceholderText(/영업 담당이 보증 연장 가능 여부/)).toBeTruthy();
  expect((screen.getByRole('button', { name: '대응 방안 저장' }) as HTMLButtonElement).disabled).toBe(true);
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('partner paths retain candidate selection and switching to a nonpartner clears selected candidates', async () => {
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, options: [{ ...options.options[0], path: 'EXTERNAL_PRODUCT', candidate_codes: ['P-A'] }, { ...options.options[0], path: 'OTHER_OWN_PRODUCT' }] }, meta: { request_id: 'test' } });
  vi.mocked(selectSupplement).mockResolvedValue({ data: { requirement_id: requirement.requirement_id, version: 2 } as never, meta: { request_id: 'test' } });
  const saved = show();fireEvent.click(screen.getByRole('button', { name: '대응 방안 기록' }));
  const paths = await screen.findByRole('combobox', { name: '기록할 대응 방안' });
  fireEvent.change(paths, { target: { value: 'EXTERNAL_PRODUCT' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '후보 P-A' }));
  fireEvent.change(paths, { target: { value: 'OTHER_OWN_PRODUCT' } });
  expect(screen.queryByRole('checkbox', { name: '후보 P-A' })).toBeNull();
  fireEvent.change(screen.getByRole('textbox', { name: '담당자와 후속 확인 사항' }), { target: { value: '기술지원이 자사 제품 조건을 확인' } });
  fireEvent.click(screen.getByRole('button', { name: '대응 방안 저장' }));await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(selectSupplement).toHaveBeenCalledWith(requirement.requirement_id, expect.objectContaining({ path: 'OTHER_OWN_PRODUCT', candidate_codes: [], role_split: '기술지원이 자사 제품 조건을 확인' }), expect.any(String), 'actor');
});

test('all server options and blocking reasons remain readable alongside the contextual selection', async () => {
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, deficiency_cause: 'COMMERCIAL', options: [{ ...options.options[0], path: 'OWN_TERMS_REVIEW' }, { ...options.options[0], path: 'EXTERNAL_PRODUCT', allowed: false, blocked_reason: '고객 조건에 따라 차단' }] }, meta: { request_id: 'test' } });
  show();await screen.findByRole('button', { name: '검토 질문 작성' });fireEvent.click(screen.getByRole('button', { name: '대응 방안 기록' }));
  fireEvent.click(screen.getByText('대응 방안과 제한 조건'));
  expect(screen.getByText('고객 조건에 따라 차단')).toBeTruthy();
  expect((screen.getByRole('option', { name: '외부 제품' }) as HTMLOptionElement).disabled).toBe(true);expect(selectSupplement).not.toHaveBeenCalled();
});

test('a task without a scalar value keeps its real development gate instead of inventing a missing customer value', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, spec: { ...MOCK_DETAIL.spec, task: '현장 시험 수행', operator: 'TEXT', value: null }, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED' } } as never });
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, deficiency_cause: 'TASK', options: [{ ...options.options[0], gate: { requires: 'OWN_DEVELOPMENT_CHECK', status: 'NOT_REQUIRED' } }] }, meta: { request_id: 'test' } });
  show();fireEvent.click(await screen.findByRole('button', { name: '담당·수행 범위와 제출 근거 확인 →' }));
  expect(screen.getByRole('heading', { name: '개발팀 가능성·일정 확인' })).toBeTruthy();expect(screen.queryByRole('button', { name: '고객 확인 질문 작성' })).toBeNull();expect(selectSupplement).not.toHaveBeenCalled();
});

test('a task card asks who performs it from allowed paths without a value comparison or automatic save', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, spec: { ...MOCK_DETAIL.spec, equipment: 'GIS', attribute: null, task: '설치 후 현장시험 성공적 완료', operator: 'TEXT', value: null, unit: null }, judgement: { ...MOCK_DETAIL.judgement, status: 'INSUFFICIENT_EVIDENCE', unresolved: ['수행 주체 확인(자사/협력사)'] } } as never });
  vi.mocked(getComparison).mockResolvedValue({ data: { ...MOCK_COMPARISON, candidates: [] } });
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, deficiency_cause: 'TASK', customer_permission: 'UNSTATED', options: [{ ...options.options[0], path: 'TASK_SUBCONTRACT', conditions_to_confirm: ['RFP에 외부 보완 허용 여부가 없음: 발주처 확인'] }, { ...options.options[0], path: 'OWN_DEVELOPMENT', rank: 2 }, { ...options.options[0], path: 'JOINT_PROPOSAL', allowed: false }] }, meta: { request_id: 'test' } });
  vi.mocked(listSupplementCandidates).mockResolvedValue({ data: { items: [], manufacturer_list_basis: 'CURRENT_PROJECT_INPUT' }, meta: { request_id: 'test' } });
  show(true, vi.fn(), { ...requirement, code: 'abcd1234:2.1.A:1', task: '설치 후 현장시험 성공적 완료', status: 'INSUFFICIENT_EVIDENCE' }, ['SALES_OPS']);
  const partner = await screen.findByRole('button', { name: /^협력사 위탁/ });
  await waitFor(() => expect(partner.textContent).toContain('등록된 후보 없음'));
  expect(partner.textContent).toContain('발주처가 외부 위탁을 허용하는지 먼저 확인 필요');
  expect(screen.getByRole('button', { name: /^자사 수행/ })).toBeTruthy();
  expect(screen.queryByRole('button', { name: /^공동 제안/ })).toBeNull();
  const current = screen.getByRole('region', { name: '선택 요구 현재 상태' });
  expect(current.textContent).toContain('조항 2.1.A');
  expect(current.textContent).not.toContain('2.1.A-1');
  expect(current.textContent).toContain('설치 후 현장시험 성공적 완료');
  expect(current.textContent).toContain('불가 판정 아님');
  expect(current.textContent).not.toContain('문자 조건');
  expect(screen.queryByLabelText('고객 요구와 자사 조건 비교')).toBeNull();
  expect(screen.queryByRole('button', { name: '협력사 후보 비교' })).toBeNull();
  expect(screen.queryByRole('region', { name: '확인 질문과 기록' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /^아직 모름/ }));
  expect(screen.getByRole('heading', { name: '발주처에 수행 범위 확인' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '발주처 질문 작성' })).toBeTruthy();
  fireEvent.click(partner);
  expect(partner.getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByRole('region', { name: '대응 경로 선택' })).toBeTruthy();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('a spec requirement without a customer value says so and leads to the customer question', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, spec: { ...MOCK_DETAIL.spec, attribute_key: 'rated_power', value: null } } as never });
  show(true, vi.fn(), requirement, ['SALES_OPS']);
  const current = await screen.findByRole('region', { name: '선택 요구 현재 상태' });
  await waitFor(() => expect(current.textContent).toContain('원문에 수치 없음'));
  expect(screen.getByLabelText('고객 요구와 자사 조건 비교')).toBeTruthy();
  expect(current.textContent).not.toContain('값 확인 필요');
  // 값이 없으면 상태와 관계없이 '무엇이 부족한가요?'를 묻고 고객 요구값을 첫 선택지로 둔다.
  expect(screen.getByText('무엇이 부족한가요?')).toBeTruthy();
  const choices = screen.getAllByRole('button').filter(b => b.closest('[role=group]')?.getAttribute('aria-labelledby')?.startsWith('response-choice-label'));
  expect(choices.map(b => b.querySelector('b')?.textContent)).toEqual(['고객 요구값', '자사 근거 자료', '개발 가능성']);
  fireEvent.click(choices[0]);
  expect(screen.getByRole('dialog', { name: /발주처 질의 목록 만들기/ })).toBeTruthy();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('each spec state asks one question with its own choices and never offers a blocked path', async () => {
  const opt = (path: string, rank: number, allowed = true, requires = 'NONE') => ({ ...options.options[0], path, rank, allowed, gate: { requires, status: requires === 'NONE' ? 'NOT_REQUIRED' : 'PENDING' } });
  const choicesOf = async (question: string) => {
    const text = await screen.findByText(question);
    return [...text.parentElement!.querySelectorAll('.response-choice b')].map(b => b.textContent);
  };
  const states: [string, unknown[], string, string[]][] = [
    ['INSUFFICIENT_EVIDENCE', [opt('NONE', 1)], '무엇이 부족한가요?', ['자사 근거 자료', '고객 요구 범위', '개발 가능성']],
    ['CONDITIONAL', [opt('OTHER_OWN_PRODUCT', 1)], '남은 조건을 누가 확인하나요?', ['기술 담당', '발주처', '확인됨']],
    ['SPEC_GAP', [opt('EXTERNAL_PRODUCT', 3, true, 'OWN_DEVELOPMENT_CHECK'), opt('OTHER_OWN_PRODUCT', 1), opt('ALTERNATIVE_SPEC', 4), opt('JOINT_PROPOSAL', 5, false)], '어떻게 보완할까요?', ['다른 자사 제품', '자사 개발', '외부 제품', '대체 사양', '아직 모름']],
    ['EVIDENCE_FOUND', [opt('NONE', 1)], '적용 조건을 확인하고 제안 범위에 넣을까요?', ['근거 확인', '반영']],
  ];
  for (const [status, list, question, expected] of states) {
    vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, spec: { ...MOCK_DETAIL.spec, attribute_key: 'rated_power' }, judgement: { ...MOCK_DETAIL.judgement, status } } as never });
    vi.mocked(getSupplementOptions).mockResolvedValue({ data: { ...options, options: list }, meta: { request_id: 'test' } } as never);
    show(true, vi.fn(), { ...requirement, status: status as RequirementSummary['status'] }, ['TS_MV']);
    expect(await choicesOf(question)).toEqual(expected);
    expect(screen.queryByRole('button', { name: /다음 할 일/ })).toBeNull();
    cleanup();
  }
  expect(selectSupplement).not.toHaveBeenCalled();
});
