import { useEffect, useState } from 'react';
import { getEvaluationSummary, isLockedPath, type RunSummary as Summary } from '../api/evaluation-summary';
import { getRunCases, type RunCases } from '../api/evaluation-cases';
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
  return <section aria-labelledby="synthetic-quality-title">
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
  </section>;
}

type Locked = NonNullable<Summary['locked_quality']>;
type LockedMetric = NonNullable<Locked['metrics']['category']>;
const metricLabels: [keyof Locked['metrics'], string][] = [
  ['category', '대분류'], ['urgency', '긴급도'], ['secondary', '보조 분류'], ['return_subtype', '반품 하위유형'], ['provided_info', '제공 정보'],
];
const segmentLabels: Record<string, string> = { all: '전체', 'scope=FULL': '채점 범위 FULL', 'scope=PRECISION_ONLY': '채점 범위 PRECISION_ONLY (precision만)' };
const originLabels: Record<Summary['data_origin'], string> = { synthetic: '합성', demo: '시연', real: '실제' };
/** 응답 수치를 반올림하지 않고 표시한다. Args: 값 또는 null. Returns: 표시 문자열. */
const raw = (value: number | null) => value === null ? '계산 불가' : String(value);
/** 건수 객체를 키=값 목록으로 표시한다. Args: 건수 객체. Returns: 표시 문자열. */
const counts = (value: Record<string, number>) => Object.entries(value).map(([key, n]) => `${key} ${n}`).join(' · ') || '없음';

/** 잠금 지표 하나의 판본과 범위별 저장 수치를 표로 표시한다. Args: 지표 이름·값. Returns: 지표 절. */
function LockedMetricView({ label, metric }: { label: string; metric: LockedMetric | null }) {
  return <div>
    <h4>{label}</h4>
    {!metric ? <p>미저장</p> : <>
      <p>채점 판본 {metric.scoring_version}{metric.stored_scoring_version_count > 1 ? ` (저장 판본 ${metric.stored_scoring_version_count}개 중 최신)` : ''} · 정답 판본 {metric.gold_version} · 채점 대상 {metric.scored_target_count}건 / 전체 대상 {metric.target_count}건</p>
      {Object.entries(metric.segments).map(([name, segment]) => <div key={name}>
        <div className="evaluation-table-scroll" tabIndex={0} role="region" aria-label={`${label} ${segmentLabels[name] ?? name} 잠금 품질 표`}>
          <table className="evaluation-metrics"><caption>{segmentLabels[name] ?? name} · 고정 분모 {segment.locked_denominator}건</caption>
            <thead><tr><th scope="col">지표</th><th scope="col">값</th><th scope="col">분자 / 분모</th><th scope="col">support</th></tr></thead>
            <tbody>{Object.entries(segment.values).map(([key, item]) => <tr key={key}><th scope="row">{key}</th><td>{raw(item.value)}</td>
              <td>{item.numerator === null || item.denominator === null ? '해당 없음' : `${item.numerator} / ${item.denominator}`}</td><td>{item.support ?? '해당 없음'}</td></tr>)}</tbody>
          </table>
        </div>
        <p className="evaluation-caption">결과 분포: {counts(segment.outcomes)} · 제외: {counts(segment.excluded_by_reason)}{segment.items ? ` · 항목 집계: ${(['tp', 'fp', 'fn'] as const).filter(key => key in segment.items!).map(key => `${key} ${segment.items![key]}`).join(' · ')}` : ''}</p>
        {Object.keys(segment.per_class).length > 0 && <div className="evaluation-table-scroll" tabIndex={0} role="region" aria-label={`${label} ${segmentLabels[name] ?? name} 클래스별 잠금 품질 표`}>
          <table className="evaluation-metrics"><caption>클래스별 수치</caption>
            <thead><tr><th scope="col">클래스</th><th scope="col">support</th><th scope="col">tp</th><th scope="col">fp</th><th scope="col">fn</th><th scope="col">precision</th><th scope="col">recall</th><th scope="col">F1</th></tr></thead>
            <tbody>{Object.entries(segment.per_class).map(([name, item]) => <tr key={name}><th scope="row">{name}</th><td>{item.support}</td><td>{item.tp}</td><td>{item.fp}</td><td>{item.fn}</td>
              <td>{raw(item.precision)}</td><td>{raw(item.recall)}</td><td>{raw(item.f1)}</td></tr>)}</tbody>
          </table>
        </div>}
      </div>)}
    </>}
  </div>;
}

/** 형식 위반율(분자/분모)과 분류 최초 시도 결과별 건수를 표시한다. Args: 저장값 또는 미저장. Returns: 형식 위반 절. */
function OutcomeView({ outcomes }: { outcomes: Locked['outcomes'] }) {
  return <div>
    <h4>형식 위반율 (분류 최초 시도)</h4>
    {!outcomes ? <p>미저장</p> : <>
      <p>채점 판본 {outcomes.scoring_version}{outcomes.stored_scoring_version_count > 1 ? ` (저장 판본 ${outcomes.stored_scoring_version_count}개 중 최신)` : ''} · 형식 위반율 {raw(outcomes.schema_violation_rate)} ({outcomes.schema_violations} / {outcomes.received}, 분모는 응답 받은 시도 VALID+SCHEMA_VIOLATION)</p>
      <div className="evaluation-table-scroll" tabIndex={0} role="region" aria-label="분류 최초 시도 결과별 건수 표">
        <table className="evaluation-metrics"><caption>결과별 건수 · 보류·전송 실패·타임아웃·시도 없음은 위반율 분모에서 뺌</caption>
          <thead><tr><th scope="col">결과</th><th scope="col">건수</th></tr></thead>
          <tbody>{Object.entries(outcomes.outcomes).map(([key, n]) => <tr key={key}><th scope="row">{key}</th><td>{n}</td></tr>)}</tbody>
        </table>
      </div>
    </>}
  </div>;
}

const stageLabels: Record<NonNullable<Summary['stage_metrics']>[number]['stage'], string> = { search: '검색', draft: '초안', rfp: 'RFP' };

/** 검색·초안·RFP 단계 채점 행을 저장 키·값 그대로 표시하고 판정하지 않는다. Args: 단계 블록 목록. Returns: 단계별 지표 절. */
function StageMetricsView({ blocks }: { blocks: NonNullable<Summary['stage_metrics']> }) {
  return <section aria-labelledby="stage-metrics-title">
    <h3 id="stage-metrics-title">단계별 지표</h3>
    {blocks.length === 0 ? <p>미저장</p> : blocks.map(block => <div key={block.stage}>
      <h4>{stageLabels[block.stage]} ({block.stage})</h4>
      <p>채점 판본 {block.scoring_version}{block.stored_scoring_version_count > 1 ? ` (저장 판본 ${block.stored_scoring_version_count}개 중 최신)` : ''}</p>
      <div className="evaluation-table-scroll" tabIndex={0} role="region" aria-label={`${stageLabels[block.stage]} 단계 지표 표`}>
        <table className="evaluation-metrics"><caption>저장 키·값 그대로 · 합격 판정 아님</caption>
          <thead><tr><th scope="col">지표</th><th scope="col">segment</th><th scope="col">클래스</th><th scope="col">값</th><th scope="col">support</th><th scope="col">기준</th><th scope="col">tp / fp / fn</th></tr></thead>
          <tbody>{block.items.map(item => <tr key={`${item.metric_key}:${item.segment}:${item.class_label ?? ''}`}>
            <th scope="row">{item.metric_key}</th><td>{item.segment}</td><td>{item.class_label ?? '해당 없음'}</td><td>{raw(item.value)}</td>
            <td>{item.support ?? '해당 없음'}</td><td>{item.threshold === null || item.comparison === null ? '해당 없음' : `${item.comparison} ${item.threshold}`}</td>
            <td>{(['tp', 'fp', 'fn'] as const).map(key => item.details[key] ?? '미보고').join(' / ')}</td></tr>)}</tbody>
        </table>
      </div>
    </div>)}
  </section>;
}

/** 실험 설정(단계·조절값·모델·판본·구성 hash 앞 12자)을 표시한다. Args: 실험 설정 또는 미제공. Returns: 실험 설정 절. */
function ExperimentView({ experiment }: { experiment: Summary['experiment'] }) {
  const knobs = experiment ? Object.entries(experiment.knobs) : [];
  return <section aria-labelledby="experiment-config-title">
    <h3 id="experiment-config-title">실험 설정</h3>
    {!experiment ? <p>미제공</p> : <div className="evaluation-table-scroll" tabIndex={0} role="region" aria-label="실험 설정 표">
      <table className="evaluation-metrics"><caption>{experiment.name} · 목적 {experiment.purpose} · 분할 {experiment.data_split ?? '해당 없음'}</caption>
        <tbody>{([
          ['단계(stage)', experiment.stage ?? '미지정'],
          ['조절값(knobs)', knobs.length ? knobs.map(([key, value]) => `${key}=${String(value)}`).join(' · ') : '없음'],
          ['모델', experiment.model_id],
          ['프롬프트 판본', experiment.prompt_version ?? '미지정'],
          ['입력 판본', experiment.input_builder_version ?? '미지정'],
          ['색인 판본', experiment.index_version ?? '미지정'],
          ['구성 hash', experiment.config_hash.slice(0, 12)],
        ] as const).map(([label, value]) => <tr key={label}><th scope="row">{label}</th><td>{value}</td></tr>)}</tbody>
      </table>
    </div>}
  </section>;
}

/** 잠금 정답 품질을 응답 값 그대로 표시하고 판정하지 않는다. Args: 검증한 요약. Returns: 잠금 품질 절. */
function LockedQualityView({ data }: { data: Summary }) {
  const quality = data.locked_quality;
  const stored = quality ? metricLabels.filter(([key]) => quality.metrics[key] !== null).length : 0;
  return <section aria-labelledby="locked-quality-title">
    <h3 id="locked-quality-title">잠금 정답 품질 (최초 시도)</h3>
    {!quality ? <p>정답 묶음 미연결</p> : <>
      <div className="evaluation-note" role="note"><p><strong>quality_gate_evaluated={String(quality.quality_gate_evaluated)} · 합격 판정 아님</strong></p></div>
      <p>정답 묶음 {quality.bundle_id} · {data.quality_available ? '다섯 지표 연결' : `일부 지표만 저장 (${stored}/${metricLabels.length})`}</p>
      {metricLabels.map(([key, label]) => <LockedMetricView key={key} label={label} metric={quality.metrics[key]} />)}
      <OutcomeView outcomes={quality.outcomes ?? null} />
    </>}
  </section>;
}

const caseLabels: Record<RunCases['items'][number]['status'], string> = {
  PENDING: '대기', VALID: '유효', INVALID_OUTPUT: '출력 오류', HELD: '보류', FAILED: '실패', EXCLUDED: '제외',
};
// 빌드 시 설정한 LangSmith 프로젝트 주소. https가 아니면 링크를 숨긴다. 실행별 trace 연결은 없다.
function langsmithUrl(): string | null {
  const value = import.meta.env.VITE_LANGSMITH_PROJECT_URL;
  try { return typeof value === 'string' && new URL(value).protocol === 'https:' ? value : null; } catch { return null; }
}

/** 사례별 시도를 20개씩 더 불러와 보인다(KAN-139 4차). Args: 실행 ID. Returns: 사례 표 구역. */
function CasesView({ runId }: { runId: string }) {
  const [pages, setPages] = useState<RunCases[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    setError(undefined);
    void getRunCases(runId, cursor, controller.signal).then(page => {
      if (!controller.signal.aborted) setPages(value => [...value, page]);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof EvaluationReadError ? reason.message : '사례를 조회하지 못했습니다.');
    });
    return () => controller.abort();
  }, [runId, cursor]);
  const items = pages.flatMap(page => page.items);
  const next = pages.at(-1)?.next_cursor ?? null;
  return <section aria-labelledby="run-cases-title">
    <h3 id="run-cases-title">사례별 시도</h3>
    <p className="evaluation-caption">사례 ID는 해시 앞 12자입니다. 원문·원시 응답은 저장하지 않으며, 잠금 채점은 실행 단위라 사례별 정답·채점은 표시하지 않습니다. 출력 코드는 분류 단계만 보입니다.</p>
    {error && <p role="alert">{error}</p>}
    {!error && pages.length === 0 && <p role="status">사례를 확인하고 있습니다.</p>}
    {pages.length > 0 && items.length === 0 && <p>저장된 사례가 없습니다.</p>}
    {items.length > 0 && <div className="evaluation-table-scroll" tabIndex={0} role="region" aria-label="사례별 시도 표">
      <table className="evaluation-metrics">
        <thead><tr><th scope="col">사례</th><th scope="col">상태</th><th scope="col">단계 · 시도</th><th scope="col">결과</th><th scope="col">오류 · HTTP</th><th scope="col">종료 사유</th><th scope="col">호출 수</th><th scope="col">모델 판본</th><th scope="col">지연 ms</th><th scope="col">출력 코드</th></tr></thead>
        <tbody>{items.flatMap(item => (item.attempts.length ? item.attempts : [null]).map((attempt, index) => <tr key={`${item.case_id}:${index}`}>
          {index === 0 && <><th scope="row" rowSpan={Math.max(item.attempts.length, 1)}><code>{item.target_ref_sha}</code></th>
            <td rowSpan={Math.max(item.attempts.length, 1)}>{caseLabels[item.status]}</td></>}
          {attempt ? <><td>{attempt.step} · {attempt.attempt}</td><td>{attempt.outcome}</td>
            <td>{attempt.error_code ?? '-'}{attempt.http_status !== null && ` · ${attempt.http_status}`}</td>
            <td>{attempt.finish_reasons.join(', ') || '-'}</td><td>{attempt.call_attempts ?? '-'}</td>
            <td>{attempt.model_version ?? '-'}</td><td>{attempt.latency_ms ?? '-'}</td>
            <td>{attempt.parsed_output ? [attempt.parsed_output.category_code, attempt.parsed_output.secondary_category, attempt.parsed_output.urgency_flag, attempt.parsed_output.return_subtype, ...attempt.parsed_output.provided_info].filter(Boolean).join(' · ') || '-' : '-'}</td></>
            : <td colSpan={8}>저장된 시도가 없습니다.</td>}
        </tr>))}</tbody>
      </table>
    </div>}
    {next && !error && <button onClick={() => setCursor(next)}>더 보기</button>}
  </section>;
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
    <h2 id="run-summary-title">실행 집계{data?.origin === 'IMPORT' && <> <span className="evaluation-tag">반입</span></>}</h2>
    {langsmithUrl() && <p className="evaluation-caption"><a href={langsmithUrl()!} target="_blank" rel="noopener noreferrer">추적 보기 (LangSmith 프로젝트)</a> · 실행별 추적 연결은 없어 프로젝트 화면으로 이동합니다.</p>}
    {data?.origin === 'IMPORT' && <p className="evaluation-caption">반입 실행(IMPORT)입니다. 후보 비교 실측 결과를 평가 저장소로 들여온 기록이며 원시 응답은 저장하지 않습니다.</p>}
    {data && (isLockedPath(data)
      ? <p>평가 실행 저장 기록입니다 · 데이터 출처 {originLabels[data.data_origin]} · 품질은 잠금 정답 저장값이며 합격 판정이 아닙니다.</p>
      : <p>합성 연결 검사 기록입니다. 실제 모델 품질이나 사용량 측정 결과가 아닙니다.</p>)}
    {!data && !error && <p role="status">집계를 확인하고 있습니다.</p>}
    {error && <p role="alert">{error} 실행 현황의 새로고침으로 다시 확인하세요.</p>}
    {data && <>
      <p>고정 대상 {data.target_count}건 · 최초 시도 미저장 {data.targets_without_first_attempt}건</p>
      <ExperimentView experiment={data.experiment ?? null} />
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
      {isLockedPath(data) ? <LockedQualityView data={data} /> : <SyntheticQualityView quality={data.synthetic_quality} />}
      <StageMetricsView blocks={data.stage_metrics ?? []} />
      <p>품질 점수: {data.quality_available ? '잠금 정답 다섯 지표 연결' : data.quality_unavailable_reason === 'GOLD_PARTIAL' ? '잠금 정답 일부 지표만 연결' : '정답 검수·연결 전'}</p>
      <CasesView runId={runId} />
    </>}
  </section>;
}
