import { useQuery } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getComparison, getDocument, getRequirement, listProjectCandidates, type DocumentVersion, type RequirementDetail } from '../api/rfp';
import { ErrorState, Loading } from '../components/states';
import { latestVersions } from './IntakeForms';
import { attributeName, label, NECESSITY_LABEL, OPERATOR_LABEL, PATH_LABEL, PROVENANCE_LABEL, STATUS_LABEL, requirementNo } from './labels';
import { OriginalDocument } from './OriginalDocument';
import { DiagramReview } from './DiagramReview';
import { equipmentGroups, readDocumentRequirements } from './equipment-groups';
import { EquipmentOverview, EquipmentResponse, ReviewPages } from './EquipmentOverview';
import './RfpDocumentReview.css';

export function RfpDocumentReview({ projectId, actorId, documents }: { projectId: string; actorId?: string; documents: DocumentVersion[] }) {
  const [picked, setPicked] = useState<string>();
  const versions = latestVersions(documents);
  const document = versions.find(d => d.document_version_id === picked) ?? versions.find(d => d.kind === 'RFP_SPEC') ?? versions[0];
  if (!document) return <div className="rfp-review-empty">문서를 올리면 원문과 분석 결과를 나란히 확인할 수 있습니다.</div>;
  return <section id="rfp-document-review" className="rfp-document-review" aria-label="원문과 분석 검토">
    <div className="rfp-review-heading"><div><h2>원문과 분석 결과</h2><p>{document.kind === 'DRAWING' ? '도면 원본에서 장비·기호와 연결 후보를 확인하세요. 문자 요구는 별도로 표시합니다.' : '설비별 대응 현황을 확인하고, 세부 요구를 선택해 원문과 근거를 대조하세요.'}</p></div>
      <label>검토 문서<select aria-label="검토 문서" value={document.document_version_id} onChange={e => setPicked(e.target.value)}>{versions.map(d => <option key={d.document_version_id} value={d.document_version_id}>{d.title} · v{d.version}</option>)}</select></label>
    </div>
    <DocumentAnalysis key={`${actorId}:${projectId}:${document.document_version_id}`} projectId={projectId} actorId={actorId} document={document} />
  </section>;
}

function DocumentAnalysis({ projectId, actorId, document }: { projectId: string; actorId?: string; document: DocumentVersion }) {
  const mode = getApiMode();
  const drawing = document.kind === 'DRAWING';
  const [diagramVisible, setDiagramVisible] = useState(false);
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState<string>();
  const [filter, setFilter] = useState(false);
  const [selectionRevision, setSelectionRevision] = useState(0);
  const focusAnalysis = useCallback(() => globalThis.document.getElementById('rfp-selected-analysis')?.focus({ preventScroll: true }), []);
  const [groupKey, setGroupKey] = useState<string>();
  const requirements = useQuery({
    queryKey: ['rfp-requirements', mode, projectId, actorId, 'equipment-review', document.document_version_id, filter],
    queryFn: ({ signal }) => readDocumentRequirements(projectId, { document_version_id: document.document_version_id, ...(filter ? { unresolved_mandatory: true } : {}) }, signal),
    retry: false,
  });
  const documentInfo = useQuery({ queryKey: ['rfp-document', mode, document.document_id, actorId, document.document_version_id], queryFn: () => getDocument(document.document_id), retry: false });
  const currentDocumentInfo = !documentInfo.isError && documentInfo.data?.data.document_version_id === document.document_version_id ? documentInfo.data.data : undefined;
  const extractionWarnings = currentDocumentInfo?.extraction_warnings ?? [];
  const extractionInfoUnavailable = documentInfo.isError || (documentInfo.isSuccess && !currentDocumentInfo);
  const candidates = useQuery({ queryKey: ['rfp-integration-candidates', mode, projectId, actorId, 'equipment-review'], queryFn: ({ signal }) => listProjectCandidates(projectId, signal), retry: false });
  const groups = useMemo(() => equipmentGroups(requirements.isError ? [] : requirements.data ?? []), [requirements.data, requirements.isError]);
  const group = groups.find(g => g.key === groupKey) ?? groups[0];
  const totalPages = Math.ceil((group?.rows.length ?? 0) / 10);
  const currentPage = Math.min(page, Math.max(0, totalPages - 1));
  const rows = group?.rows.slice(currentPage * 10, currentPage * 10 + 10) ?? [];
  const id = rows.find(r => r.requirement_id === picked)?.requirement_id ?? rows[0]?.requirement_id;
  const requirementVersion = rows.find(r => r.requirement_id === id)?.version;
  const detail = useQuery({ queryKey: ['rfp-requirement', mode, actorId, projectId, id, requirementVersion], queryFn: () => getRequirement(id!), enabled: !!id, retry: false });
  // Never highlight stale detail from another document/version after navigation or invalidation.
  const selected = !!id && !detail.isError && detail.data?.data.requirement_id === id && detail.data.data.version === requirementVersion && detail.data.data.origin.document_version_id === document.document_version_id ? detail.data.data : undefined;
  const chooseGroup = (key: string) => { setGroupKey(key); setPage(0); setPicked(undefined); };
  return <>
    {extractionInfoUnavailable && <p className="rfp-review-warning" role="status">현재 판본 추출 안내 확인 불가</p>}
    {extractionWarnings.length > 0 && <div className="rfp-review-warning" role="status"><strong>원문 추출 확인 필요</strong><ul>{extractionWarnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></div>}
    <DiagramReview document={document} actorId={actorId} projectId={projectId} onOriginalVisible={setDiagramVisible} />
    <div className="rfp-group-filter"><label><input type="checkbox" checked={filter} onChange={e => { setFilter(e.target.checked); setPage(0); setPicked(undefined); setGroupKey(undefined); }} />미해결 필수만</label></div>
    {requirements.isError ? <ErrorState error={requirements.error} onRetry={() => requirements.refetch()} /> : requirements.isPending ? <Loading label="설비별 요구 전체 읽는 중" /> : <>
      {groups.length ? <EquipmentOverview groups={groups} selected={group?.key} onSelect={chooseGroup} filtered={filter} /> : <p className="rfp-review-empty">{filter ? '이 문서에 미해결 필수 요구가 없습니다.' : drawing ? '추출된 문자 요구는 0건입니다. 도면의 장비·기호와 연결 후보는 위 도면 검토에서 확인하세요.' : '이 문서의 분석 결과가 없습니다. 처리 실행 후 확인하세요.'}</p>}
      {group && <EquipmentResponse group={group} candidates={candidates.data?.data.items ?? []} candidatesPending={candidates.isPending} candidatesError={candidates.isError} projectId={projectId} />}
    </>}
    {(!drawing || groups.length > 0 || !diagramVisible) && <div className="rfp-review-columns" style={drawing && !groups.length ? { gridTemplateColumns: 'minmax(0, 1fr)' } : undefined}>
    <OriginalDocument key={document.document_version_id} document={document} origin={selected?.origin} selectionRevision={selectionRevision} onQuoteClick={focusAnalysis} />
    {(!drawing || groups.length > 0) && <aside className="rfp-analysis-pane" aria-label="문서별 분석 결과">
      <div className="rfp-analysis-toolbar"><h3>{group ? `${group.name} · 세부 요구` : '분석한 요구'}</h3></div>
      {!requirements.isError && !requirements.isPending && <>
        <div className="rfp-requirement-cards" role="group" aria-label="분석 요구 선택">
          {rows.map(row => <button type="button" key={row.requirement_id} aria-pressed={row.requirement_id === id} onClick={() => { setPicked(row.requirement_id); setSelectionRevision(n => n + 1); }} className="rfp-requirement-card">
            <span><b>{requirementNo(row.code)}</b><small className={`rfp-status ${row.status}`}>{label(STATUS_LABEL, row.status)}</small></span>
            <strong>{attributeName(row.attribute_key, row.attribute) || row.task || '내용 확인 필요'}</strong>
            <span>{row.value_text || '요구값 미확정'}<small>{label(NECESSITY_LABEL, row.necessity)}</small></span>
          </button>)}
        </div>
        {rows.length > 0 && <><p className="rfp-review-page-count">{currentPage + 1}쪽 · {rows.length}건 / 선택 묶음 {group?.rows.length}건</p><ReviewPages label="문서 요구 페이지" page={currentPage} count={totalPages} onChange={n => { setPage(n); setPicked(undefined); }} /></>}
      </>}
      {id && (detail.isError ? <ErrorState error={detail.error} onRetry={() => detail.refetch()} /> : detail.isPending ? <Loading label="선택한 요구 읽는 중" /> : selected ? <AnalysisDetail data={selected} projectId={projectId} actorId={actorId} /> : <p role="alert">요구와 문서 판본이 일치하지 않습니다. 문서를 다시 선택하세요.</p>)}
    </aside>}
    </div>}
  </>;
}

function AnalysisDetail({ data, projectId, actorId }: { data: RequirementDetail; projectId: string; actorId?: string }) {
  const { spec, judgement, origin } = data;
  const comparison = useQuery({ queryKey: ['rfp-comparison', getApiMode(), actorId, projectId, data.requirement_id, data.version], queryFn: () => getComparison(data.requirement_id), retry: false });
  const currentComparison = comparison.data?.data.requirement_id === data.requirement_id && comparison.data.data.requirement_version === data.version ? comparison.data.data : undefined;
  return <article className="rfp-selected-analysis" id="rfp-selected-analysis" tabIndex={-1}>
    <div className="rfp-analysis-title"><h3>{requirementNo(data.code)} 분석 상세</h3><span>{label(PROVENANCE_LABEL, judgement.provenance_kind)}</span></div>
    {origin.clause_no?.startsWith('OCR-') && <p className="rfp-review-warning">이미지에서 읽은 텍스트 · 원문 대조 필요. 문자·수치 오독 여부를 원본 이미지에서 확인하세요. 텍스트 밑줄이 연결되지 않을 수 있습니다.</p>}
    <h4>원문에서 인용한 문장</h4><blockquote>{origin.quote || '인용 문구가 없습니다.'}</blockquote>
    <p className="rfp-location">{[origin.clause_no && `조항 ${origin.clause_no}`, origin.page != null && `원문 ${origin.page}쪽`].filter(Boolean).join(' · ') || '원문 위치 정보 없음'}</p>
    <h4>추출한 요구</h4><dl>
      <dt>장비·작업</dt><dd>{[spec.equipment, spec.task].filter(Boolean).join(' · ') || '미확정'}</dd>
      <dt>속성·요구값</dt><dd>{[attributeName(spec.attribute_key, spec.attribute), label(OPERATOR_LABEL, spec.operator), spec.value, spec.unit].filter(Boolean).join(' ') || '미확정'}</dd>
      <dt>조건</dt><dd>{spec.conditions.map(c => [c.name, c.value, c.unit].filter(Boolean).join(' ')).join(' / ') || '기재 없음'}</dd>
      <dt>규격·성격</dt><dd>{[spec.standard_system !== 'NONE' && spec.standard_system, label(NECESSITY_LABEL, spec.necessity)].filter(Boolean).join(' · ')}</dd>
    </dl>
    <h4>선택 요구의 자사 제품 비교</h4>
    {comparison.isPending ? <p>자사 비교 읽는 중</p> : comparison.isError ? <ErrorState error={comparison.error} onRetry={() => comparison.refetch()} /> : !currentComparison ? <p>현재 요구 판본의 비교 결과를 확인할 수 없습니다.</p> : currentComparison.candidates.length ? <ul className="rfp-own-candidates">{currentComparison.candidates.map(candidate => <li key={`${candidate.product_code}:${candidate.variant ?? ''}`}><b>{candidate.product_code}{candidate.variant ? ` · ${candidate.variant}` : ''}</b><span>{[candidate.attribute_match.our_value, candidate.attribute_match.our_unit].filter(Boolean).join(' ') || '자료 없음'} · {({ MEETS: '충족', DIFFERS: '사양 차이', NOT_COMPARABLE: '비교 불가', NO_DATA: '자료 없음' })[candidate.attribute_match.result]}</span><small>{candidate.attribute_match.evidence.map(e => [e.document, e.clause].filter(Boolean).join(' ')).join(' / ') || '근거 없음'}</small></li>)}</ul> : <p>{currentComparison.search_trace.search_failed ? '자사 후보 검색에 실패했습니다.' : '현재 검색 범위에 비교할 자사 후보가 없습니다.'}</p>}
    <h4>판단과 확인할 점</h4><p className={`rfp-status ${judgement.status}`}>{label(STATUS_LABEL, judgement.status)}</p><p>{judgement.reason}</p>
    {(data.needs_review || data.needs_rereview) && <p className="rfp-review-warning">{data.needs_review && '원문 확인 필요 '}{data.needs_rereview && '재검토 필요'}</p>}
    <dl><dt>미확인</dt><dd>{[...spec.undetermined_items, ...judgement.unresolved].join(' / ') || '기록된 항목 없음'}</dd><dt>보완 경로</dt><dd>{label(PATH_LABEL, judgement.path ?? '') || '미지정'}</dd></dl>
    <p className="rfp-review-help">추출·판단 결과는 담당자의 최종 승인과 다릅니다.</p>
    <div className="rfp-review-actions"><Link className="kb" to={`/tft/projects/${projectId}/requirements/${data.requirement_id}`}>상세 검토·변경 제안</Link><Link className="kb" to={`/tft/projects/${projectId}/table`}>자사 대응표</Link></div>
  </article>;
}
