import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import Evaluations from './Evaluations';
import { watchEvaluation, type EvaluationView } from '../api/evaluation-events';
import { lockedQualityFixture, summaryFixture, syntheticQualityFixture } from '../test/evaluation-summary';
import { jsonResponse, renderAt } from '../test/render';

vi.mock('../api/evaluation-events', () => ({ watchEvaluation: vi.fn() }));
const jobId = '11111111-1111-4111-8111-111111111111';
let emit: (view: EvaluationView) => void;
const state = (checkedAt = 1): EvaluationView => ({ connection: 'ended', checkedAt,
  data: { job_id: jobId, run_id: summaryFixture().run_id, owner: 'platform', kind: 'evaluation.run',
    status: 'PARTIAL', run_status: 'PARTIAL', needs_review: false, version: 3, last_event_sequence: 4 } });
beforeEach(() => {
  vi.mocked(watchEvaluation).mockImplementation(async (_id, _signal, update) => { emit = update; update(state()); });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
/** 조회 화면을 연다. Args: 없음. Returns: 화면 렌더 핸들. */
function view() { return renderAt(`/evaluations?job=${jobId}`, '/evaluations', <Evaluations />); }

test('합성 표시와 최초·재시도·전체 표에 미보고·부분합·실제0을 구분한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: summaryFixture() })));
  view();
  await screen.findByRole('table');
  expect(screen.getByRole('columnheader', { name: '최초 시도' })).toBeTruthy();
  expect(screen.getAllByText('부분합 6 · 1건 미보고').length).toBe(2);
  expect(screen.getAllByText('판정 보류').length).toBe(1);
  expect(screen.getAllByText('50%').length).toBe(2);
  expect(screen.getByText(/실제 모델 품질이나 사용량 측정 결과가 아닙니다/)).toBeTruthy();
  expect(screen.getByText(/품질 점수: 정답 검수·연결 전/)).toBeTruthy();
});

/** 잠금 경로 요약을 만든다. Args: 응답 변경. Returns: 검증기를 통과하는 독립 응답. */
function lockedSummary(change: (data: ReturnType<typeof summaryFixture>, quality: ReturnType<typeof lockedQualityFixture>) => void = () => {}) {
  const data = summaryFixture();
  const quality = lockedQualityFixture();
  Object.assign(data, { data_origin: 'real', quality_available: true, quality_unavailable_reason: null, comparison_key: null, locked_quality: quality });
  change(data, quality);
  return data;
}

test('잠금 경로는 다섯 지표의 범위별 저장값을 반올림 없이 표시하고 합성 문구·합성 품질 절을 숨긴다', async () => {
  const data = lockedSummary((_, q) => { q.metrics.category!.segments.all.values.macro_f1.value = 0.5714285714285714; });
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data })));
  view();
  const category = await screen.findByRole('region', { name: '대분류 전체 잠금 품질 표' });
  expect(screen.getByText('평가 실행 저장 기록입니다 · 데이터 출처 실제 · 품질은 잠금 정답 저장값이며 합격 판정이 아닙니다.')).toBeTruthy();
  expect(screen.queryByText(/합성 연결 검사 기록/)).toBeNull();
  expect(screen.queryByText(/합성 품질/)).toBeNull();
  expect(screen.getByRole('note').textContent).toBe('quality_gate_evaluated=false · 합격 판정 아님');
  expect(screen.getByText('정답 묶음 cs-gold-bundle-test · 다섯 지표 연결')).toBeTruthy();
  for (const label of ['대분류', '긴급도', '보조 분류', '반품 하위유형', '제공 정보']) expect(screen.getByRole('heading', { name: label })).toBeTruthy();
  expect([...category.querySelectorAll('tbody tr')].map(row => row.textContent)).toEqual(['accuracy13 / 33', 'macro_f10.5714285714285714해당 없음3']);
  const partial = screen.getByRole('region', { name: '제공 정보 채점 범위 PRECISION_ONLY (precision만) 잠금 품질 표' });
  expect([...partial.querySelectorAll('tbody tr')].map(row => row.textContent)).toEqual(['precision계산 불가0 / 01']);
  expect(screen.getByText(/결과 분포: HELD 0 · NOT_STARTED 0 · SCHEMA_VIOLATION 1 · TIMEOUT 0 · TRANSPORT_FAILURE 0 · VALID 1 · 제외: PRECISION_ONLY 1 · 항목 집계: tp 1 · fp 1 · fn 1/)).toBeTruthy();
  expect(screen.getByText(/제외: NOT_RETURN 1/)).toBeTruthy();
  expect(screen.getByRole('region', { name: '반품 하위유형 전체 클래스별 잠금 품질 표' }).querySelectorAll('tbody tr').length).toBe(2);
  expect(screen.getByText('품질 점수: 잠금 정답 다섯 지표 연결')).toBeTruthy();
  expect(screen.queryByText(/양호|우수|합격$|불합격|정답 검수·연결 전/)).toBeNull();
});

test('잠금 경로의 일부 저장·묶음 미연결은 사유대로 표시하고 미저장 지표를 0으로 바꾸지 않는다', async () => {
  const partial = lockedSummary((data, q) => { q.metrics.urgency = null; Object.assign(data, { data_origin: 'synthetic', quality_available: false, quality_unavailable_reason: 'GOLD_PARTIAL' }); });
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: partial })));
  const { unmount } = view();
  await screen.findByRole('region', { name: '대분류 전체 잠금 품질 표' });
  expect(screen.getByText(/데이터 출처 합성 · 품질은 잠금 정답 저장값/)).toBeTruthy();
  expect(screen.getByText('정답 묶음 cs-gold-bundle-test · 일부 지표만 저장 (4/5)')).toBeTruthy();
  expect(screen.getByRole('heading', { name: '긴급도' }).nextElementSibling?.textContent).toBe('미저장');
  expect(screen.getByText('품질 점수: 잠금 정답 일부 지표만 연결')).toBeTruthy();
  unmount();
  const none = lockedSummary(data => { Object.assign(data, { quality_available: false, quality_unavailable_reason: 'GOLD_NOT_CONNECTED', locked_quality: null }); });
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: none })));
  view();
  expect(await screen.findByText('정답 묶음 미연결')).toBeTruthy();
  expect(screen.queryByText(/합성 연결 검사 기록|합성 품질/)).toBeNull();
  expect(screen.getByText(/품질 점수: 정답 검수·연결 전/)).toBeTruthy();
});

test('재조회 실패 후 이전 집계를 숨기며 서버 원문을 표시하지 않는다', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(jsonResponse(200, { data: summaryFixture() }))
    .mockResolvedValue(jsonResponse(403, { error: { message: 'secret' } }));
  vi.stubGlobal('fetch', fetcher); view(); await screen.findByRole('table');
  fireEvent.click(screen.getByRole('button', { name: '새로고침' }));
  await screen.findByText(/평가자 권한 또는 현재 구성 승인/);
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.queryByText(/secret/)).toBeNull();
});

test('상태 권한 회수와 탭 숨김에서 집계를 제거하고 늦은 응답을 무시한다', async () => {
  let resolve!: (value: Response) => void;
  const fetcher = vi.fn((_url, init: RequestInit) => new Promise<Response>(done => { resolve = done; expect(init.signal).toBeTruthy(); }));
  vi.stubGlobal('fetch', fetcher); view();
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  const signal = fetcher.mock.calls[0][1].signal!;
  act(() => emit({ connection: 'error', message: '로그인 재확인' }));
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(jsonResponse(200, { data: summaryFixture() })));
  expect(screen.queryByRole('table')).toBeNull();
  act(() => emit(state(2)));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  Object.defineProperty(document, 'hidden', { configurable: true, value: true });
  fireEvent(document, new Event('visibilitychange'));
  expect(fetcher.mock.calls[1][1].signal!.aborted).toBe(true);
  expect(screen.queryByRole('heading', { name: '실행 집계' })).toBeNull();
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
});

test('SSE 연결 상태만 바뀌면 중복 조회하지 않고 새 상태 시점에서만 갱신한다', async () => {
  const fetcher = vi.fn(async () => jsonResponse(200, { data: summaryFixture() }));
  vi.stubGlobal('fetch', fetcher); view(); await screen.findByRole('table');
  act(() => emit({ ...state(), connection: 'connected' }));
  expect(fetcher).toHaveBeenCalledTimes(1);
  act(() => emit({ connection: 'reconnecting', message: '다시 연결' }));
  expect(screen.queryByRole('table')).toBeNull();
  act(() => emit(state(2)));
  await screen.findByRole('table');
  expect(fetcher).toHaveBeenCalledTimes(2);
});

test('USD 소액·실제0·부분합·혼합 판본을 반올림 없이 표시한다', async () => {
  const data = summaryFixture();
  data.first.cost_usd = { reported_count: 2, missing_count: 1, known_sum: '0.000001', total: null, pricing_version_count: 2 };
  data.all = { ...data.all, cost_usd: { reported_count: 3, missing_count: 0, known_sum: '0.000000', total: '0.000000', pricing_version_count: 1 } };
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data })));
  view(); await screen.findByRole('table');
  expect(screen.getByText('부분합 0.000001 USD · 1건 미보고')).toBeTruthy();
  expect(screen.getByText('0.000000 USD')).toBeTruthy();
  expect(screen.getByText('2종 (혼합)')).toBeTruthy();
  expect(screen.getByText(/청구액이나 무료 사용 여부를 뜻하지 않습니다/)).toBeTruthy();
});

test('구버전 집계의 비용 누락은0 대신 미연결로 표시한다', async () => {
  const data = summaryFixture();
  delete data.first.cost_usd; delete data.retry.cost_usd;
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data })));
  view(); await screen.findByRole('table');
  expect(screen.getAllByText('비용 집계 미연결').length).toBe(3);
});

test('합성 품질 미저장은 한 줄로 표시하고 고정 안내를 유지한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: summaryFixture() })));
  view(); await screen.findByRole('table');
  expect(screen.getByRole('heading', { name: /합성 품질/ })).toBeTruthy();
  expect(screen.getByText('합성 품질 미저장')).toBeTruthy();
  expect(screen.getByRole('note').textContent).toBe('합성 정답 기준, 실제 품질·합격 판정 아님');
  expect(screen.getAllByRole('table').length).toBe(1);
});

test('저장된 합성 품질의 판본·대상·수치·클래스 표를 숫자와 문구로만 표시한다', async () => {
  const data = summaryFixture();
  data.synthetic_quality = syntheticQualityFixture();
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data })));
  view();
  const table = await screen.findByRole('region', { name: '클래스별 합성 품질 표' });
  expect(screen.getByText('채점 판본 syn-v2 (저장 판본 2개 중 최신) · 정답 판본 미지정')).toBeTruthy();
  expect(screen.getByText('채점 대상 2건 / 전체 대상 3건 · accuracy 0.5 · macro-F1 0.5 · null 예측 1건')).toBeTruthy();
  expect(screen.getByRole('note').textContent).toBe('합성 정답 기준, 실제 품질·합격 판정 아님');
  const rows = table.querySelectorAll('tbody tr');
  expect([...rows].map(row => row.textContent)).toEqual(['DEFECT1111', 'NON_DEFECT1000']);
  expect(screen.queryByText('합성 품질 미저장')).toBeNull();
  expect(screen.queryByText(/양호|우수|합격$|불합격/)).toBeNull();
});

test('잘못된 합성 품질 값이면 집계 전체를 숨기고 안전한 오류만 표시한다', async () => {
  const data = summaryFixture();
  data.synthetic_quality = { ...syntheticQualityFixture(), accuracy: 2 };
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data })));
  view();
  await screen.findByText(/집계 응답을 확인할 수 없습니다/);
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.queryByText(/합성 품질/)).toBeNull();
});
