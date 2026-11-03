import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { RfpWatch } from '../api/rfp-job-events';
import type { DocumentVersion } from '../api/rfp';
import { RfpPersona } from './RfpPersona';
import { RfpReasoning, rfpStageDescription } from './RfpReasoning';
import './RfpJobBanner.css';

const steps: Record<string, string> = { parse: '문서 읽기', extract: '요구 추출', search: '자사 자료 검색', compare: '자사 비교', supplement: '보완 경로 확인', candidates: '협력사 후보 확인', draft: '초안 작성', export: 'Word 내보내기', annotate: '보완 문서 처리', context_map:'설비·과업 맥락 정리', context_link:'요구·근거 연결', context_validate:'맥락 정리 검증' };
const statuses: Record<string, string> = { QUEUED: '처리 요청을 접수했습니다.', RUNNING: '작업이 시작됐습니다.', SUCCEEDED: '처리가 완료됐습니다.', PARTIAL: '일부 처리가 완료됐습니다. 결과 확인이 필요합니다.', FAILED: '처리에 실패했습니다. 입력과 결과를 확인하세요.', CANCELLED: '처리가 취소됐습니다.' };
// Presentation only: observed events never mutate the authoritative GET snapshot.
export function rfpJobPresentation(status: string | undefined, view?: RfpWatch, accepted = false) {
  const terminal = !!status && !['QUEUED', 'RUNNING'].includes(status);
  if (view?.connection === 'stopped') return { headline: '작업 상태 조회가 중단됐습니다.', row: '조회 불가', terminal: false };
  if (!terminal && ['job.completed', 'job.failed'].includes(view?.progress?.type ?? '')) return { headline: '작업 종료 신호를 받았습니다. 저장된 결과를 확인하고 있습니다.', row: '결과 확인 중', terminal: false };
  if (!terminal && ['job.started', 'job.progress'].includes(view?.progress?.type ?? '')) return { headline: '작업 진행이 관측됐습니다.', row: '실행 중 (최근 서버 관측)', terminal: false };
  const rows: Record<string, string> = { QUEUED: '대기', RUNNING: '실행 중', SUCCEEDED: '완료', PARTIAL: '일부 완료', FAILED: '실패', CANCELLED: '취소' };
  return { headline: status ? statuses[status] : accepted ? '처리 요청을 접수했습니다. 작업 시작을 확인하고 있습니다.' : '작업 상태를 확인하고 있습니다.', row: status ? rows[status] : '확인 중', terminal };
}
export function RfpJobBanner({ view, status, accepted, documents, projectId, requestDocuments = [], idle = false }: {
  view?: RfpWatch; status?: string; accepted: boolean; documents: DocumentVersion[]; projectId: string; requestDocuments?: string[]; idle?: boolean;
}) {
  const p = view?.progress;
  const display = idle ? { headline: documents.length ? '처리 실행을 누르면 문서 분석을 시작합니다.' : '문서를 올리면 분석을 시작할 수 있습니다.', row: '대기', terminal: false } : rfpJobPresentation(status, view, accepted);
  const disconnected = view?.connection === 'fallback' || view?.connection === 'reconnecting';
  const stopped = view?.connection === 'stopped';
  const confirming = !display.terminal && ['job.completed', 'job.failed'].includes(p?.type ?? '');
  const running = !display.terminal && !stopped && !confirming && (status === 'RUNNING' || ['job.started', 'job.progress'].includes(p?.type ?? ''));
  const live = running && view?.connection === 'connected';
  const activity = stopped ? [] : view?.activity ?? (p?.step ? [p] : []);
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
  const latest = activity.at(-1);
  // One observed source drives the current card, Persona and history label. A terminal
  // signal without a step retains the latest stage until GET confirms the result.
  const current = p?.step && (!latest || p.sequence >= latest.sequence) ? p : latest;
  const actualStage = current?.step;
  const haloState = processing ? actualStage === 'draft' ? 'speaking' : 'thinking' : 'idle';
  const haloPaused = stopped || failed || p?.type === 'job.failed' || display.terminal || disconnected || reducedMotion;
  const haloLabel = stopped ? '분석 상태 확인 중' : failed ? '분석 중단' : display.terminal ? '분석 완료' : p?.type === 'job.failed' ? '분석 중단' : disconnected ? '분석 연결 확인 중'
    : `${haloState === 'speaking' ? 'AI 초안 작성 중' : haloState === 'thinking' ? 'AI 분석 중' : 'AI 대기 중'}`;
  const counts = current?.counts ?? [...activity].reverse().find(item => item.counts)?.counts;
  const docId = current?.documentVersionId;
  const doc = documents.find(d => d.document_version_id === docId);
  const drawingOnly = documents.length > 0 && documents.every(document => document.kind === 'DRAWING');
  const kind = view?.job?.kind;
  const isContext = kind==='rfp.context' || actualStage?.startsWith('context_');
  const isAnalysis = kind ? kind === 'rfp.process' : !['export', 'annotate'].includes(p?.step ?? '');
  const title = idle ? 'RFP 분석 대기 중' : stopped ? '처리 상태를 확인할 수 없습니다' : disconnected && !display.terminal ? '분석 상태를 다시 확인하고 있습니다' : confirming ? '분석 결과를 확인하고 있습니다' : running ? isContext ? 'AI가 요구를 맥락별로 정리하고 있습니다' : isAnalysis ? haloState === 'speaking' ? 'AI가 RFP 초안을 작성하고 있습니다' : 'AI가 RFP를 분석하고 있습니다' : steps[p?.step ?? ''] ?? '작업을 처리하고 있습니다' : display.terminal ? display.headline : '분석을 준비하고 있습니다';
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
  return <section className={`rfp-job-banner${live ? ' is-live' : ''}${processing ? ' is-processing' : ''}${display.terminal ? ' is-terminal' : ''}${disconnected || stopped ? ' is-disconnected' : ''}`} aria-label="문서 처리 상태">
    <header className="rfp-analysis-heading">
      <RfpPersona state={haloState} paused={haloPaused} label={haloLabel} completed={display.terminal && !failed} />
      <div className="rfp-analysis-title" role="status" aria-live="polite">
        <div className="rfp-analysis-eyebrow">RFP ANALYSIS <span className="rfp-analysis-badge">{display.terminal ? '처리 결과' : stopped ? '조회 중단' : disconnected ? '연결 확인 중' : live ? haloState === 'speaking' ? '초안 작성 중' : '분석 진행 중' : confirming ? '결과 확인 중' : running ? '상태 확인 중' : '시작 대기'}</span></div>
        <strong key={title} className="rfp-stage-caption">{title}</strong>
        {!display.terminal && <p>{disconnected ? '실시간 연결 확인 중 · 작업 실패를 뜻하지 않습니다.' : live ? isAnalysis ? haloState === 'speaking' ? '요구사항과 대응 근거를 초안으로 정리하고 있습니다. 실시간 상태 연결됨' : '문서의 요구사항과 대응 근거를 정리하고 있습니다. 실시간 상태 연결됨' : '요청한 작업을 처리하고 있습니다. 실시간 상태 연결됨' : display.headline}</p>}
      </div>
      {processing && elapsed && <div className="rfp-analysis-elapsed"><span>작업 시작 후</span><b>{elapsed}</b>{disconnected && <small>진행 확인 중</small>}</div>}
      {display.terminal && (drawingOnly
        ? <a className="rfp-analysis-result" href="#rfp-document-review">도면 분석 결과 확인 →</a>
        : <Link className="rfp-analysis-result" to={`/tft/projects/${projectId}/table`}>대응 방안 검토에서 결과 확인 →</Link>)}
    </header>
    {view?.message && <p className="rfp-analysis-notice">{view.message}</p>}
    {docId ? <p className="rfp-analysis-document">관측 문서: {doc ? `${doc.title} v${doc.version}` : '문서 판본 이름을 확인 중입니다.'}</p>
      : requestDocuments.length > 0 && <p className="rfp-analysis-document">요청 문서: {requestDocuments.map(id => { const d = documents.find(v => v.document_version_id === id); return d ? `${d.title} v${d.version}` : '문서 판본 확인 중'; }).join(', ')}</p>}
    {!idle && !stopped && <div className="rfp-analysis-current" aria-label="현재 분석 상태" aria-live="polite" aria-atomic="true" data-step={actualStage}>
      <div className="rfp-current-copy rfp-stage-caption" key={`${display.terminal ? status : confirming ? 'confirming' : actualStage ?? 'waiting'}`}>
        <span className="rfp-current-label">{display.terminal ? '처리 결과' : disconnected ? '마지막 수신 단계' : confirming ? '결과 확인 중' : '최근 확인 단계'}</span>
        <strong>{display.terminal ? display.headline : confirming ? '저장된 결과를 확인하고 있습니다' : actualStage ? steps[actualStage] ?? '작업 상태 확인' : running ? '작업 시작 확인 · 첫 단계 소식을 기다립니다' : '작업 시작을 기다립니다'}</strong>
        <p>{display.terminal ? '분석 과정은 아래 작업 내역에서 확인할 수 있습니다.' : confirming ? '종료 신호를 받았습니다. 최종 상태를 확인한 뒤 결과를 표시합니다.' : current ? rfpStageDescription(actualStage) : '서버에서 보낸 상태를 수신하면 이곳에 표시합니다.'}</p>
      </div>
      <div className="rfp-current-meta">{current && <time dateTime={current.occurredAt}>서버 기록 {clock(current.occurredAt)}</time>}
        {counts && !display.terminal && <span>누적 발견 수(임시): 요구 {counts.requirements}건 · 제안 {counts.proposals}건</span>}
      </div>
    </div>}
    {activity.length > 0 && !stopped && <RfpReasoning activity={activity} active={live} completed={display.terminal} disconnected={disconnected} currentStep={actualStage} documents={documents} labels={steps} />}
    {p?.needsReview && <p className="rfp-analysis-notice">결과에 사람의 검토가 필요합니다.</p>}
    {display.terminal && <p className="rfp-analysis-footnote">{drawingOnly ? '완료는 담당자 승인이 아닙니다. 도면 원본과 연결 후보를 대조해 확인하세요.' : '완료는 담당자 승인이 아닙니다. 대응표의 미해결 요구와 근거를 확인하세요.'}</p>}
  </section>;
}
