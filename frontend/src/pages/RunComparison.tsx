import { useEffect, useState } from 'react';
import { getEvaluationSummary, type RunSummary as Summary } from '../api/evaluation-summary';
import { EvaluationReadError, isJobId } from '../api/evaluations';

type Attempt = Summary['first'];
type Row = [label: string, baseline: string, candidate: string, difference: string];
const terminal = new Set(['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED', 'TIMED_OUT']);
const notComputed = '계산 안 함';

/** 숫자를 반올림 표시만 한다. Args: 값. Returns: 표시 문자열. */
const plain = (value: number) => value.toLocaleString('ko-KR', { maximumFractionDigits: 4 });
/** 부호 있는 차이만 표시하고 개선·퇴행을 판정하지 않는다. Args: 후보−기준. Returns: 표시 문자열. */
const signed = (value: number) => `${value > 0 ? '+' : ''}${plain(value)}`;
/** 토큰 합계 또는 부분합을 표시한다. Args: 토큰 집계. Returns: 표시 문자열. */
const tokenText = (value: Attempt['tokens']['total_tokens']) => value.total !== null ? plain(value.total)
  : value.known_sum === null ? '미보고' : `부분합 ${plain(value.known_sum)} · ${value.missing_count}건 미보고`;
/** 비용 문자열을 바꾸지 않고 표시한다. Args: 비용 집계. Returns: 표시 문자열. */
const costText = (value: Attempt['cost_usd']) => !value ? '비용 집계 미연결' : value.total !== null ? `${value.total} USD`
  : value.known_sum === null ? '미보고' : `부분합 ${value.known_sum} USD · ${value.missing_count}건 미보고`;

/** 비교 가능 조건을 확인한다. Args: 기준·후보 요약. Returns: 비교 불가 이유 또는 null. */
function refusal(baseline: Summary, candidate: Summary): string | null {
  if (baseline.run_id === candidate.run_id) return '같은 실행끼리는 비교하지 않습니다.';
  if (!baseline.comparison_key || !candidate.comparison_key) return '비교 키가 없는 응답이라 비교 가능 여부를 확인할 수 없습니다.';
  if (baseline.comparison_key !== candidate.comparison_key) return '두 실행의 평가 조건(데이터·대상·정답·정책 판본 등)이 달라 비교하지 않습니다.';
  if (!terminal.has(baseline.status) || !terminal.has(candidate.status)) return '두 실행이 모두 종료된 뒤 비교할 수 있습니다.';
  return null;
}

/** 조건을 충족한 값만 차이를 계산해 표 행을 만든다. Args: 기준·후보 요약. Returns: 행과 품질 차이 미계산 이유. */
function rows(baseline: Summary, candidate: Summary): { rows: Row[]; qualityNote?: string } {
  const [a, b] = [baseline.first, candidate.first];
  const tokens = [['총 토큰', 'total_tokens'], ['입력 토큰', 'input_tokens'], ['출력 토큰', 'output_tokens'],
    ['추론 토큰', 'thought_tokens'], ['캐시 입력 토큰', 'cached_input_tokens']] as const;
  const result: Row[] = tokens.map(([label, key]) => {
    const [x, y] = [a.tokens[key], b.tokens[key]];
    return [label, tokenText(x), tokenText(y), x.total !== null && y.total !== null ? signed(y.total - x.total) : notComputed];
  });
  for (const key of ['p50', 'p95'] as const) {
    const [x, y] = [a.latency_ms[key], b.latency_ms[key]];
    result.push([`지연 ${key} (ms)`, x === null ? '미보고' : `${plain(x)} (표본 ${a.latency_ms.reported_count})`,
      y === null ? '미보고' : `${plain(y)} (표본 ${b.latency_ms.reported_count})`, x !== null && y !== null ? signed(y - x) : notComputed]);
  }
  result.push(['비용 (USD)', costText(a.cost_usd), costText(b.cost_usd), notComputed]);
  const [p, q] = [baseline.synthetic_quality, candidate.synthetic_quality];
  const qualityNote = !p || !q ? '한쪽 이상의 합성 품질이 저장되지 않아 품질 차이를 계산하지 않습니다.'
    : p.gold_sha256 !== q.gold_sha256 || p.gold_version !== q.gold_version || p.scored_target_count !== q.scored_target_count
      ? '정답 판본 또는 채점 대상이 달라 품질 차이를 계산하지 않습니다.' : undefined;
  for (const [label, key] of [['합성 품질 accuracy', 'accuracy'], ['합성 품질 macro-F1', 'macro_f1']] as const) {
    const [x, y] = [p?.[key] ?? null, q?.[key] ?? null];
    result.push([label, p ? (x === null ? '계산 불가' : plain(x)) : '미저장', q ? (y === null ? '계산 불가' : plain(y)) : '미저장',
      !qualityNote && x !== null && y !== null ? signed(y - x) : notComputed]);
  }
  return { rows: result, qualityNote };
}

const numeric = /^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$/;
/** 스프레드시트 수식으로 해석될 셀을 막고 CSV로 인용한다. Args: 셀 문자열. Returns: 안전한 CSV 셀. */
export function csvCell(value: string): string {
  const safe = /^[=+\-@\t\r]/.test(value) && !numeric.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/** 화면의 비교 표만 CSV로 만든다. Args: 비교 가능한 기준·후보 요약. Returns: UTF-8 BOM·CRLF CSV 문자열. */
export function comparisonCsv(baseline: Summary, candidate: Summary): string {
  const table = rows(baseline, candidate);
  const lines: string[][] = [
    ['기준 run_id', baseline.run_id], ['후보 run_id', candidate.run_id], ['comparison_key', candidate.comparison_key ?? ''],
    ['기준 집계 시각', baseline.generated_at], ['후보 집계 시각', candidate.generated_at], [],
    ['항목', '기준', '후보', '차이(후보−기준)'], ...table.rows, [],
    ...(table.qualityNote ? [['품질 차이', table.qualityNote]] : []),
    ['안내', '합성 정답 기준, 실제 품질·합격 판정 아님. 최초 시도 기준이며 비용 차이는 계산하지 않음.'],
  ];
  return `\uFEFF${lines.map(line => line.map(csvCell).join(',')).join('\r\n')}\r\n`;
}

/** 서버가 확인한 run_id와 날짜로만 파일명을 만든다. Args: 기준·후보 요약, 기준 시각. Returns: 파일명. */
export function comparisonFileName(baseline: Summary, candidate: Summary, now = new Date()): string {
  return `run-comparison-${baseline.run_id.slice(0, 8)}-${candidate.run_id.slice(0, 8)}-${now.toISOString().slice(0, 10)}.csv`;
}

/** 브라우저에서 CSV 파일을 저장한다. Args: 기준·후보 요약. Returns: 없음. */
function download(baseline: Summary, candidate: Summary) {
  const url = URL.createObjectURL(new Blob([comparisonCsv(baseline, candidate)], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url; link.download = comparisonFileName(baseline, candidate);
  // 일부 브라우저는 문서에 없는 앵커 클릭을 무시하거나 시작 전 해제된 URL을 읽지 못한다.
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 현재 실행을 후보로, 입력한 실행을 기준으로 요약 두 개를 비교한다. Args: 후보 실행 ID. Returns: 비교 절. */
export default function RunComparison({ candidateId }: { candidateId: string }) {
  const [input, setInput] = useState('');
  const [baselineId, setBaselineId] = useState<string>();
  const [pair, setPair] = useState<[Summary, Summary]>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (!baselineId) return;
    const controller = new AbortController();
    setPair(undefined); setError(undefined);
    void Promise.all([getEvaluationSummary(baselineId, controller.signal), getEvaluationSummary(candidateId, controller.signal)])
      .then(value => { if (!controller.signal.aborted) setPair(value); })
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof EvaluationReadError ? reason.message : '비교할 집계를 조회하지 못했습니다.');
      });
    return () => controller.abort();
  }, [baselineId, candidateId]);
  const reason = pair && refusal(...pair);
  const table = pair && !reason ? rows(...pair) : undefined;
  return <section aria-labelledby="run-comparison-title">
    <h2 id="run-comparison-title">실행 비교</h2>
    <p>현재 실행을 후보로, 입력한 실행을 기준으로 최초 시도 집계를 나란히 봅니다. 차이는 후보−기준이며 좋고 나쁨을 판정하지 않습니다.</p>
    <form className="evaluation-form" onSubmit={event => { event.preventDefault(); setBaselineId(input.trim().toLowerCase()); }}>
      <label htmlFor="comparison-baseline">기준 실행 ID<span>비교할 다른 실행의 run_id를 입력하세요.</span></label>
      <div><input id="comparison-baseline" value={input} onChange={event => setInput(event.target.value)}
        placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" autoComplete="off" spellCheck={false} />
      <button type="submit" disabled={!isJobId(input.trim())}>비교</button></div>
    </form>
    {baselineId && !pair && !error && <p role="status">비교할 집계를 확인하고 있습니다.</p>}
    {error && <p role="alert">{error}</p>}
    {reason && <p role="alert">{reason}</p>}
    {table && <>
      <div className="evaluation-table-scroll" tabIndex={0} role="region" aria-label="실행 비교 표">
        <table className="evaluation-metrics"><caption>최초 시도 기준 · 차이 = 후보 − 기준</caption>
          <thead><tr><th scope="col">항목</th><th scope="col">기준</th><th scope="col">후보</th><th scope="col">차이</th></tr></thead>
          <tbody>{table.rows.map(([label, ...cells]) => <tr key={label}><th scope="row">{label}</th>{cells.map((cell, index) => <td key={index}>{cell}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <p className="evaluation-caption">토큰 차이는 두 실행 모두 전건 보고된 합계일 때만, 지연 차이는 두 실행 모두 보고됐을 때만 계산합니다. 비용은 가격표 판본이 같은지 확인할 수 없어 차이를 계산하지 않습니다.</p>
      {table.qualityNote && <p className="evaluation-caption">{table.qualityNote}</p>}
      <button type="button" onClick={() => download(...pair!)}>CSV 저장</button>
      <p className="evaluation-caption">합성 품질은 합성 정답 기준이며 실제 품질·합격 판정이 아닙니다.</p>
    </>}
  </section>;
}
