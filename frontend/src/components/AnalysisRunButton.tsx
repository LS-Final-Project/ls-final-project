import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { AnalysisUncertain, getAnalysisJob, startAnalysis, type AnalysisAttempt } from '../api/cs-analysis';
import { ElloMark } from './Ello';

type Note = (kind: 'ok' | 'error', text: string) => void;

/**
 * "AI 분석 실행" 버튼 하나. 누르면 바로 분석 작업을 접수하고(모델 호출 비용), 끝날 때까지 상태를 읽은 뒤 onComplete를 부른다.
 * 실행 중에는 다시 누를 수 없다. 접수 결과를 모르면(AnalysisUncertain) 같은 요청 키로만 다시 확인한다.
 */
export type RunPhase = 'idle' | 'sending' | 'running';

export default function AnalysisRunButton({ actorId, ticketId, disabled, title, onComplete, onNote, onPhase }: {
  actorId: string; ticketId: string; disabled?: boolean; title?: string; onComplete: () => void; onNote: Note;
  // 실행 단계와 시작 시각. 화면이 이 실행의 결과가 도착했는지 가르는 데 쓴다.
  onPhase?: (phase: RunPhase, startedAt: number | null) => void;
}) {
  const [phase, setPhaseState] = useState<RunPhase>('idle');
  const startedAt = useRef<number | null>(null);
  const setPhase = (next: RunPhase) => { setPhaseState(next); if (next === 'sending') startedAt.current = Date.now(); onPhase?.(next, next === 'idle' ? null : startedAt.current); };
  const pending = useRef<AnalysisAttempt | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

  async function run() {
    if (phase !== 'idle') return;
    const attempt = pending.current ?? { actorId, ticketId, key: crypto.randomUUID() };
    pending.current = attempt;
    const current = new AbortController(); controller.current = current;
    setPhase('sending');
    try {
      const accepted = await startAnalysis(attempt, current.signal);
      pending.current = null;
      setPhase('running');
      for (let i = 0; i < 600; i++) {
        const job = await getAnalysisJob(actorId, ticketId, accepted.job_id, current.signal);
        if (!['QUEUED', 'RUNNING'].includes(job.status)) {
          onNote(job.status === 'SUCCEEDED' ? 'ok' : 'error', job.status === 'SUCCEEDED' ? 'AI 분석을 마쳤습니다. 분류와 초안을 다시 읽었습니다.'
            : job.status === 'PARTIAL' ? 'AI 분석이 일부만 끝났습니다. 결과를 확인하세요.' : 'AI 분석이 실패했습니다. 잠시 뒤 다시 실행하세요.');
          onComplete();
          return;
        }
        await sleep(1000);
      }
      onNote('error', 'AI 분석이 오래 걸리고 있습니다. 잠시 뒤 "최신 상태 조회"로 결과를 확인하세요.');
    } catch (error) {
      if (current.signal.aborted) return;
      if (!(error instanceof AnalysisUncertain)) pending.current = null;
      onNote('error', error instanceof ApiError && error.code === 'CS_JOB_IN_PROGRESS' ? '이 문의에 이미 진행 중인 분석이 있습니다. 끝난 뒤 다시 실행하세요.'
        : error instanceof AnalysisUncertain ? '분석 접수 결과를 확인하지 못했습니다. 다시 누르면 같은 요청으로 확인합니다(새 비용 없음).'
        : error instanceof Error ? error.message : '분석을 접수하지 못했습니다.');
    } finally {
      if (!current.signal.aborted) setPhase('idle');
    }
  }

  return <button className="kb3 sm" disabled={disabled || phase !== 'idle'} title={title ?? '분류·검색·초안을 다시 만듭니다. 모델 호출 비용이 듭니다'} onClick={() => void run()}>
    {phase !== 'sending' && <ElloMark />}{phase === 'sending' ? '접수 중…' : phase === 'running' ? '분석 중…' : 'AI 분석 실행'}
  </button>;
}
