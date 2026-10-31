// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import AnalysisCorrection from './AnalysisCorrection';
import { ApiError } from '../api/client';
import type { TicketAnalysisResponse } from '../api/cs';

afterEach(cleanup);
type Analysis = TicketAnalysisResponse['data'];
const analysis = (over: Record<string, unknown> = {}) => ({ version: 2, latest_valid_run_id: 'run-1', fields: { category_code: { effective_value: 'DEFECT' }, secondary_category: { effective_value: null }, urgency_flag: { effective_value: 'NONE' } }, ...over }) as unknown as Analysis;
const ok = () => vi.fn<(body: unknown) => Promise<void>>(async () => undefined);
const setup = (onSubmit = ok(), over: Record<string, unknown> = {}) => { const onDone = vi.fn(); render(<AnalysisCorrection analysis={analysis(over)} onSubmit={onSubmit} onDone={onDone} />); return { onSubmit, onDone }; };
const fill = (reason = '고객 원문이 보증 문의임') => fireEvent.change(screen.getByLabelText('정정 사유'), { target: { value: reason } });
const save = () => fireEvent.click(screen.getByRole('button', { name: '정정 저장' }));

test('바꾼 칸만 읽은 판본·기준 분석 실행·사유와 함께 보내고 끝나면 닫는다', async () => {
  const { onSubmit, onDone } = setup();
  expect((screen.getByRole('button', { name: '정정 저장' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText('바꾼 칸이 없습니다.')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('주분류'), { target: { value: 'WARRANTY' } });
  fireEvent.change(screen.getByLabelText('긴급 여부'), { target: { value: 'SITE_DOWN' } });
  expect((screen.getByRole('button', { name: '정정 저장' }) as HTMLButtonElement).disabled).toBe(true);
  fill('  고객 원문이 보증 문의임  ');
  save();
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(onSubmit).toHaveBeenCalledWith({ version: 2, analysis_id: 'run-1', reason: '고객 원문이 보증 문의임', category_code: 'WARRANTY', urgency_flag: 'SITE_DOWN' });
});

test('보조분류는 고른 경우에만 보내고, 분석 실행이 없으면 analysis_id를 null로 보낸다', async () => {
  const { onSubmit } = setup(undefined, { latest_valid_run_id: null });
  fireEvent.change(screen.getByLabelText('보조분류'), { target: { value: 'INSTALL' } });
  fill();
  save();
  await waitFor(() => expect(onSubmit).toHaveBeenCalled());
  expect(onSubmit).toHaveBeenCalledWith({ version: 2, analysis_id: null, reason: '고객 원문이 보증 문의임', secondary_category: 'INSTALL' });
});

test('충돌(409)과 서버 거부는 이유를 보이고 닫지 않으며, 알 수 없는 오류는 확인하라고 안내한다', async () => {
  const onSubmit = ok().mockRejectedValueOnce(new ApiError(409, '충돌', undefined, 'CS_ANALYSIS_VERSION_CONFLICT'));
  const { onDone } = setup(onSubmit);
  fireEvent.change(screen.getByLabelText('주분류'), { target: { value: 'WARRANTY' } });
  fill();
  save();
  expect((await screen.findByRole('alert')).textContent).toContain('다른 사용자가 먼저 정정했습니다');
  onSubmit.mockRejectedValueOnce(new ApiError(422, '지원하지 않는 값입니다.'));
  save();
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('지원하지 않는 값입니다.'));
  onSubmit.mockRejectedValueOnce(new ApiError(503, '장애'));
  save();
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('같은 요청을 다시 보내지 않고'));
  expect(onDone).not.toHaveBeenCalled();
  expect(onSubmit).toHaveBeenCalledTimes(3);
});
