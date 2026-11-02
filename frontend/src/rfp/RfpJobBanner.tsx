import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { RfpWatch } from '../api/rfp-job-events';
import type { DocumentVersion } from '../api/rfp';
import './RfpJobBanner.css';

const steps: Record<string, string> = { parse: '문서 읽기', extract: '요구 추출', search: '자사 자료 검색', compare: '자사 비교', supplement: '보완 경로 확인', candidates: '협력사 후보 확인', draft: '초안 작성', export: 'Word 내보내기', annotate: '보완 문서 처리' };
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
    if (!running || !view?.startedAt) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running, view?.startedAt]);
  const seconds = view?.startedAt ? Math.floor((now - Date.parse(view.startedAt)) / 1000) : -1;
  const elapsed = seconds >= 0 ? `${Math.floor(seconds / 60)}분 ${String(seconds % 60).padStart(2, '0')}초` : undefined;
  const clock = (stamp: string) => new Date(stamp).toLocaleTimeString('ko-KR', { hour12: false });
  return <section className={`rfp-job-banner${live ? ' is-live' : ''}${display.terminal ? ' is-terminal' : ''}${disconnected || stopped ? ' is-disconnected' : ''}`} aria-label="문서 처리 상태">
    <header className="rfp-analysis-heading">
      <div className="rfp-analysis-symbol" aria-hidden="true">{display.terminal ? status === 'SUCCEEDED' ? '✓' : '!' : stopped || disconnected ? '!' : '✦'}</div>
      <div className="rfp-analysis-title" role="status" aria-live="polite">
        <div className="rfp-analysis-eyebrow">RFP ANALYSIS <span className="rfp-analysis-badge">{display.terminal ? '처리 결과' : stopped ? '조회 중단' : disconnected ? '연결 확인 중' : live ? '분석 진행 중' : confirming ? '결과 확인 중' : running ? '상태 확인 중' : '시작 대기'}</span></div>
        <strong>{title}</strong>
        {!display.terminal && <p>{disconnected ? '실시간 연결 확인 중 · 작업 실패를 뜻하지 않습니다.' : live ? isAnalysis ? '문서의 요구사항과 대응 근거를 정리하고 있습니다. 실시간 상태 연결됨' : '요청한 작업을 처리하고 있습니다. 실시간 상태 연결됨' : display.headline}</p>}
      </div>
      {running && elapsed && <div className="rfp-analysis-elapsed"><span>작업 시작 후</span><b>{elapsed}</b>{disconnected && <small>진행 확인 중</small>}</div>}
      {display.terminal && <Link className="rfp-analysis-result" to={`/tft/projects/${projectId}/table`}>자사 대응표에서 결과 확인 →</Link>}
    </header>
    {view?.message && <p className="rfp-analysis-notice">{view.message}</p>}
    {docId ? <p className="rfp-analysis-document">관측 문서: {doc ? `${doc.title} v${doc.version}` : '문서 판본 이름을 확인 중입니다.'}</p>
      : requestDocuments.length > 0 && <p className="rfp-analysis-document">요청 문서: {requestDocuments.map(id => { const d = documents.find(v => v.document_version_id === id); return d ? `${d.title} v${d.version}` : '문서 판본 확인 중'; }).join(', ')}</p>}
    {!display.terminal && !stopped && isAnalysis && <ol className="rfp-analysis-stages" aria-label="분석 단계별 관측 상태">
      {pipeline.map((step, i) => {
        const seen = activity.some(item => item.step === step);
        const recent = latest?.step === step;
        return <li key={step} className={recent ? 'is-current' : seen ? 'is-observed' : ''} aria-current={recent ? 'step' : undefined}>
          <span className="rfp-stage-number" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
          <span><b>{steps[step]}</b><small>{recent ? disconnected ? '마지막 수신 단계' : '최근 확인' : seen ? '관측됨' : '아직 관측 안 됨'}</small></span>
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
