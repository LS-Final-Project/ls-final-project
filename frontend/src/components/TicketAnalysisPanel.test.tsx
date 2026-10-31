import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import TicketAnalysisPanel from './TicketAnalysisPanel';

afterEach(cleanup);
const field = (value: unknown, corrected = false) => ({ ai_value: value, corrected, corrected_value: corrected ? value : null, effective_value: value, source: corrected ? 'human_input' : 'ai_draft' });
const analysis = (category: string, extra: Record<string, unknown> = {}) => ({
  ticket_id: 't', version: 1, corrections: [], drafts: [], runs: [], latest_valid_draft_id: null, latest_valid_run_id: null,
  latest_run: { created_at: '2026-10-28T00:02:00Z' }, missing_info: { ai: null, effective: null, error: null }, return_subtype_stated: 'DEFECT',
  fields: { category_code: field(category, true), secondary_category: field('HMI'), urgency_flag: field(true), provided_info: field(['photos', 'serial_no']) },
  rules: { effective: { dept: 'TS_AUTO', priority: 'P2', first_response_due: '2026-10-29T00:00:00Z', resolution_due: '2026-11-02T00:00:00Z' }, first_response_due: { effective: '2026-10-29T00:00:00Z' }, resolution_due: { effective: '2026-11-02T00:00:00Z' } },
  ...extra,
}) as never;
const flow = (extra: Record<string, unknown> = {}) => ({ version: 4, return_responsibility: null, ...extra }) as never;
const panel = (props: Partial<Parameters<typeof TicketAnalysisPanel>[0]> = {}) => {
  const onConfirm = vi.fn();
  render(<TicketAnalysisPanel analysis={analysis('RETURN')} pending={false} workflow={flow()} ticketState="IN_REVIEW" canWrite busy={false} onConfirm={onConfirm} {...props} />);
  return onConfirm;
};

test('유효값과 사람 정정 표시, 규칙 결과를 보이고 반품 주장은 확정이 아니라고 적는다', () => {
  panel();
  expect(screen.getByText(/반품 \(RETURN\)/)).toBeTruthy();
  expect(screen.getByText('사람 정정')).toBeTruthy();
  expect(screen.getByText('HMI')).toBeTruthy();
  expect(screen.getByText('photos, serial_no')).toBeTruthy();
  expect(screen.getByText(/자동화 기술지원 \(TS_AUTO\)/)).toBeTruthy();
  expect(screen.getByText('반품 사유(고객 주장)')).toBeTruthy();
  expect(screen.getByText('품질·불량 반품')).toBeTruthy();
  expect(screen.getByText(/접수 때 밝힌 값, 확정 아님/)).toBeTruthy();
});

test('반품이 아닌 문의에는 반품 주장과 확정 입력을 보이지 않는다', () => {
  panel({ analysis: analysis('DEFECT') });
  expect(screen.queryByText('반품 사유(고객 주장)')).toBeNull();
  expect(screen.queryByRole('form', { name: '반품 확정' })).toBeNull();
});

test('확정 칸은 고객 주장으로 미리 채우지 않고 필수값이 차야 보낼 수 있다', () => {
  const onConfirm = panel();
  const button = screen.getByRole('button', { name: '반품 확정' }) as HTMLButtonElement;
  expect((screen.getByLabelText('책임 구분') as HTMLSelectElement).value).toBe('');
  expect((screen.getByLabelText('대응 내용') as HTMLTextAreaElement).value).toBe('');
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('책임 구분'), { target: { value: 'MANUFACTURING' } });
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('대응 내용'), { target: { value: '  교체 발송  ' } });
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  expect(onConfirm).toHaveBeenCalledWith({ responsibility: 'MANUFACTURING', cause_type: null, non_manufacturing_reason: null, response_note: '교체 발송' });
});

test('제조 외 책임일 때만 제조 외 사유를 받고 원인 유형과 함께 보낸다', () => {
  const onConfirm = panel();
  expect(screen.queryByLabelText('제조 외 사유')).toBeNull();
  fireEvent.change(screen.getByLabelText('책임 구분'), { target: { value: 'NON_MANUFACTURING' } });
  fireEvent.change(screen.getByLabelText('원인 유형'), { target: { value: 'SITE_CONDITION' } });
  fireEvent.change(screen.getByLabelText('제조 외 사유'), { target: { value: ' 운송 손상 ' } });
  fireEvent.change(screen.getByLabelText('대응 내용'), { target: { value: '반송 안내' } });
  fireEvent.click(screen.getByRole('button', { name: '반품 확정' }));
  expect(onConfirm).toHaveBeenCalledWith({ responsibility: 'NON_MANUFACTURING', cause_type: 'SITE_CONDITION', non_manufacturing_reason: '운송 손상', response_note: '반송 안내' });
  // 책임 구분을 바꾸면 제조 외 사유는 보내지 않는다.
  fireEvent.change(screen.getByLabelText('책임 구분'), { target: { value: 'MANUFACTURING' } });
  expect(screen.queryByLabelText('제조 외 사유')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '반품 확정' }));
  expect(onConfirm).toHaveBeenLastCalledWith({ responsibility: 'MANUFACTURING', cause_type: 'SITE_CONDITION', non_manufacturing_reason: null, response_note: '반송 안내' });
});

test.each([
  ['개발 모드가 아님', { canWrite: false }, /실제 API 개발 모드/],
  ['판본을 아직 못 읽음', { workflow: undefined }, /업무 판본을 읽는 중/],
  ['접수 상태', { ticketState: 'RECEIVED' }, /검토중·답변 완료 상태에서만/],
  ['종결 상태', { ticketState: 'CLOSED' }, /검토중·답변 완료 상태에서만/],
])('%s에서는 확정 단추가 막히고 이유를 적는다', (_name, props, reason) => {
  panel(props as never);
  fireEvent.change(screen.getByLabelText('책임 구분'), { target: { value: 'MANUFACTURING' } });
  fireEvent.change(screen.getByLabelText('대응 내용'), { target: { value: '내용' } });
  expect((screen.getByRole('button', { name: '반품 확정' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getAllByText(reason).length).toBeGreaterThan(0);
});

test('확정 결과를 보이고 정하지 못함은 불량 여부를 정한 것처럼 적지 않는다', () => {
  panel({ workflow: flow({ return_responsibility: 'UNDETERMINED', return_cause_type: 'HARDWARE', return_response_note: '재검사', return_subtype_confirmed_by: 'u1', return_subtype_confirmed_at: '2026-10-30T01:00:00Z' }) });
  const status = screen.getByRole('status').textContent ?? '';
  expect(status).toContain('확정: 검사로 정하지 못함');
  expect(status).toContain('원인 유형 하드웨어');
  expect(status).toContain('불량 여부는 정해지지 않음');
  expect(status).toContain('다시 확정하면 모든 칸을 덮어씁니다');
  expect(screen.getByRole('button', { name: '다시 확정' })).toBeTruthy();
});

test('분석이 없으면 이전처럼 자리만 두고 비활성으로 안내한다', () => {
  const { container } = render(<TicketAnalysisPanel analysis={undefined} pending={false} canWrite busy={false} onConfirm={() => undefined} />);
  expect(screen.getByText(/분석 조회는 연결 전입니다/)).toBeTruthy();
  expect((screen.getByLabelText('주분류') as HTMLSelectElement).disabled).toBe(true);
  expect(container.querySelector('form')).toBeNull();
});
