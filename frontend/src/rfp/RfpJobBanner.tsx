import { Link } from 'react-router-dom';
import type { RfpWatch } from '../api/rfp-job-events';
import type { DocumentVersion } from '../api/rfp';
import './RfpJobBanner.css';

const steps: Record<string, string> = { parse: '문서 읽기', extract: '요구 추출', search: '자사 자료 검색', compare: '자사 비교', supplement: '보완 경로 확인', candidates: '협력사 후보 확인', draft: '초안 작성', export: 'Word 내보내기', annotate: '보완 문서 처리' };
const statuses: Record<string, string> = { QUEUED: '처리 요청을 접수했습니다.', RUNNING: '작업이 시작됐습니다.', SUCCEEDED: '처리가 완료됐습니다.', PARTIAL: '일부 처리가 완료됐습니다. 결과 확인이 필요합니다.', FAILED: '처리에 실패했습니다. 입력과 결과를 확인하세요.', CANCELLED: '처리가 취소됐습니다.' };
export function RfpJobBanner({ view, status, accepted, documents, projectId, requestDocuments = [] }: {
  view?: RfpWatch; status?: string; accepted: boolean; documents: DocumentVersion[]; projectId: string; requestDocuments?: string[];
}) {
  const p = view?.progress;
  const doc = documents.find(d => d.document_version_id === p?.documentVersionId);
  const disconnected = view?.connection === 'fallback' || view?.connection === 'reconnecting';
  const terminal = status && !['QUEUED', 'RUNNING'].includes(status);
  const observedStart = p?.type === 'job.started' || p?.type === 'job.progress';
  const terminalSignal = p?.type === 'job.completed' || p?.type === 'job.failed';
  return <section className="rfp-job-banner" aria-label="문서 처리 상태">
    <div role="status" aria-live="polite">
      <strong>{view?.connection === 'stopped' ? '작업 상태 조회가 중단됐습니다.' : !terminal && terminalSignal ? '작업 종료 신호를 받았습니다. 저장된 결과를 확인하고 있습니다.' : !terminal && observedStart ? '작업 진행이 관측됐습니다.' : status ? statuses[status] : accepted ? '처리 요청을 접수했습니다. 작업 시작을 확인하고 있습니다.' : '작업 상태를 확인하고 있습니다.'}</strong>
      {disconnected && <span> 실시간 연결 확인 중 · 작업 실패를 뜻하지 않습니다.</span>}
      {view?.connection === 'connected' && <span> 실시간 상태 연결됨</span>}
    </div>
    {view?.message && <p>{view.message}</p>}
    {p && <p>{p.step && <>최근 확인 단계: {steps[p.step]} · </>}서버 기록 시각 <time dateTime={p.occurredAt}>{new Date(p.occurredAt).toLocaleString('ko-KR')}</time>{p.type === 'job.started' && ' · 작업 시작 확인'}</p>}
    {p?.documentVersionId ? <p>관측 문서: {doc ? `${doc.title} v${doc.version}` : '문서 판본 이름을 확인 중입니다.'}</p>
      : requestDocuments.length > 0 && <p>요청 문서: {requestDocuments.map(id => { const d = documents.find(v => v.document_version_id === id); return d ? `${d.title} v${d.version}` : '문서 판본 확인 중'; }).join(', ')}</p>}
    {p?.counts && <p>누적 발견 수(임시): 요구 {p.counts.requirements}건 · 제안 {p.counts.proposals}건. 저장된 최종 결과와 다를 수 있습니다.</p>}
    {p?.needsReview && <p>결과에 사람의 검토가 필요합니다.</p>}
    {terminal && view?.connection !== 'stopped' && <p><Link to={`/tft/projects/${projectId}/table`}>자사 대응표에서 결과 확인</Link> · 완료는 담당자 승인이 아닙니다.</p>}
  </section>;
}
