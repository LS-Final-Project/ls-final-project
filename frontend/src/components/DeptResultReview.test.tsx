import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import DeptResultReview from './DeptResultReview';

afterEach(cleanup);
const result = { id: 'r1', dept_code: 'TS_AUTO', result: '터치 컨트롤러 FW 2.33 적용 후 재현 없음', reply_content: 'Please update the firmware.', submitted_at: '2026-10-28T05:05:00Z', review_status: 'SUBMITTED', version: 3 } as never;
const view = (props: Record<string, unknown> = {}) => {
  const onSubmit = vi.fn();
  render(<DeptResultReview result={result} round={1} busy={false} onSubmit={onSubmit} {...props} />);
  return onSubmit;
};
const save = () => screen.getByRole('button', { name: '검토 저장' }) as HTMLButtonElement;

test('답변과 고객 회신 문안, 부서·차수를 보이고 기본은 확인 완료다', () => {
  const onSubmit = view();
  expect(screen.getByText(/검토 답변 확인, 자동화 기술지원 \(TS_AUTO\) 1차/)).toBeTruthy();
  expect(screen.getByText('터치 컨트롤러 FW 2.33 적용 후 재현 없음')).toBeTruthy();
  expect(screen.getByText('Please update the firmware.')).toBeTruthy();
  expect((screen.getByLabelText('확인 완료') as HTMLInputElement).checked).toBe(true);
  fireEvent.click(save());
  expect(onSubmit).toHaveBeenCalledWith({ decision: 'ACCEPT', note: null, result_version: 3 });
});

test('다시 요청은 메모가 있어야 저장되고 결과 판본과 함께 보낸다', () => {
  const onSubmit = view();
  fireEvent.click(screen.getByLabelText('다시 요청'));
  expect(save().disabled).toBe(true);
  expect(screen.getByText(/메모에 보완할 내용을 적어야 합니다/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('메모'), { target: { value: '  접지 점검 결과 추가  ' } });
  expect(save().disabled).toBe(false);
  fireEvent.click(save());
  expect(onSubmit).toHaveBeenCalledWith({ decision: 'REWORK', note: '접지 점검 결과 추가', result_version: 3 });
});

test('막힌 사유가 있거나 실행 중이면 저장하지 못한다', () => {
  const blockedSubmit = view({ blocked: '실제 API 개발 모드에서만 실행할 수 있습니다' });
  expect(save().disabled).toBe(true);
  expect(screen.getByText(/실제 API 개발 모드에서만/)).toBeTruthy();
  fireEvent.submit(save().closest('form')!);
  expect(blockedSubmit).not.toHaveBeenCalled();
  cleanup();
  view({ busy: true }); expect(save().disabled).toBe(true);
});
