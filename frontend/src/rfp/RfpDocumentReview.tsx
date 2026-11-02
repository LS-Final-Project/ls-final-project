import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getRequirement, listRequirements, type DocumentVersion, type RequirementDetail } from '../api/rfp';
import { ErrorState, Loading } from '../components/states';
import { latestVersions } from './IntakeForms';
import { attributeName, label, NECESSITY_LABEL, OPERATOR_LABEL, PATH_LABEL, PROVENANCE_LABEL, STATUS_LABEL, requirementNo } from './labels';
import { OriginalDocument } from './OriginalDocument';
import './RfpDocumentReview.css';

export function RfpDocumentReview({ projectId, actorId, documents }: { projectId: string; actorId?: string; documents: DocumentVersion[] }) {
  const [picked, setPicked] = useState<string>();
  const versions = latestVersions(documents);
  const document = versions.find(d => d.document_version_id === picked) ?? versions.find(d => d.kind === 'RFP_SPEC') ?? versions[0];
  if (!document) return <div className="rfp-review-empty">문서를 올리면 원문과 분석 결과를 나란히 확인할 수 있습니다.</div>;
  return <section className="rfp-document-review" aria-label="원문과 분석 검토">
    <div className="rfp-review-heading"><div><h2>원문과 분석 결과</h2><p>요구를 선택하면 원문의 인용 위치와 추출·판단 내용을 함께 보여줍니다.</p></div>
      <label>검토 문서<select aria-label="검토 문서" value={document.document_version_id} onChange={e => setPicked(e.target.value)}>{versions.map(d => <option key={d.document_version_id} value={d.document_version_id}>{d.title} · v{d.version}</option>)}</select></label>
    </div>
    <DocumentAnalysis key={`${actorId}:${projectId}:${document.document_version_id}`} projectId={projectId} actorId={actorId} document={document} />
  </section>;
}

function DocumentAnalysis({ projectId, actorId, document }: { projectId: string; actorId?: string; document: DocumentVersion }) {
  const mode = getApiMode();
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState<string>();
  const [filter, setFilter] = useState(false);
  const requirements = useInfiniteQuery({
    queryKey: ['rfp-requirements', mode, projectId, actorId, 'document-review', document.document_version_id, filter],
    queryFn: ({ pageParam }) => listRequirements(projectId, { document_version_id: document.document_version_id, ...(filter ? { unresolved_mandatory: true } : {}) }, 10, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: last => last.data.next_cursor ?? undefined,
    retry: false,
  });
  const rows = requirements.data?.pages[page]?.data.items ?? [];
  const id = rows.find(r => r.requirement_id === picked)?.requirement_id ?? rows[0]?.requirement_id;
  const detail = useQuery({ queryKey: ['rfp-requirement', mode, actorId, projectId, id], queryFn: () => getRequirement(id!), enabled: !!id, retry: false });
  // Never highlight stale detail from another document/version after navigation or invalidation.
  const selected = !!id && detail.data?.data.requirement_id === id && detail.data.data.origin.document_version_id === document.document_version_id ? detail.data.data : undefined;
  useEffect(() => { if (requirements.data && page >= requirements.data.pages.length) setPage(0); }, [requirements.data, page]);
  const turn = async () => {
    if (page + 1 < (requirements.data?.pages.length ?? 0)) { setPage(page + 1); return; }
    const result = await requirements.fetchNextPage();
    if (!result.isError && result.data && result.data.pages.length > page + 1) setPage(page + 1);
  };
  return <div className="rfp-review-columns">
    <OriginalDocument key={document.document_version_id} document={document} origin={selected?.origin} onQuoteClick={() => globalThis.document.getElementById('rfp-selected-analysis')?.focus()} />
    <aside className="rfp-analysis-pane" aria-label="문서별 분석 결과">
      <div className="rfp-analysis-toolbar"><h3>분석한 요구</h3><label><input type="checkbox" checked={filter} onChange={e => { setFilter(e.target.checked); setPage(0); setPicked(undefined); }} />미해결 필수만</label></div>
      {requirements.isError ? <ErrorState error={requirements.error} onRetry={() => requirements.refetch()} /> : requirements.isPending ? <Loading label="분석 결과 읽는 중" /> : <>
        <div className="rfp-requirement-cards" role="group" aria-label="분석 요구 선택">
          {rows.map(row => <button type="button" key={row.requirement_id} aria-pressed={row.requirement_id === id} onClick={() => setPicked(row.requirement_id)} className="rfp-requirement-card">
            <span><b>{requirementNo(row.code)}</b><small className={`rfp-status ${row.status}`}>{label(STATUS_LABEL, row.status)}</small></span>
            <strong>{[row.equipment, attributeName(row.attribute_key, row.attribute)].filter(Boolean).join(' · ') || '내용 확인 필요'}</strong>
            <span>{row.value_text || '요구값 미확정'}<small>{label(NECESSITY_LABEL, row.necessity)}</small></span>
          </button>)}
          {!rows.length && <p className="rfp-review-empty">{filter ? '이 문서에 미해결 필수 요구가 없습니다.' : '이 문서의 분석 결과가 없습니다. 처리 실행 후 확인하세요.'}</p>}
        </div>
        <nav className="rfp-review-pagination" aria-label="문서 요구 페이지"><button className="kb" disabled={page === 0 || requirements.isFetching} onClick={() => setPage(page - 1)}>이전 요구 목록</button><span>{page + 1}쪽 · {rows.length}건</span><button className="kb" disabled={requirements.isFetching || !(page + 1 < (requirements.data?.pages.length ?? 0) || requirements.hasNextPage)} onClick={turn}>다음 요구 목록</button></nav>
      </>}
      {id && (detail.isError ? <ErrorState error={detail.error} onRetry={() => detail.refetch()} /> : detail.isPending ? <Loading label="선택한 요구 읽는 중" /> : selected ? <AnalysisDetail data={selected} projectId={projectId} /> : <p role="alert">요구와 문서 판본이 일치하지 않습니다. 문서를 다시 선택하세요.</p>)}
    </aside>
  </div>;
}

function AnalysisDetail({ data, projectId }: { data: RequirementDetail; projectId: string }) {
  const { spec, judgement, origin } = data;
  return <article className="rfp-selected-analysis" id="rfp-selected-analysis" tabIndex={-1}>
    <div className="rfp-analysis-title"><h3>{requirementNo(data.code)} 분석 상세</h3><span>{label(PROVENANCE_LABEL, judgement.provenance_kind)}</span></div>
    <h4>원문에서 인용한 문장</h4><blockquote>{origin.quote || '인용 문구가 없습니다.'}</blockquote>
    <p className="rfp-location">{[origin.clause_no && `조항 ${origin.clause_no}`, origin.page != null && `원문 ${origin.page}쪽`].filter(Boolean).join(' · ') || '원문 위치 정보 없음'}</p>
    <h4>추출한 요구</h4><dl>
      <dt>장비·작업</dt><dd>{[spec.equipment, spec.task].filter(Boolean).join(' · ') || '미확정'}</dd>
      <dt>속성·요구값</dt><dd>{[attributeName(spec.attribute_key, spec.attribute), label(OPERATOR_LABEL, spec.operator), spec.value, spec.unit].filter(Boolean).join(' ') || '미확정'}</dd>
      <dt>조건</dt><dd>{spec.conditions.map(c => [c.name, c.value, c.unit].filter(Boolean).join(' ')).join(' / ') || '기재 없음'}</dd>
      <dt>규격·성격</dt><dd>{[spec.standard_system !== 'NONE' && spec.standard_system, label(NECESSITY_LABEL, spec.necessity)].filter(Boolean).join(' · ')}</dd>
    </dl>
    <h4>판단과 확인할 점</h4><p className={`rfp-status ${judgement.status}`}>{label(STATUS_LABEL, judgement.status)}</p><p>{judgement.reason}</p>
    {(data.needs_review || data.needs_rereview) && <p className="rfp-review-warning">{data.needs_review && '원문 확인 필요 '}{data.needs_rereview && '재검토 필요'}</p>}
    <dl><dt>미확인</dt><dd>{[...spec.undetermined_items, ...judgement.unresolved].join(' / ') || '기록된 항목 없음'}</dd><dt>보완 경로</dt><dd>{label(PATH_LABEL, judgement.path ?? '') || '미지정'}</dd></dl>
    <p className="rfp-review-help">추출·판단 결과는 담당자의 최종 승인과 다릅니다.</p>
    <div className="rfp-review-actions"><Link className="kb" to={`/tft/projects/${projectId}/requirements/${data.requirement_id}`}>상세 검토·변경 제안</Link><Link className="kb" to={`/tft/projects/${projectId}/table`}>자사 대응표</Link></div>
  </article>;
}
