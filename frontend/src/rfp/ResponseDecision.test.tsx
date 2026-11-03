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
function show(canSelect = true, onSaved = vi.fn(), selectedRequirement = requirement) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><ResponseDecision projectId="project" actorId="actor" requirement={selectedRequirement} canSelect={canSelect} onSaved={onSaved} /></MemoryRouter></QueryClientProvider>);
  return onSaved;
}
test('stale comparison and candidate profiles prevent saving', async () => {
  vi.mocked(getComparison).mockResolvedValue({ data: { ...MOCK_COMPARISON, requirement_version: 2 } });
  vi.mocked(listSupplementCandidates).mockResolvedValue({ data: { items: [{ ...candidate, profile_version: 3 }], manufacturer_list_basis: 'CURRENT_PROJECT_INPUT' }, meta: { request_id: 'test' } });
  show();
  expect(await screen.findByText(/현재 요구 판본의 자사 비교 결과가 아닙니다/)).toBeTruthy();
  expect(await screen.findByText(/현재 요구·프로필 판본과 연결된 등급/)).toBeTruthy();
  expect(screen.queryByText('합성-TRF-1000')).toBeNull();
  expect(screen.queryByRole('button', { name: '보완 선택 저장' })).toBeNull();
  expect(selectSupplement).not.toHaveBeenCalled();
});
test('read only access disables original selection form without opening mutations', async () => {
  show(false);
  expect((await screen.findByRole('combobox', { name: '보완 경로' })).closest('fieldset')?.disabled).toBe(true);
  expect((screen.getByRole('button', { name: '보완 선택 저장' }) as HTMLButtonElement).disabled).toBe(true);
  expect(selectSupplement).not.toHaveBeenCalled();
});
test('explicit save uses original version and invokes parent refresh', async () => {
  vi.mocked(selectSupplement).mockResolvedValue({ data: { requirement_id: requirement.requirement_id, version: 2 } as never, meta: { request_id: 'test' } });
  const saved = show();
  fireEvent.change(await screen.findByRole('combobox', { name: '보완 경로' }), { target: { value: 'NONE' } });
  fireEvent.change(screen.getByRole('textbox', { name: '역할 분담' }), { target: { value: '자사 확인 담당' } });
  expect(selectSupplement).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(selectSupplement).toHaveBeenCalledWith(requirement.requirement_id, expect.objectContaining({ version: 1, path: 'NONE' }), expect.any(String), 'actor');
  expect(getComparison).toHaveBeenCalledWith(requirement.requirement_id, { useMockFallback: false });
});

test('failed options refresh disables cached selection evidence', async () => {
  show();
  await screen.findByRole('combobox', { name: '보완 경로' });
  vi.mocked(getSupplementOptions).mockRejectedValueOnce(new Error('조회 실패'));
  fireEvent.click(screen.getByRole('button', { name: '대응 근거 최신 조회' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: '보완 선택 저장' })).toBeNull());
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
  fireEvent.click(await screen.findByRole('button', { name: '미확인 조건·대응 경로 확인 →' }));
  await waitFor(() => expect((screen.getByText('대응 방안 검토').closest('details') as HTMLDetailsElement).open).toBe(true));
  expect(screen.queryByText('개발 검토 요청 작성')).toBeNull();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('collapsing a selection keeps the unsaved role split and does not submit it', async () => {
  show();
  const selection = screen.getByText('대응 방안 검토').closest('details') as HTMLDetailsElement;
  fireEvent.click(selection.querySelector('summary')!);
  await screen.findByRole('combobox', { name: '보완 경로' });
  fireEvent.change(screen.getByRole('textbox', { name: '역할 분담' }), { target: { value: '검토 중인 역할' } });
  fireEvent.click(selection.querySelector('summary')!);
  await waitFor(() => expect(selection.open).toBe(false));
  fireEvent.click(selection.querySelector('summary')!);
  expect((screen.getByRole('textbox', { name: '역할 분담' }) as HTMLTextAreaElement).value).toBe('검토 중인 역할');
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('detail-derived rereview stays visible and an AI path is not a completed human selection', async () => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, needs_rereview: true, judgement: { ...MOCK_DETAIL.judgement, path: 'EXTERNAL_PRODUCT' } } as never });
  show();
  await screen.findByText(/판정에 기록된 경로/);
  const current = screen.getByRole('region', { name: '선택 요구 현재 상태' });
  expect(current.textContent).toContain('원문 확인·재검토 필요');
  expect(current.textContent).toContain('AI 제안');
  expect(screen.getByText('판정 경로 있음 · 사람 선택 미확인')).toBeTruthy();
  expect(screen.queryByText('선택 완료')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '원문·판정 재확인 →' }));
  await waitFor(() => expect((screen.getByText('비교 근거와 원문').closest('details') as HTMLDetailsElement).open).toBe(true));
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
  const action = await screen.findByRole('button', { name: '영업·보증 조건 변경 확인 →' });
  const current = screen.getByRole('region', { name: '선택 요구 현재 상태' });
  expect(current.textContent).toContain('36 개월');
  expect(current.textContent).toContain('24 개월');
  expect(current.textContent).toContain('12 개월 차이');
  expect(screen.queryByText('개발 검토 요청 작성')).toBeNull();
  fireEvent.click(action);
  await waitFor(() => expect((screen.getByText('보증·상업 조건 검토').closest('details') as HTMLDetailsElement).open).toBe(true));
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
  const action = await screen.findByRole('button', { name: '담당·수행 범위와 제출 근거 확인 →' });
  fireEvent.click(action);
  await waitFor(() => expect((screen.getByText('수행 가능성·일정 검토').closest('details') as HTMLDetailsElement).open).toBe(true));
  expect(screen.getByRole('button', { name: '수행 가능성 검토 요청 작성' })).toBeTruthy();
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
  await waitFor(() => expect((screen.getByText('비교 근거와 원문').closest('details') as HTMLDetailsElement).open).toBe(true));
  expect((screen.getByText('수행 범위·담당 검토').closest('details') as HTMLDetailsElement).open).toBe(false);
  expect(selectSupplement).not.toHaveBeenCalled();
});
