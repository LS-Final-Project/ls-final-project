import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import ResolutionCaseForm from './ResolutionCaseForm';

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
