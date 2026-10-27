import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import RunComparison from './RunComparison';
import { summaryFixture, syntheticQualityFixture } from '../test/evaluation-summary';
import { jsonResponse } from '../test/render';
import type { RunSummary } from '../api/evaluation-summary';

const baselineId = '33333333-3333-4333-8333-333333333333';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

/** 기준·후보 요약을 만든다. Args: 후보 변경. Returns: 두 합성 응답. */
function pair(change: (candidate: RunSummary) => void = () => {}) {
  const baseline = { ...summaryFixture(), run_id: baselineId, synthetic_quality: syntheticQualityFixture() };
  const candidate = { ...summaryFixture(), synthetic_quality: { ...syntheticQualityFixture(), accuracy: 0.25 } };
  candidate.first = { ...candidate.first, latency_ms: { reported_count: 3, missing_count: 0, p50: 7, p95: 6 },
    tokens: { ...candidate.first.tokens, input_tokens: { reported_count: 3, missing_count: 0, known_sum: 25, total: 25 } } };
  change(candidate);
  return { baseline, candidate };
}

/** 두 요약을 응답하는 fetch로 비교를 실행한다. Args: 기준·후보 응답, 입력할 기준 ID. Returns: fetch mock. */
function compare({ baseline, candidate }: ReturnType<typeof pair>, input = baselineId) {
  const fetcher = vi.fn(async (url: string) => jsonResponse(200, { data: url.includes(baselineId) ? baseline : candidate }));
  vi.stubGlobal('fetch', fetcher);
  render(<RunComparison candidateId={candidate.run_id} />);
  fireEvent.change(screen.getByLabelText(/기준 실행 ID/), { target: { value: input } });
  fireEvent.click(screen.getByRole('button', { name: '비교' }));
  return fetcher;
}

/** 표의 항목별 셀을 읽는다. Args: 없음. Returns: 항목 → [기준, 후보, 차이]. */
function cells() {
  const table = screen.getByRole('region', { name: '실행 비교 표' });
  return Object.fromEntries([...table.querySelectorAll('tbody tr')].map(row =>
    [row.querySelector('th')!.textContent, [...row.querySelectorAll('td')].map(cell => cell.textContent)]));
}

test('같은 조건의 종료 실행은 요약 두 번으로 비교하고 조건을 충족한 값만 부호 있는 차이를 낸다', async () => {
  const fetcher = compare(pair());
  await screen.findByRole('region', { name: '실행 비교 표' });
  expect(fetcher).toHaveBeenCalledTimes(2);
  const rows = cells();
  expect(rows['입력 토큰']).toEqual(['21', '25', '+4']);
  expect(rows['총 토큰']).toEqual(['부분합 20 · 1건 미보고', '부분합 20 · 1건 미보고', '계산 안 함']);
  expect(rows['지연 p50 (ms)']).toEqual(['5 (표본 3)', '7 (표본 3)', '+2']);
  expect(rows['지연 p95 (ms)'][2]).toBe('-2');
  expect(rows['비용 (USD)']).toEqual(['미보고', '미보고', '계산 안 함']);
  expect(rows['합성 품질 accuracy']).toEqual(['0.5', '0.25', '-0.25']);
  expect(rows['합성 품질 macro-F1'][2]).toBe('0');
  expect(screen.getByText(/가격표 판본이 같은지 확인할 수 없어/)).toBeTruthy();
  expect(screen.queryByText(/개선|퇴행|양호|우수|합격입니다/)).toBeNull();
});

test('비교 키가 다르면 표 없이 이유만 표시한다', async () => {
  compare(pair(candidate => { candidate.comparison_key = 'd'.repeat(64); }));
  expect((await screen.findByRole('alert')).textContent).toMatch(/평가 조건.*달라 비교하지 않습니다/);
  expect(screen.queryByRole('table')).toBeNull();
});

test('한쪽이 미종료면 비교하지 않는다', async () => {
  compare(pair(candidate => { candidate.status = 'RUNNING'; }));
  expect((await screen.findByRole('alert')).textContent).toMatch(/모두 종료된 뒤/);
  expect(screen.queryByRole('table')).toBeNull();
});

test('정답 판본이 다르면 품질 차이만 계산하지 않고 이유를 적는다', async () => {
  compare(pair(candidate => { candidate.synthetic_quality = { ...syntheticQualityFixture(), gold_sha256: 'b'.repeat(64) }; }));
  await screen.findByRole('region', { name: '실행 비교 표' });
  const rows = cells();
  expect(rows['합성 품질 accuracy']).toEqual(['0.5', '0.5', '계산 안 함']);
  expect(rows['입력 토큰'][2]).toBe('+4');
  expect(screen.getByText('정답 판본 또는 채점 대상이 달라 품질 차이를 계산하지 않습니다.')).toBeTruthy();
});

test('한쪽 합성 품질이 없으면 미저장으로 두고 차이를 계산하지 않는다', async () => {
  compare(pair(candidate => { candidate.synthetic_quality = null; }));
  await screen.findByRole('region', { name: '실행 비교 표' });
  expect(cells()['합성 품질 accuracy']).toEqual(['0.5', '미저장', '계산 안 함']);
});

test('같은 실행·구버전 키 누락·조회 실패는 표를 만들지 않는다', async () => {
  const same = pair();
  compare(same, same.candidate.run_id);
  expect((await screen.findByRole('alert')).textContent).toMatch(/같은 실행끼리는/);
  cleanup(); vi.unstubAllGlobals();
  compare(pair(candidate => { candidate.comparison_key = null; }));
  expect((await screen.findByRole('alert')).textContent).toMatch(/비교 키가 없는 응답/);
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(403, { error: { message: 'secret' } })));
  render(<RunComparison candidateId={summaryFixture().run_id} />);
  fireEvent.change(screen.getByLabelText(/기준 실행 ID/), { target: { value: baselineId } });
  fireEvent.click(screen.getByRole('button', { name: '비교' }));
  expect((await screen.findByRole('alert')).textContent).toMatch(/평가자 권한/);
  expect(screen.queryByText(/secret/)).toBeNull();
  expect(screen.queryByRole('table')).toBeNull();
});

test('잘못된 실행 ID는 비교를 시작하지 않는다', () => {
  vi.stubGlobal('fetch', vi.fn());
  render(<RunComparison candidateId={summaryFixture().run_id} />);
  fireEvent.change(screen.getByLabelText(/기준 실행 ID/), { target: { value: 'not-a-run' } });
  expect((screen.getByRole('button', { name: '비교' }) as HTMLButtonElement).disabled).toBe(true);
});
