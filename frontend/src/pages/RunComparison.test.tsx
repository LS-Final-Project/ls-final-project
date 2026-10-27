import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import RunComparison, { comparisonCsv, comparisonFileName, csvCell } from './RunComparison';
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
  expect(screen.queryByRole('button', { name: 'CSV 저장' })).toBeNull();
});

test('한쪽이 미종료면 비교하지 않는다', async () => {
  compare(pair(candidate => { candidate.status = 'RUNNING'; }));
  expect((await screen.findByRole('alert')).textContent).toMatch(/모두 종료된 뒤/);
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.queryByRole('button', { name: 'CSV 저장' })).toBeNull();
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

test('비교 가능할 때만 CSV 저장 버튼을 보인다', async () => {
  compare(pair());
  await screen.findByRole('region', { name: '실행 비교 표' });
  expect(screen.getByRole('button', { name: 'CSV 저장' })).toBeTruthy();
});

test('CSV는 화면 표의 집계만 BOM·CRLF로 담고 사례·정답·예측·원시 출력은 넣지 않는다', () => {
  const { baseline, candidate } = pair();
  const csv = comparisonCsv(baseline, candidate);
  expect(csv.startsWith('\uFEFF')).toBe(true);
  expect(csv.endsWith('\r\n')).toBe(true);
  expect(csv.replace(/\r\n/g, '').includes('\n')).toBe(false);
  const lines = csv.slice(1).split('\r\n');
  expect(lines.slice(0, 2)).toEqual([`기준 run_id,${baselineId}`, `후보 run_id,${candidate.run_id}`]);
  expect(lines).toContain('항목,기준,후보,차이(후보−기준)');
  expect(lines).toContain('입력 토큰,21,25,+4');
  expect(lines).toContain('합성 품질 accuracy,0.5,0.25,-0.25');
  expect(lines).toContain('비용 (USD),미보고,미보고,계산 안 함');
  expect(lines.at(-2)).toBe('안내,"합성 정답 기준, 실제 품질·합격 판정 아님. 최초 시도 기준이며 비용 차이는 계산하지 않음."');
  for (const forbidden of ['cs-smoke', 'case', 'prediction', 'raw_output', 'gold', 'a'.repeat(64), 'DEFECT']) {
    expect(csv).not.toContain(forbidden);
  }
});

test('수식으로 해석될 셀은 작은따옴표를 붙이고 순수·부호 숫자는 그대로 둔다', () => {
  expect(['=1+1', '+cmd', '-x', '@SUM(A1)', '\tx'].map(csvCell)).toEqual(["'=1+1", "'+cmd", "'-x", "'@SUM(A1)", "'\tx"]);
  expect(csvCell('\rx')).toBe('"\'\rx"');
  expect(['-0.25', '+4', '0', '-2', '12.5'].map(csvCell)).toEqual(['-0.25', '+4', '0', '-2', '12.5']);
  expect(csvCell('+1,234')).toBe('"+1,234"');
  expect(csvCell('-1,2x')).toBe('"\'-1,2x"');
  expect(csvCell('부분합 20 · 1건 미보고')).toBe('부분합 20 · 1건 미보고');
  expect(csvCell('a"b')).toBe('"a""b"');
});

test('파일명은 서버가 확인한 두 run_id 앞 8자리와 날짜로만 만든다', () => {
  const { baseline, candidate } = pair();
  expect(comparisonFileName(baseline, candidate, new Date('2026-10-28T03:00:00Z')))
    .toBe(`run-comparison-33333333-${candidate.run_id.slice(0, 8)}-2026-10-28.csv`);
});

test('CSV 저장은 앵커를 클릭 동안만 문서에 붙이고 바로 떼며 URL은 즉시 해제하지 않는다', async () => {
  const created = vi.fn(() => 'blob:comparison');
  const revoked = vi.fn();
  const original = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
  Object.assign(URL, { createObjectURL: created, revokeObjectURL: revoked });
  const attached: boolean[] = [];
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    attached.push(document.body.contains(this));
  });
  compare(pair());
  fireEvent.click(await screen.findByRole('button', { name: 'CSV 저장' }));
  expect(created).toHaveBeenCalledTimes(1);
  expect(attached).toEqual([true]);
  expect(document.body.querySelector('a[download]')).toBeNull();
  expect(revoked).not.toHaveBeenCalled();
  await new Promise(done => setTimeout(done, 1100));
  expect(revoked).toHaveBeenCalledWith('blob:comparison');
  click.mockRestore();
  Object.assign(URL, original);
});
