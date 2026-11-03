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
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: MOCK_DETAIL as never });
  vi.mocked(getComparison).mockResolvedValue({ data: MOCK_COMPARISON });
  vi.mocked(getSupplementOptions).mockResolvedValue({ data: options, meta: { request_id: 'test' } });
  vi.mocked(listSupplementCandidates).mockResolvedValue({ data: { items: [candidate], manufacturer_list_basis: 'CURRENT_PROJECT_INPUT' }, meta: { request_id: 'test' } });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });
function show(canSelect = true, onSaved = vi.fn()) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><ResponseDecision projectId="project" actorId="actor" requirement={requirement} canSelect={canSelect} onSaved={onSaved} /></MemoryRouter></QueryClientProvider>);
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

test('default view identifies the selected requirement and keeps all review areas collapsed', async () => {
  show();
  const current = await screen.findByRole('region', { name: '선택 요구 현재 상태' });
  await waitFor(() => expect(current.textContent).toContain(MOCK_DETAIL.judgement.reason));
  expect(current.textContent).toContain('사양 차이');
  for (const name of ['자사 비교 근거', '개발 검토', '협력사 후보', '대응 경로 선택', '판정 기준·상세']) {
    const summary = screen.getByText(name).closest('summary');
    expect((summary?.parentElement as HTMLDetailsElement).open).toBe(false);
  }
  expect(screen.getByText('저장 후보 1개')).toBeTruthy();
  expect(screen.getByText('제외되지 않은 후보 1곳')).toBeTruthy();
  expect(screen.getByText('선택 미완료')).toBeTruthy();
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('the next action opens the relevant review without saving or requesting', async () => {
  show();
  await waitFor(() => expect(screen.getByRole('region', { name: '선택 요구 현재 상태' }).textContent).toContain(MOCK_DETAIL.judgement.reason));
  fireEvent.click(screen.getByRole('button', { name: '개발·보완 검토 시작 →' }));
  await waitFor(() => expect((screen.getByText('개발 검토').closest('details') as HTMLDetailsElement).open).toBe(true));
  expect(selectSupplement).not.toHaveBeenCalled();
});

test('collapsing a selection keeps the unsaved role split and does not submit it', async () => {
  show();
  const selection = screen.getByText('대응 경로 선택').closest('details') as HTMLDetailsElement;
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
  await waitFor(() => expect((screen.getByText('판정 기준·상세').closest('details') as HTMLDetailsElement).open).toBe(true));
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
