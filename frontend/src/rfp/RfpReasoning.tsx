import { useId, useState } from 'react';
import type { RfpProgress } from '../api/rfp-job-events';
import type { DocumentVersion } from '../api/rfp';
import './RfpReasoning.css';

const descriptions: Record<string, string> = {
  context_map:'설비와 업무별로 고객 요구사항을 정리하는 단계입니다.',
  context_link:'각 요구사항이 적용되는 대상과 원문 근거를 확인하는 단계입니다.',
  context_validate:'정리한 요구에 누락이나 충돌이 있는지 확인하는 단계입니다.',
  parse: '문서의 글과 이미지 영역을 확인하는 단계가 기록됐습니다.',
  extract: '문서에서 요구사항을 추출하는 단계가 기록됐습니다.',
  search: '자사 자료 검색 단계가 기록됐습니다.',
  compare: '자사 장비와 요구조건을 비교하는 단계가 기록됐습니다.',
  supplement: '대응이 필요한 요구의 보완 경로를 확인하는 단계가 기록됐습니다.',
  candidates: '협력사 후보를 확인하는 단계가 기록됐습니다.',
  draft: '제안 초안 작성 단계가 기록됐습니다. 최종 저장 상태는 별도로 확인합니다.',
  export: 'Word 내보내기 단계가 기록됐습니다.',
  annotate: '보완 문서 처리 단계가 기록됐습니다.',
};

export function rfpStageDescription(step?: string) {
  return descriptions[step ?? ''] ?? '서버에서 작업 상태를 수신했습니다.';
}

/** Public worker observations only: no model reasoning text and no synthetic progress. */
export function RfpReasoning({ activity, active, completed, disconnected, currentStep, documents, labels }: {
  activity: RfpProgress[]; active: boolean; completed: boolean; disconnected: boolean;
  currentStep?: string; documents: DocumentVersion[]; labels: Record<string, string>;
}) {
  // Start compact; preserve the person's choice across events and completion.
  const [open, setOpen] = useState(false);
  const contentId = useId();
  const latest = activity.at(-1);
  const observedStep = currentStep ?? latest?.step;
  const summary = completed ? '처리 결과와 함께 작업 기록을 확인하세요.'
    : disconnected ? '연결을 다시 확인하고 있습니다. 마지막 기록을 보존합니다.'
    : active ? '서버에서 보내온 작업 소식을 확인하고 있습니다.'
    : '최근에 수신한 작업 기록입니다.';
  return <section className={`rfp-reasoning rfp-analysis-history${active ? ' is-active' : ''}`} aria-label="AI 작업 내역">
    <button type="button" className="rfp-reasoning-trigger" aria-expanded={open} aria-controls={contentId} onClick={() => setOpen(value => !value)}>
      <span className="rfp-reasoning-chevron" aria-hidden="true">›</span>
      <span className="rfp-reasoning-label">AI 작업 내역</span>
      <span className="rfp-reasoning-count">{activity.length}개 단계 관측</span>
      {observedStep && <span className="rfp-reasoning-latest">{labels[observedStep]}</span>}
    </button>
    <p className="rfp-reasoning-summary">{summary}</p>
    <div id={contentId} className="rfp-reasoning-content" data-open={open} aria-hidden={!open} inert={!open}>
      <div className="rfp-reasoning-body">
        <ol>{activity.map(item => {
          const document = documents.find(row => row.document_version_id === item.documentVersionId);
          return <li key={item.step} className={item === latest && active ? 'is-latest' : undefined}>
            <span className="rfp-reasoning-marker" aria-hidden="true" />
            <div className="rfp-reasoning-entry">
              <div className="rfp-reasoning-entry-heading"><b>{labels[item.step ?? ''] ?? '작업 상태'}</b><time dateTime={item.occurredAt}>{new Date(item.occurredAt).toLocaleTimeString('ko-KR', { hour12: false })}</time></div>
              <p>{rfpStageDescription(item.step)}</p>
              {item.documentVersionId && <small>관측 문서: {document ? `${document.title} v${document.version}` : '문서 판본 이름 확인 중'}</small>}
              {item.counts && <small>요구 {item.counts.requirements}건 · 제안 {item.counts.proposals}건 발견 (임시)</small>}
            </div>
          </li>;
        })}</ol>
        <p className="rfp-reasoning-footnote">서버가 보낸 각 단계의 최근 기록입니다. AI의 내부 추론이 아니며, 단계 관측만으로 저장 완료를 뜻하지 않습니다.</p>
      </div>
    </div>
  </section>;
}
