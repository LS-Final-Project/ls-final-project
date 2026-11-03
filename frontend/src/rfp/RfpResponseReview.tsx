import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type CSSProperties } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { me } from '../api/platform';
import { listDocuments, type DocumentVersion, type RequirementSummary } from '../api/rfp';
import { getSupplementRequirement } from '../api/rfp-supplement';
import { CONTEXT_CATEGORIES, getContext } from '../api/rfp-context';
import { getProject } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvTitle } from '../ls-shell/kv';
import { latestVersions } from './IntakeForms';
import { readDocumentRequirements } from './equipment-groups';
import IntegrationPanel from './IntegrationPanel';
import { OriginalDocument } from './OriginalDocument';
import { ResponseDecision } from './ResponseDecision';
import { responseReviewGroups } from './response-review-groups';
import { attributeName, label, STATUS_LABEL, requirementNo } from './labels';
import './RfpDocumentReview.css';
import './RfpResponseReview.css';

export default function RfpResponseReview() {
  const { projectId = '' } = useParams();
  const access = useBusinessAccess('tft');
  const user = useQuery({ queryKey: ['me'], queryFn: me, enabled: access.ready, retry: false });
  if (!access.ready) return <BusinessGate access={access} title="대응 방안 검토" />;
  if (user.isPending) return <Loading label="선택 권한 확인 중" />;
  if (user.isError) return <ErrorState error={user.error} onRetry={() => user.refetch()} />;
  if (!user.data) return <p role="alert">로그인이 필요합니다.</p>;
  return <ResponseProject key={`${getApiMode()}:${user.data.data.id}:${projectId}`} projectId={projectId} actorId={user.data.data.id} />;
}

function ResponseProject({ projectId, actorId }: { projectId: string; actorId: string }) {
  const mode = getApiMode(), client = useQueryClient();
  const project = useQuery({ queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId) });
  const documents = useQuery({ queryKey: ['rfp-response-documents', mode, actorId, projectId], queryFn: () => listDocuments(projectId) });
  const [documentId, setDocumentId] = useState('');
  const available = documents.data?.data.items ?? [];
  const document = available.find(d => d.document_version_id === documentId) ?? (!documentId ? latestVersions(available).find(d => d.kind === 'RFP_SPEC') ?? latestVersions(available)[0] : undefined);
  const canSelect = project.isSuccess && !!project.data && [project.data.data.sales_user_id, project.data.data.responsible_user_id].includes(actorId);
  return <main className="rfp-response-review">
    <KvTitle path="제안 지원" title="대응 방안 검토" code="TF0102"><Link className="kb" to={`/tft/projects/${projectId}`}>원문 분석</Link><Link className="kb" to={`/tft/projects/${projectId}/draft`}>검토본 초안</Link><Link className="kb" to={`/tft/projects/${projectId}/table/approvals`}>변경 승인</Link></KvTitle>
    <p className="response-project-name">{project.data?.data.name ?? '프로젝트 확인 중'}</p>
    {project.isError && <ErrorState error={project.error} onRetry={() => project.refetch()} />}
    {documents.isError ? <ErrorState error={documents.error} onRetry={() => documents.refetch()} /> : documents.isPending ? <Loading /> : <>
      <label className="response-document">원문 문서<select aria-label="원문 문서" value={document?.document_version_id ?? ''} onChange={e => setDocumentId(e.target.value)}>{available.map(d => <option key={d.document_version_id} value={d.document_version_id}>{d.title} · 판본 {d.version}</option>)}</select></label>
      {document ? <DocumentResponses key={document.document_version_id} projectId={projectId} actorId={actorId} document={document} canSelect={canSelect} reviewDepartments={project.isSuccess ? project.data.data.review_departments : undefined} projectVersion={project.isSuccess && !project.isFetching ? project.data.data.version : undefined} canEditProject={canSelect && !project.isFetching} onProjectSaved={() => { void client.invalidateQueries({ queryKey: ['projects', mode, projectId] }); }} /> : <p>선택할 원문 문서가 없습니다.</p>}
    </>}
    {project.isSuccess && <details className="response-integration"><summary>프로젝트 조립품 통합 책임</summary><IntegrationPanel projectId={projectId} canEdit={canSelect} /></details>}
  </main>;
}

function DocumentResponses({ projectId, actorId, document, canSelect, reviewDepartments, projectVersion, canEditProject, onProjectSaved }: { projectId: string; actorId: string; document: DocumentVersion; canSelect: boolean; reviewDepartments?: string[]; projectVersion?: number; canEditProject: boolean; onProjectSaved: () => void }) {
  const mode = getApiMode(), client = useQueryClient();
  const prefix = ['rfp-response', mode, actorId, projectId, document.document_version_id];
  const requirements = useQuery({ queryKey: [...prefix, 'requirements'], queryFn: ({ signal }) => readDocumentRequirements(projectId, { document_version_id: document.document_version_id }, signal, { useMockFallback: false }) });
  const context = useQuery({ queryKey: ['rfp-context', mode, projectId, actorId], queryFn: ({ signal }) => getContext(projectId, signal), retry: false });
  const [groupId, setGroupId] = useState(''), [category, setCategory] = useState(''), [search, setSearch] = useState(''), [unresolved, setUnresolved] = useState(false);
  const [picked, setPicked] = useState(''), [pagination, setPagination] = useState({ scope: '', page: 1 });
  const [selectionRevision, setSelectionRevision] = useState(0);
  const revealSelection = () => setSelectionRevision(n => n + 1);
  const [collapsed, setCollapsed] = useState(false), [wide, setWide] = useState(false);
  const rows = requirements.data ?? [];
  const grouped = responseReviewGroups(projectId, document, rows, context.isError ? undefined : context.data?.data);
  const card = useRef<HTMLDivElement>(null);
  const group = grouped.groups.find(g => g.key === groupId);
  const query = search.trim().toLocaleLowerCase();
  const filtered = (group?.rows ?? rows).filter(row => (!grouped.current || !category || grouped.categories.get(row.requirement_id)?.includes(category as keyof typeof CONTEXT_CATEGORIES)) && (!unresolved || isUnresolved(row)) && (!query || [row.code, row.equipment, row.task, row.attribute, row.value_text].some(value => value?.toLocaleLowerCase().includes(query))));
  const scope = JSON.stringify([groupId, category, search, unresolved]);
  const pages = Math.max(1, Math.ceil(filtered.length / 10));
  const page = pagination.scope === scope ? Math.min(pagination.page, pages) : 1;
  const visible = filtered.slice((page - 1) * 10, page * 10);
  const selected = visible.find(row => row.requirement_id === picked) ?? visible[0];
  const detail = useQuery({ queryKey: ['rfp-response-detail', mode, actorId, projectId, selected?.requirement_id, selected?.version], queryFn: () => getSupplementRequirement(selected!.requirement_id), enabled: !!selected });
  const checked = detail.data?.data;
  const origin = !detail.isError && selected && checked?.requirement_id === selected.requirement_id && checked.version === selected.version && checked.origin.document_version_id === document.document_version_id ? checked.origin : undefined;
  const saved = () => { void client.invalidateQueries({ queryKey: ['rfp-response', mode, actorId, projectId] }); void client.invalidateQueries({ queryKey: ['rfp-response-detail', mode, actorId, projectId] }); };
  const start = Math.max(1, Math.min(page - 2, pages - 4));
  return <>
    <div className="response-toolbar">
      <label>설비·과업<select aria-label="설비·과업" value={group?.key ?? ''} onChange={e => { setGroupId(e.target.value); revealSelection(); }}><option value="">선택 문서 전체</option>{grouped.groups.map(g => <option key={g.key} value={g.key}>{g.name} ({g.rows.length})</option>)}</select></label>
      <label>분류<select aria-label="분류" value={grouped.current ? category : ''} disabled={!grouped.current} onChange={e => { setCategory(e.target.value); revealSelection(); }}><option value="">전체 분류</option>{Object.entries(CONTEXT_CATEGORIES).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
      <label>요구 검색<input aria-label="요구 검색" value={search} onChange={e => setSearch(e.target.value)} placeholder="장비·속성·요구값" /></label>
      <label className="response-check"><input type="checkbox" checked={unresolved} onChange={e => setUnresolved(e.target.checked)} />미해결만</label>
    </div>
    <div className="response-context-summary"><span role="status">{grouped.current ? '현재 맥락 정리' : context.isPending ? '맥락 확인 중' : context.isError ? '맥락 조회 실패 · 문서별 요구 표시' : context.data?.data.status === 'STALE' ? '이전 맥락 · 원문 재확인 필요' : '맥락 미정리 · 문서별 요구 표시'}</span><details><summary>화면 안내·묶음 기준</summary>    <p role="status" className="response-context-notice">{grouped.current ? '현재 맥락 정리의 설비·과업과 주·추가 분류를 표시합니다. 묶음은 대응 충족 판정이 아닙니다.' : context.isPending ? '맥락 확인 중 · 선택 문서의 정확한 장비명·조항 묶음으로 표시합니다.' : `맥락 ${context.isError ? '조회 실패' : context.data?.data.status === 'STALE' ? '판본 변경' : '미분석 또는 요구·출처 판본 불일치'} · 선택 문서의 정확한 장비명·조항 묶음으로 표시합니다. 분류는 원문 분석에서 확인하세요.`}</p></details></div>
    {requirements.isError ? <ErrorState error={requirements.error} onRetry={() => requirements.refetch()} /> : requirements.isPending ? <Loading label="문서 요구 읽는 중" /> : <>
      <div className="response-pane-controls"><button className="kb" onClick={() => setCollapsed(!collapsed)} aria-expanded={!collapsed}>{collapsed ? '원문 펼치기' : '원문 접기'}</button><button className="kb" disabled={collapsed} onClick={() => setWide(!wide)} aria-pressed={wide}>{wide ? '원문 기본 너비' : '원문 넓게'}</button><span>요구 {filtered.length}건 · 미해결 {filtered.filter(isUnresolved).length}건</span></div>
      <div className={`response-workspace${collapsed ? ' source-collapsed' : ''}`} style={{ '--source-width': wide ? '56%' : '42%' } as CSSProperties}>
        <div className="response-source" hidden={collapsed}><OriginalDocument document={document} origin={origin} selectionRevision={origin ? selectionRevision : 0} onQuoteClick={() => card.current?.focus({ preventScroll: true })} />{selected && detail.isError && <ErrorState error={detail.error} onRetry={() => detail.refetch()} />}{selected && checked && !origin && <p role="alert">선택 요구와 원문 판본이 일치하지 않습니다. 목록을 새로 조회하세요.</p>}</div>
        <section className="response-decisions" aria-label="선택 범위 대응 검토"><header><h2>{group?.name ?? '선택 문서 전체'}{category && grouped.current ? ` · ${CONTEXT_CATEGORIES[category as keyof typeof CONTEXT_CATEGORIES]}` : ''}</h2></header>
          <details className="response-requirement-list"><summary>요구 목록 · {filtered.length}건 · 선택 {selected ? requirementNo(selected.code) : '없음'}</summary><nav className="response-requirements" aria-label="요구 선택">{visible.map((row, i) => <button key={row.requirement_id} aria-pressed={selected?.requirement_id === row.requirement_id} onClick={() => { setPicked(row.requirement_id); revealSelection(); }}><span>{(page - 1) * 10 + i + 1}. {requirementNo(row.code)} · {attributeName(row.attribute_key, row.attribute)}</span><small>{row.value_text ?? '요구값 확인 필요'} · {label(STATUS_LABEL, row.status)}{row.needs_review || row.needs_rereview ? ' · 재확인 필요' : ''}</small></button>)}</nav></details>
          <nav className="response-pagination" aria-label="요구 페이지"><span>{page} / {pages}쪽 · 한 페이지 10건</span><button className="kb" disabled={page <= 1} onClick={() => { setPagination({ scope, page: page - 1 }); revealSelection(); }}>이전</button>{Array.from({ length: Math.min(5, pages) }, (_, i) => start + i).map(n => <button className="kb" key={n} aria-current={page === n ? 'page' : undefined} aria-label={`${n}페이지`} onClick={() => { setPagination({ scope, page: n }); revealSelection(); }}>{n}</button>)}<button className="kb" disabled={page >= pages} onClick={() => { setPagination({ scope, page: page + 1 }); revealSelection(); }}>다음</button></nav>
          <div ref={card} tabIndex={-1} className="response-selected-card">{selected ? <ResponseDecision key={`${selected.requirement_id}:${selected.version}`} projectId={projectId} actorId={actorId} requirement={selected} reviewDepartments={reviewDepartments} projectVersion={projectVersion} canEditProject={canEditProject} onProjectSaved={onProjectSaved} canSelect={canSelect && !requirements.isFetching} onSaved={saved} /> : <p>조건에 맞는 요구가 없습니다.</p>}</div>
        </section>
      </div>
    </>}
  </>;
}
function isUnresolved(row: RequirementSummary) { return row.status !== 'EVIDENCE_FOUND' || row.needs_review || row.needs_rereview || row.open_questions > 0; }
