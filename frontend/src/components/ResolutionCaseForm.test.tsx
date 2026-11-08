import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import ResolutionCaseForm, { type FaultEntry } from './ResolutionCaseForm';
import { ApiError } from '../api/client';
import type { FaultSuggestion } from '../api/cs-workflow';

afterEach(cleanup);
const flow = { dept_results: [
  { id: 'r1', dept_code: 'TS_AUTO', review_status: 'ACCEPTED', submitted_at: '2026-10-28T01:00:00Z' },
  { id: 'r2', dept_code: 'TS_LV', review_status: 'SUBMITTED', submitted_at: '2026-10-28T02:00:00Z' },
] } as never;
const form = (onSubmit = vi.fn(), props: Record<string, unknown> = {}) => {
  render(<ResolutionCaseForm workflow={flow} busy={false} onSubmit={onSubmit} onCancel={() => undefined} {...props} />);
  return onSubmit;
};
const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const fillRequired = () => {
  fill('고장 코드', 'FLT-012'); fill('증상', ' 터치 무반응 '); fill('원인 유형', 'FIRMWARE'); fill('원인', 'FW 2.31 정전기'); fill('조치', 'FW 2.33 적용'); fill('해결 부서', 'TS_AUTO'); fill('현장 방문', 'no');
};
const save = () => screen.getByRole('button', { name: '해결 기록 저장' }) as HTMLButtonElement;

test('필수 칸이 모두 차고 고장 코드가 FLT-000 형식이어야 저장할 수 있다', () => {
  form();
  expect(save().disabled).toBe(true);
  fill('고장 코드', 'F12'); expect(screen.getByText(/FLT-000 형식이어야 합니다/)).toBeTruthy();
  fillRequired(); expect(save().disabled).toBe(false);
  fill('고장 코드', 'flt-12'); expect(save().disabled).toBe(true);
  fill('고장 코드', 'FLT-012'); fill('현장 방문', ''); expect(save().disabled).toBe(true);
});

test('저장 본문은 공백을 다듬고 빈 선택 칸은 null, 현장 방문은 불리언으로 보낸다', () => {
  const onSubmit = form();
  fillRequired(); fill('RMA 번호', '  '); fill('재발 방지', ' V2.31 출하분 안내 ');
  fireEvent.click(save());
  expect(onSubmit).toHaveBeenCalledWith({
    fault_id: 'FLT-012', symptom: '터치 무반응', root_cause_type: 'FIRMWARE', root_cause: 'FW 2.31 정전기', action_taken: 'FW 2.33 적용', resolved_by_dept: 'TS_AUTO', onsite_visit: false,
    recurrence_prevention: 'V2.31 출하분 안내', rma_no: null, fw_version_at_issue: null, fw_fixed_version: null, dept_result_id: null, responsibility: null, non_manufacturing_reason: null,
  });
});

test('연결 답변은 확인 완료된 부서 답변만 고르게 한다', () => {
  const onSubmit = form();
  const options = [...(screen.getByLabelText('연결 답변') as HTMLSelectElement).options].map(option => option.textContent);
  expect(options).toHaveLength(2);
  expect(options[1]).toContain('자동화 기술지원');
  expect(options.join(' ')).not.toContain('저압기기');
  fillRequired(); fill('연결 답변', 'r1'); fill('현장 방문', 'yes');
  fireEvent.click(save());
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ dept_result_id: 'r1', onsite_visit: true }));
});

test('제조 외 사유는 제조 외 책임일 때만 받고 보낸다(책임 구분은 반품 확정에서 복사하지 않는다)', () => {
  const onSubmit = form();
  fillRequired();
  expect(screen.queryByLabelText('제조 외 사유')).toBeNull();
  fill('책임 구분', 'NON_MANUFACTURING'); fill('제조 외 사유', ' 운송 손상 ');
  fireEvent.click(save());
  expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({ responsibility: 'NON_MANUFACTURING', non_manufacturing_reason: '운송 손상' }));
  fill('책임 구분', 'MANUFACTURING');
  expect(screen.queryByLabelText('제조 외 사유')).toBeNull();
  fireEvent.click(save());
  expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({ responsibility: 'MANUFACTURING', non_manufacturing_reason: null }));
});

test('실행 중에는 저장하지 못한다', () => {
  form(vi.fn(), { busy: true }); fillRequired();
  expect(save().disabled).toBe(true);
});

test('고장 목록이 있어도 목록에 없는 새 고장 코드는 직접 적을 수 있다', () => {
  const items = [{ fault_id: 'FLT-020', error_code: 'P.01', symptom: 'CPU stops', root_cause: 'loop', diagnosis: 'd', action: 'a', needs_onsite: false, needs_rma: false, fw_fix_version: null, replacement_part: null }];
  form(vi.fn(), { faults: { items } });
  const select = screen.getByLabelText('고장 코드') as HTMLSelectElement;
  expect(select.tagName).toBe('SELECT');
  fireEvent.change(select, { target: { value: '__manual__' } });
  const input = screen.getByLabelText('고장 코드') as HTMLInputElement;
  expect(input.tagName).toBe('INPUT');
  fireEvent.change(input, { target: { value: 'FLT-990' } });
  expect(input.value).toBe('FLT-990');
  fireEvent.click(screen.getByRole('button', { name: '목록에서 고르기' }));
  expect((screen.getByLabelText('고장 코드') as HTMLSelectElement).tagName).toBe('SELECT');
});

// 같은 고장 AI 추천(KAN-245)
const entry = (fault_id: string, symptom: string): FaultEntry => ({ fault_id, symptom, error_code: null, root_cause: 'r', diagnosis: 'd', action: 'a', needs_onsite: false, needs_rma: false, fw_fix_version: null, replacement_part: null });
const faults = { items: [entry('FLT-065', 'Buchholz alarm'), entry('FLT-067', 'Oil leakage at gasket')] };
const advice = (extra: Partial<FaultSuggestion>): { data: FaultSuggestion } => ({ data: { ticket_id: 't', decision: 'EXISTING', fault_id: 'FLT-067', fault_symptom: 'Oil leakage at gasket', matched_cases: 13, evidence_quote: 'oil is leaking', reason: '같은 누유 증상', compared_faults: 2, compared_cases: 15, prompt_version: 'fault-match-v2', model: 'gemini-3.6-flash', provenance: 'ai_draft', ...extra } });
const aiForm = (suggest?: () => Promise<{ data: FaultSuggestion }>) => render(<ResolutionCaseForm busy={false} onSubmit={() => {}} onCancel={() => {}} faults={faults} suggest={suggest} />);

test('AI 추천을 받아 담당자가 눌러야 고장 코드가 골라진다', async () => {
  const suggest = vi.fn(() => Promise.resolve(advice({})));
  aiForm(suggest);
  fireEvent.click(screen.getByRole('button', { name: /AI로 같은 고장 찾기/ }));
  expect(await screen.findByText('FLT-067')).toBeTruthy();
  expect(screen.getByText(/oil is leaking/)).toBeTruthy();
  // 추천만으로는 고르지 않는다.
  expect((screen.getByLabelText('고장 코드') as HTMLSelectElement).value).toBe('');
  fireEvent.click(screen.getByRole('button', { name: '이 고장으로 고르기' }));
  expect((screen.getByLabelText('고장 코드') as HTMLSelectElement).value).toBe('FLT-067');
  expect(suggest).toHaveBeenCalledTimes(1);
});

test('판단 보류와 꺼진 배포는 직접 고르라고 안내한다', async () => {
  aiForm(() => Promise.resolve(advice({ decision: 'UNSURE', fault_id: null, fault_symptom: null, matched_cases: 0 })));
  fireEvent.click(screen.getByRole('button', { name: /AI로 같은 고장 찾기/ }));
  expect(await screen.findByText('판단 보류')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '이 고장으로 고르기' })).toBeNull();
  cleanup();
  aiForm(() => Promise.reject(new ApiError(404, 'not found')));
  fireEvent.click(screen.getByRole('button', { name: /AI로 같은 고장 찾기/ }));
  expect(await screen.findByText(/이 배포에서 꺼져 있습니다/)).toBeTruthy();
  cleanup();
  aiForm(() => Promise.reject(new ApiError(403, 'forbidden')));
  fireEvent.click(screen.getByRole('button', { name: /AI로 같은 고장 찾기/ }));
  expect(await screen.findByText(/C\/S 담당자만/)).toBeTruthy();
});

test('추천 기능을 넘기지 않으면 단추가 없다', () => {
  aiForm();
  expect(screen.queryByRole('button', { name: /AI로 같은 고장 찾기/ })).toBeNull();
});
