import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getApiMode } from '../api/client';
import { AnalysisSessionChanged, AnalysisUncertain, getAnalysisJob, startAnalysis, type AnalysisAccepted, type AnalysisAttempt } from '../api/cs-analysis';
import type { Identity } from '../api/platform';

const STATUS = { QUEUED: '대기 중', RUNNING: '분석 중', SUCCEEDED: '작업 완료·분류와 초안 검증 결과를 확인하세요', PARTIAL: '일부 완료·결과 확인 필요', FAILED: '분석 작업 실패', CANCELLED: '분석 작업 취소' };
export default function CsAnalysisAction({ user, ticketId, closed, hasAnalysis, onComplete }: {
  user: Identity; ticketId: string; closed: boolean; hasAnalysis: boolean; onComplete: () => void;
}) {
  const [attempt, setAttempt] = useState<AnalysisAttempt>();
  const [accepted, setAccepted] = useState<AnalysisAccepted>();
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [message, setMessage] = useState('');
  const [blocked, setBlocked] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const completed = useRef<string | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const permitted = getApiMode() === 'live' && user.roles.includes('cs_agent') && !closed;
  const job = useQuery({
    queryKey: ['cs-analysis-job', user.id, ticketId, accepted?.job_id],
    queryFn: ({ signal }) => getAnalysisJob(user.id, ticketId, accepted!.job_id, signal),
    enabled: !!accepted && !blocked, retry: false,
    refetchInterval: query => !query.state.error && (!query.state.data || ['QUEUED', 'RUNNING'].includes(query.state.data.status)) ? 1000 : false,
  });
  const status = job.data?.status ?? accepted?.status;
  const running = !!status && ['QUEUED', 'RUNNING'].includes(status);
  useEffect(() => {
    if (!job.data || ['QUEUED', 'RUNNING'].includes(job.data.status) || completed.current === job.data.job_id) return;
    completed.current = job.data.job_id;
    onComplete();
  }, [job.data, onComplete]);
  async function submit() {
    if (!permitted || busy || blocked || running || job.isFetching || job.isError || !confirmed) return;
    const snapshot = attempt ?? { actorId: user.id, ticketId, key: crypto.randomUUID() };
    setAttempt(snapshot); setBusy(true); setMessage('');
    const current = new AbortController(); controller.current = current;
    try {
      const result = await startAnalysis(snapshot, current.signal);
      if (current.signal.aborted) return;
      setAccepted(result); setAttempt(undefined); setConfirmed(false);
    } catch (error) {
      if (current.signal.aborted) return;
      setMessage(error instanceof Error ? error.message : '분석을 접수하지 못했습니다.');
      if (error instanceof AnalysisSessionChanged || error instanceof ApiError && [401, 403, 409].includes(error.status)) setBlocked(true);
      if (error instanceof ApiError && error.code === 'CS_JOB_IN_PROGRESS') setMessage('이 문의에 진행 중인 분석이 있습니다. 새 요청을 보내지 마세요. 기존 작업 번호를 모르면 현재 화면에서 진행 상태를 복원할 수 없습니다.');
      if (error instanceof ApiError && error.code === 'IDEMPOTENCY_CONFLICT') setMessage('같은 요청 키의 문의 입력이 변경되었습니다. 새 요청을 보내지 않고 최신 문의를 다시 확인하세요.');
      if (!(error instanceof AnalysisUncertain)) setAttempt(undefined);
    } finally { if (!current.signal.aborted) setBusy(false); }
  }
  return <div className="kv-sub" style={{ margin: '6px 14px' }} aria-label="AI 분석 접수">
    <p>AI 분석은 분류·검색·초안 작성에 모델 호출 비용이 듭니다. 결과는 담당자가 확인하며 고객에게 자동 발송하지 않습니다.</p>
    <label><input type="checkbox" checked={confirmed} disabled={!permitted || busy || running || blocked || job.isError} onChange={event => setConfirmed(event.target.checked)} />비용이 드는 {hasAnalysis ? '재분석' : '분석'}을 요청합니다</label>{' '}
    <button className="kb" disabled={!permitted || busy || running || blocked || job.isFetching || job.isError || !confirmed} onClick={() => void submit()}>{busy ? '접수 확인 중' : attempt ? '같은 분석 요청 다시 확인' : hasAnalysis ? 'AI 재분석 요청' : 'AI 분석 요청'}</button>
    {!permitted && <span> {closed ? '종결된 문의는 분석할 수 없습니다.' : '실제 API의 C/S 담당자만 요청할 수 있습니다.'}</span>}
    {message && <p role="alert">{message}</p>}
    {blocked && <p>현재 세션과 최신 문의를 다시 확인하세요. 자동으로 재접수하지 않습니다.</p>}
    {accepted && <p role="status">분석 작업 {accepted.job_id}: {status && STATUS[status]}</p>}
    {job.isError && <div role="alert">분석 상태를 읽지 못했습니다. 새 분석을 접수하지 않습니다. {job.error.message}{!(job.error instanceof AnalysisSessionChanged) && !(job.error instanceof ApiError && [401, 403, 404].includes(job.error.status)) && <button className="kb" onClick={() => void job.refetch()}>분석 상태 다시 조회</button>}</div>}
  </div>;
}
