import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { RfpWatch } from '../api/rfp-job-events';
import type { DocumentVersion } from '../api/rfp';
import './RfpJobBanner.css';

const steps: Record<string, string> = { parse: '문서 읽기', extract: '요구 추출', search: '자사 자료 검색', compare: '자사 비교', supplement: '보완 경로 확인', candidates: '협력사 후보 확인', draft: '초안 작성', export: 'Word 내보내기', annotate: '보완 문서 처리' };
const statuses: Record<string, string> = { QUEUED: '처리 요청을 접수했습니다.', RUNNING: '작업이 시작됐습니다.', SUCCEEDED: '처리가 완료됐습니다.', PARTIAL: '일부 처리가 완료됐습니다. 결과 확인이 필요합니다.', FAILED: '처리에 실패했습니다. 입력과 결과를 확인하세요.', CANCELLED: '처리가 취소됐습니다.' };
// Minimum reading time for bursts, not estimated processing durations. Never advance
// beyond an observed event. Time already spent on a slow stage counts toward its minimum.
const readingTime: Record<string, number> = { parse: 1100, extract: 2200, search: 1000, compare: 1800, supplement: 1400, candidates: 1200, draft: 2200 };
// Presentation only: observed events never mutate the authoritative GET snapshot.
export function rfpJobPresentation(status: string | undefined, view?: RfpWatch, accepted = false) {
  const terminal = !!status && !['QUEUED', 'RUNNING'].includes(status);
  if (view?.connection === 'stopped') return { headline: '작업 상태 조회가 중단됐습니다.', row: '조회 불가', terminal: false };
  if (!terminal && ['job.completed', 'job.failed'].includes(view?.progress?.type ?? '')) return { headline: '작업 종료 신호를 받았습니다. 저장된 결과를 확인하고 있습니다.', row: '결과 확인 중', terminal: false };
  if (!terminal && ['job.started', 'job.progress'].includes(view?.progress?.type ?? '')) return { headline: '작업 진행이 관측됐습니다.', row: '실행 중 (최근 서버 관측)', terminal: false };
  const rows: Record<string, string> = { QUEUED: '대기', RUNNING: '실행 중', SUCCEEDED: '완료', PARTIAL: '일부 완료', FAILED: '실패', CANCELLED: '취소' };
  return { headline: status ? statuses[status] : accepted ? '처리 요청을 접수했습니다. 작업 시작을 확인하고 있습니다.' : '작업 상태를 확인하고 있습니다.', row: status ? rows[status] : '확인 중', terminal };
}
export function RfpJobBanner({ view, status, accepted, documents, projectId, requestDocuments = [] }: {
  view?: RfpWatch; status?: string; accepted: boolean; documents: DocumentVersion[]; projectId: string; requestDocuments?: string[];
}) {
  const p = view?.progress;
  const display = rfpJobPresentation(status, view, accepted);
  const disconnected = view?.connection === 'fallback' || view?.connection === 'reconnecting';
  const stopped = view?.connection === 'stopped';
  const confirming = !display.terminal && ['job.completed', 'job.failed'].includes(p?.type ?? '');
  const running = !display.terminal && !stopped && !confirming && (status === 'RUNNING' || ['job.started', 'job.progress'].includes(p?.type ?? ''));
  const live = running && view?.connection === 'connected';
  const activity = stopped ? [] : view?.activity ?? (p?.step ? [p] : []);
  // Pace only newly received observations; an already completed job opens without a fake wait.
  const [visibleCount, setVisibleCount] = useState(activity.length);
  const [finishingPlayback, setFinishingPlayback] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false);
  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!media) return;
    const change = () => setReducedMotion(media.matches);
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, []);
  const failed = status === 'FAILED' || status === 'CANCELLED';
  // The stream ending/reconnecting is not the job ending. Keep motion through draft
  // and committed-result confirmation, but never continue on denial or known failure.
  const processing = !stopped && !failed && p?.type !== 'job.failed' && (running || confirming);
  const paced = !stopped && !disconnected && !failed && !reducedMotion;
  const displayedActivity = paced ? activity.slice(0, Math.max(1, visibleCount)) : activity;
  const playing = paced && displayedActivity.length < activity.length;
  const shownLatest = displayedActivity.at(-1);
  const shownSince = useRef({ step: shownLatest?.step, at: Date.now() });
  useEffect(() => {
    if (shownSince.current.step !== shownLatest?.step) shownSince.current = { step: shownLatest?.step, at: Date.now() };
  }, [shownLatest?.step]);
  useEffect(() => {
    if (!playing) return;
    const remaining = Math.max(0, (readingTime[shownLatest?.step ?? ''] ?? 1100) - (Date.now() - shownSince.current.at));
    const timer = window.setTimeout(() => {
      const next = Math.max(1, visibleCount) + 1;
      setVisibleCount(next);
      if (next >= activity.length) setFinishingPlayback(true);
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [playing, visibleCount, activity.length, shownLatest?.step]);
  useEffect(() => {
    if (!finishingPlayback) return;
    if (!paced) { setFinishingPlayback(false); return; }
    const remaining = Math.max(0, (readingTime[shownLatest?.step ?? ''] ?? 1100) - (Date.now() - shownSince.current.at));
    const timer = window.setTimeout(() => setFinishingPlayback(false), remaining);
    return () => window.clearTimeout(timer);
  }, [finishingPlayback, paced, shownLatest?.step]);
  const presenting = playing || paced && finishingPlayback;
  // Real completion and the visual replay are separate: results stay available while
  // the ring accompanies the remaining observed cards, including the final draft card.
  const replaying = display.terminal && presenting;
  const latest = activity.at(-1);
  const counts = [...activity].reverse().find(item => item.counts)?.counts;
  const docId = p?.documentVersionId ?? latest?.documentVersionId;
  const doc = documents.find(d => d.document_version_id === docId);
  const pipeline = ['parse', 'extract', 'search', 'compare', 'supplement', 'candidates', 'draft'];
  const kind = view?.job?.kind;
  const isAnalysis = kind ? kind === 'rfp.process' : !['export', 'annotate'].includes(p?.step ?? '');
  const title = stopped ? '처리 상태를 확인할 수 없습니다' : disconnected && !display.terminal ? '분석 상태를 다시 확인하고 있습니다' : confirming ? '분석 결과를 확인하고 있습니다' : running ? isAnalysis ? 'AI가 RFP를 분석하고 있습니다' : steps[p?.step ?? ''] ?? '작업을 처리하고 있습니다' : display.terminal ? display.headline : '분석을 준비하고 있습니다';
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!processing || !view?.startedAt) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [processing, view?.startedAt]);
  const seconds = view?.startedAt ? Math.floor((now - Date.parse(view.startedAt)) / 1000) : -1;
  const elapsed = seconds >= 0 ? `${Math.floor(seconds / 60)}분 ${String(seconds % 60).padStart(2, '0')}초` : undefined;
  const clock = (stamp: string) => new Date(stamp).toLocaleTimeString('ko-KR', { hour12: false });
  return <section className={`rfp-job-banner${live ? ' is-live' : ''}${processing ? ' is-processing' : ''}${replaying ? ' is-replaying' : ''}${display.terminal ? ' is-terminal' : ''}${disconnected || stopped ? ' is-disconnected' : ''}`} aria-label="문서 처리 상태">
    <header className="rfp-analysis-heading">
      <div className="rfp-analysis-symbol" aria-hidden="true">{display.terminal ? status === 'SUCCEEDED' ? '✓' : '!' : stopped || disconnected ? '!' : '✦'}</div>
      <div className="rfp-analysis-title" role="status" aria-live="polite">
        <div className="rfp-analysis-eyebrow">RFP ANALYSIS <span className="rfp-analysis-badge">{display.terminal ? replaying ? '완료 · 과정 표시 중' : '처리 결과' : stopped ? '조회 중단' : disconnected ? '연결 확인 중' : live ? '분석 진행 중' : confirming ? '결과 확인 중' : running ? '상태 확인 중' : '시작 대기'}</span></div>
        <strong>{title}</strong>
        {!display.terminal && <p>{disconnected ? '실시간 연결 확인 중 · 작업 실패를 뜻하지 않습니다.' : live ? isAnalysis ? '문서의 요구사항과 대응 근거를 정리하고 있습니다. 실시간 상태 연결됨' : '요청한 작업을 처리하고 있습니다. 실시간 상태 연결됨' : display.headline}</p>}
      </div>
      {processing && elapsed && <div className="rfp-analysis-elapsed"><span>작업 시작 후</span><b>{elapsed}</b>{disconnected && <small>진행 확인 중</small>}</div>}
      {display.terminal && <Link className="rfp-analysis-result" to={`/tft/projects/${projectId}/table`}>자사 대응표에서 결과 확인 →</Link>}
    </header>
    {view?.message && <p className="rfp-analysis-notice">{view.message}</p>}
    {docId ? <p className="rfp-analysis-document">관측 문서: {doc ? `${doc.title} v${doc.version}` : '문서 판본 이름을 확인 중입니다.'}</p>
      : requestDocuments.length > 0 && <p className="rfp-analysis-document">요청 문서: {requestDocuments.map(id => { const d = documents.find(v => v.document_version_id === id); return d ? `${d.title} v${d.version}` : '문서 판본 확인 중'; }).join(', ')}</p>}
    {!stopped && isAnalysis && activity.length > 0 && <div className="rfp-analysis-playback">
      <span>{display.terminal ? presenting ? '완료된 분석 과정 다시보기 · 결과는 지금 열 수 있습니다' : '완료된 분석 과정' : presenting ? '수신한 분석 단계를 순서대로 표시합니다' : '서버에서 관측된 분석 단계'}</span>
      {presenting ? <button className="kb" onClick={() => { setFinishingPlayback(false); setVisibleCount(activity.length); }}>한 번에 보기</button>
        : display.terminal && !failed && !reducedMotion && activity.length > 1 && <button className="kb" onClick={() => { setFinishingPlayback(false); setVisibleCount(0); }}>과정 다시보기</button>}
    </div>}
    {!stopped && isAnalysis && (!display.terminal || activity.length > 0) && <ol className="rfp-analysis-stages" aria-label="분석 단계별 관측 상태">
      {pipeline.map((step, i) => {
        const seen = displayedActivity.some(item => item.step === step);
        const recent = shownLatest?.step === step;
        return <li key={step} className={recent ? 'is-current' : seen ? 'is-observed' : ''} aria-current={recent ? 'step' : undefined}>
          <span className="rfp-stage-number" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
          <span><b>{steps[step]}</b><small>{recent ? disconnected ? '마지막 수신 단계' : playing || display.terminal ? '과정 보기' : '최근 확인' : seen ? '관측됨' : activity.some(item => item.step === step) ? '표시 대기' : '아직 관측 안 됨'}</small></span>
        </li>;
      })}
    </ol>}
    {!display.terminal && !stopped && <div className="rfp-analysis-observation">
      <div><span className="rfp-observation-dot" aria-hidden="true" /><b>{latest?.step ? `최근 확인 단계: ${steps[latest.step]}` : running ? '작업 시작 확인 · 첫 단계 소식을 기다립니다' : '작업 시작을 기다립니다'}</b>{p && <time dateTime={p.occurredAt}>서버 기록 {clock(p.occurredAt)}</time>}</div>
      {counts && <p>누적 발견 수(임시): <strong>요구 {counts.requirements}건</strong> · 제안 {counts.proposals}건. 저장된 최종 결과와 다를 수 있습니다.</p>}
    </div>}
    {activity.length > 0 && !stopped && <details className="rfp-analysis-history"><summary>분석 활동 내역 <span>{activity.length}개 단계 관측</span></summary>
      <ol>{[...activity].reverse().map(item => <li key={item.step}><time dateTime={item.occurredAt}>{clock(item.occurredAt)}</time><b>{steps[item.step!]}</b><span>{item.counts ? `요구 ${item.counts.requirements}건 · 제안 ${item.counts.proposals}건 발견 (임시)` : '단계 이벤트 수신'}</span></li>)}</ol>
      <p>각 단계의 최근 수신 기록입니다. 관측된 단계가 저장 완료를 뜻하지는 않습니다.</p>
    </details>}
    {p?.needsReview && <p className="rfp-analysis-notice">결과에 사람의 검토가 필요합니다.</p>}
    {display.terminal && <p className="rfp-analysis-footnote">완료는 담당자 승인이 아닙니다. 대응표의 미해결 요구와 근거를 확인하세요.</p>}
  </section>;
}
