import { useEffect, useState } from 'react';
import { getEvaluationSummary, type RunSummary as Summary } from '../api/evaluation-summary';
import { EvaluationReadError, statusLabels } from '../api/evaluations';

/** 미보고를 실제 0과 구별한다. Args: 관측값. Returns: 표시 문자열. */
const number = (value: number | null) => value === null ? '미보고' : value.toLocaleString('ko-KR', { maximumFractionDigits: 2 });
/** 전체 합계와 알려진 부분합을 구별한다. Args: 토큰 집계. Returns: 표시 문자열. */
const token = (value: Summary['first']['tokens']['total_tokens']) => value.total !== null ? number(value.total)
  : value.known_sum === null ? '미보고' : `부분합 ${number(value.known_sum)} · ${value.missing_count}건 미보고`;

/** 비용 문자열을 숫자로 바꾸거나 반올림하지 않는다. Args: 비용 집계. Returns: USD 합계·부분합 또는 미보고 안내. */
const cost = (value: Summary['first']['cost_usd']) => !value ? '비용 집계 미연결'
  : value.total !== null ? `${value.total} USD`
  : value.known_sum === null ? '미보고' : `부분합 ${value.known_sum} USD · ${value.missing_count}건 미보고`;

/** 0~1 수치를 반올림 표시만 하고 등급·색을 붙이지 않는다. Args: 비율 또는 null. Returns: 표시 문자열. */
const score = (value: number | null) => value === null ? '계산 불가' : value.toLocaleString('ko-KR', { maximumFractionDigits: 4 });

/** 저장된 합성 품질을 숫자와 문구로만 표시한다. Args: 검증한 합성 품질 또는 미저장. Returns: 품질 절. */
function SyntheticQualityView({ quality }: { quality: Summary['synthetic_quality'] }) {
  return <div aria-labelledby="synthetic-quality-title">
    <h3 id="synthetic-quality-title">합성 품질 (반품 하위유형 · 최초 시도)</h3>
    <div className="evaluation-note" role="note"><p><strong>합성 정답 기준, 실제 품질·합격 판정 아님</strong></p></div>
    {!quality ? <p>합성 품질 미저장</p> : <>
      <p>채점 판본 {quality.scoring_version}{quality.stored_scoring_version_count > 1 ? ` (저장 판본 ${quality.stored_scoring_version_count}개 중 최신)` : ''} · 정답 판본 {quality.gold_version ?? '미지정'}</p>
      <p>채점 대상 {quality.scored_target_count}건 / 전체 대상 {quality.target_count}건 · accuracy {score(quality.accuracy)} · macro-F1 {score(quality.macro_f1)} · null 예측 {quality.null_predictions}건</p>
      <div className="evaluation-table-scroll" tabIndex={0} role="region" aria-label="클래스별 합성 품질 표">
        <table className="evaluation-metrics"><caption>클래스별 수치 · 실패·보류·null 예측은 해당 정답 클래스의 FN</caption>
          <thead><tr><th scope="col">클래스</th><th scope="col">support</th><th scope="col">precision</th><th scope="col">recall</th><th scope="col">F1</th></tr></thead>
          <tbody>{(['DEFECT', 'NON_DEFECT'] as const).map(label => {
            const item = quality.per_class[label];
            return <tr key={label}><th scope="row">{label}</th><td>{item.support}</td><td>{score(item.precision)}</td><td>{score(item.recall)}</td><td>{score(item.f1)}</td></tr>;
          })}</tbody>
        </table>
      </div>
    </>}
  </div>;
}

/** 상태 조회에 결속된 한 실행의 집계를 표시한다. Args: 실행 ID. Returns: 합성 집계 또는 안전한 오류. */
export default function RunSummary({ runId }: { runId: string }) {
  const [data, setData] = useState<Summary>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    setData(undefined); setError(undefined);
    void getEvaluationSummary(runId, controller.signal).then(value => {
      if (!controller.signal.aborted) setData(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof EvaluationReadError ? reason.message : '집계를 조회하지 못했습니다. 실행 현황을 새로고침해 주세요.');
    });
    return () => controller.abort();
  }, [runId]);
  return <section aria-labelledby="run-summary-title" aria-busy={!data && !error}>
    <h2 id="run-summary-title">실행 집계</h2>
    <p>합성 연결 검사 기록입니다. 실제 모델 품질이나 사용량 측정 결과가 아닙니다.</p>
    {!data && !error && <p role="status">집계를 확인하고 있습니다.</p>}
    {error && <p role="alert">{error} 실행 현황의 새로고침으로 다시 확인하세요.</p>}
    {data && <>
      <p>고정 대상 {data.target_count}건 · 최초 시도 미저장 {data.targets_without_first_attempt}건</p>
      <p className="evaluation-caption">집계 시점 상태: {statusLabels[data.status]} · <time dateTime={data.generated_at}>{new Date(data.generated_at).toLocaleString('ko-KR')}</time></p>
      <div className="evaluation-table-scroll" tabIndex={0} role="region" aria-label="최초·재시도·전체 집계 표">
        <table className="evaluation-metrics"><caption>저장된 분류 시도 기준 · 지연 단위 ms</caption>
          <thead><tr><th scope="col">항목</th><th scope="col">최초 시도</th><th scope="col">재시도</th><th scope="col">전체</th></tr></thead>
          <tbody>{[
            ['저장 시도', (v: Summary['first']) => number(v.stored_attempt_count)],
            ['미종료 시도', (v: Summary['first']) => number(v.unfinished_attempt_count)],
            ['비용 (USD)', (v: Summary['first']) => cost(v.cost_usd)],
            ['비용 보고 / 미보고', (v: Summary['first']) => v.cost_usd ? `${v.cost_usd.reported_count} / ${v.cost_usd.missing_count}` : '미연결'],
            ['가격표 판본 수', (v: Summary['first']) => v.cost_usd ? `${v.cost_usd.pricing_version_count}${v.cost_usd.pricing_version_count > 1 ? '종 (혼합)' : '종'}` : '미연결'],
            ['총 토큰', (v: Summary['first']) => token(v.tokens.total_tokens)],
            ['입력 토큰', (v: Summary['first']) => token(v.tokens.input_tokens)],
            ['출력 토큰', (v: Summary['first']) => token(v.tokens.output_tokens)],
            ['추론 토큰', (v: Summary['first']) => token(v.tokens.thought_tokens)],
            ['캐시 입력 토큰', (v: Summary['first']) => token(v.tokens.cached_input_tokens)],
            ['지연 p50 / p95', (v: Summary['first']) => `${number(v.latency_ms.p50)} / ${number(v.latency_ms.p95)}`],
            ['지연 보고 / 미보고', (v: Summary['first']) => `${v.latency_ms.reported_count} / ${v.latency_ms.missing_count}`],
            ['스키마 위반 / 저장 응답', (v: Summary['first']) => `${v.schema_violation_count} / ${v.stored_raw_response_count}`],
            ['스키마 위반율', (v: Summary['first']) => v.schema_violation_rate === null ? '판정 보류' : `${number(v.schema_violation_rate * 100)}%`],
            ['증거 불완전 시도', (v: Summary['first']) => number(v.incomplete_evidence_attempt_count)],
          ].map(([label, display]) => <tr key={label as string}><th scope="row">{label as string}</th>{(['first', 'retry', 'all'] as const).map(group => <td key={group}>{(display as (v: Summary['first']) => string)(data[group])}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <p className="evaluation-caption">부분합은 보고된 값만 더한 수치입니다. 미보고는 0이 아니며, 캐시·추론 토큰을 총 토큰에 다시 더하지 않습니다. 위반율은 응답이 없거나 증거가 불완전하면 보류합니다.</p>
      <p className="evaluation-caption">비용은 저장된 USD 금액이며 청구액이나 무료 사용 여부를 뜻하지 않습니다. 가격표가 여러 판본이면 각 시도에 기록된 비용의 합이며 공통 단가로 재계산하지 않습니다.</p>
      <SyntheticQualityView quality={data.synthetic_quality} />
      <p>품질 점수: 정답 검수·연결 전 · 실험 간 비교: 아직 제공하지 않음</p>
    </>}
  </section>;
}
