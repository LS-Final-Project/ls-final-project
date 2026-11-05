import { useEffect, useState } from 'react';
import { statusLabels } from '../api/evaluations';
import {
  AdmissionError, blockedLabels, getAdmissionAvailable, listExperiments, listRuns, prepareAdmission, sendAdmission,
  type Admission, type Experiment, type RunItem,
} from '../api/evaluation-admission';

const RETRYABLE = new Set(['FAILED', 'CANCELLED', 'TIMED_OUT', 'PARTIAL']);
type Lists = { available: boolean; experiments: Experiment[]; runs: RunItem[] };

/**
 * 새 합성 평가 접수와 본인 실행 목록을 보여준다.
 * Args: onOpen은 작업 ID로 기존 상태 화면을 여는 함수, onOpenRun은 실행 ID로 실행 상세(summary)를 여는 함수다.
 * Returns: 접수 폼과 실행 목록 구역.
 */
export default function RunAdmission({ onOpen, onOpenRun }: { onOpen: (jobId: string) => void; onOpenRun?: (runId: string) => void }) {
  const [lists, setLists] = useState<Lists | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState('');
  // 응답을 받기 전에 다시 보내는 동안 같은 접수(같은 멱등 키)를 유지한다.
  const [pending, setPending] = useState<Admission | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<AdmissionError | Error | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoadError(null);
    Promise.all([getAdmissionAvailable(controller.signal), listExperiments(controller.signal), listRuns(controller.signal)])
      .then(([available, experiments, runs]) => setLists({ available, experiments, runs }))
      .catch(cause => { if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : '목록을 불러오지 못했습니다.'); });
    return () => controller.abort();
  }, [revision]);

  async function submit(admission: Admission) {
    if (busy) return;
    setBusy(true); setError(null); setPending(admission);
    try {
      const result = await sendAdmission(admission, new AbortController().signal);
      setPending(null);
      setRevision(value => value + 1);
      onOpen(result.jobId);
    } catch (cause) {
      const failure = cause instanceof Error ? cause : new Error('접수하지 못했습니다.');
      setError(failure);
      // 결과가 불명확할 때만 같은 키로 다시 확인한다. 거절이면 새 접수를 만든다.
      if (!(failure instanceof AdmissionError && failure.retryable)) setPending(null);
    } finally {
      setBusy(false);
    }
  }

  const experiment = lists?.experiments.find(item => item.experimentId === selected);
  const formOpen = Boolean(lists?.available);
  return <section aria-labelledby="admission-title" className="evaluation-admission">
    <div className="section-title"><h2 id="admission-title">새 접수와 내 실행</h2>
      <button onClick={() => setRevision(value => value + 1)} disabled={busy}>목록 새로고침</button></div>
    <p className="evaluation-caption" role="note">현재는 승인된 합성 smoke 실험만 접수합니다. 실제 모델·실제 데이터 접수는 실행기 연결 뒤 가능합니다.</p>
    {loadError && <p role="alert" className="error">{loadError}</p>}
    {!lists && !loadError && <p role="status" className="state">실험과 실행 목록을 불러오고 있습니다.</p>}
    {lists && <>
      {!formOpen && <p role="status">서버의 평가 접수가 꺼져 있어 새 접수를 할 수 없습니다.</p>}
      <form className="evaluation-form" onSubmit={event => {
        event.preventDefault();
        if (experiment?.admissible) void submit(pending?.experimentId === experiment.experimentId ? pending : prepareAdmission(experiment.experimentId));
      }}>
        <label htmlFor="evaluation-experiment">실험<span>모델·설정은 실험에 고정되어 있습니다. 접수할 수 없는 실험은 사유와 함께 표시합니다.</span></label>
        <div><select id="evaluation-experiment" value={selected} disabled={!formOpen || busy || pending !== null}
          onChange={event => { setSelected(event.target.value); setPending(null); setError(null); }}>
          <option value="">실험을 선택하세요</option>
          {lists.experiments.map(item => <option key={item.experimentId} value={item.experimentId} disabled={!item.admissible}>
            {item.name}{item.blockedReason ? ` (${blockedLabels[item.blockedReason]})` : ''}
          </option>)}
        </select>
        <button type="submit" disabled={!formOpen || busy || !experiment?.admissible}>{busy ? '접수 중' : pending ? '같은 접수로 다시 확인' : '접수'}</button></div>
      </form>
      {experiment && <dl aria-label="선택한 실험">
        <dt>모델</dt><dd><code>{experiment.modelId}</code></dd><dt>목적</dt><dd>{experiment.purpose}</dd>
        <dt>데이터 출처</dt><dd>{experiment.dataOrigin}</dd><dt>대상 수</dt><dd>{experiment.targetCount}</dd>
        {experiment.blockedReason && <><dt>접수 불가</dt><dd>{blockedLabels[experiment.blockedReason]}</dd></>}
      </dl>}
      {error && <p role="alert" className="error">{error.message}</p>}
      {pending && <p role="status">결과를 확인하지 못한 접수가 있습니다. 새 접수 전에 먼저 '같은 접수로 다시 확인'을 눌러 결과를 확인하세요.</p>}
      {lists.runs.length === 0 ? <p>접수한 실행이 없습니다.</p> : <table aria-label="내 실행">
        <thead><tr><th>접수 시각</th><th>상태</th><th>대상</th><th>작업</th></tr></thead>
        <tbody>{lists.runs.map(run => {
          const source = lists.experiments.find(item => item.experimentId === run.experimentId);
          return <tr key={run.runId}>
            <td><time dateTime={run.createdAt}>{new Date(run.createdAt).toLocaleString('ko-KR')}</time></td>
            <td>{statusLabels[run.status]}{run.needsReview ? ' · 검토 필요' : ''}</td><td>{run.requestedCount}</td>
            <td><button onClick={() => onOpen(run.jobId)}>열기</button>
              {RETRYABLE.has(run.status) && formOpen && source?.admissible
                && <button disabled={busy || pending !== null} onClick={() => { setSelected(run.experimentId); void submit(prepareAdmission(run.experimentId)); }}>같은 실험으로 다시 접수</button>}</td>
          </tr>;
        })}</tbody>
      </table>}
      {onOpenRun && lists.experiments.some(item => item.runs.length > 0) && <table aria-label="실험별 실행">
        <caption className="evaluation-caption">반입 실행은 위 목록에 없고 여기서 실행 상세로 엽니다.</caption>
        <thead><tr><th>실험</th><th>실행</th><th>상태</th><th>상세</th></tr></thead>
        <tbody>{lists.experiments.flatMap(item => item.runs.map(run => <tr key={run.runId}>
          <td>{item.name}</td>
          <td><code>{run.runId.slice(0, 8)}</code>{run.origin === 'IMPORT' && <> <span className="evaluation-tag">반입</span></>}</td>
          <td>{statusLabels[run.status]}</td>
          <td><button onClick={() => onOpenRun(run.runId)} aria-label={`실행 ${run.runId.slice(0, 8)} 상세`}>상세</button></td>
        </tr>))}</tbody>
      </table>}
    </>}
  </section>;
}
