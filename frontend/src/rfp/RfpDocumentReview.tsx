import { useQuery } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getDocument, type DocumentVersion, type RequirementSummary } from '../api/rfp';
import { getSupplementRequirement } from '../api/rfp-supplement';
import { getProject } from '../api/tft';
import { ErrorState, Loading } from '../components/states';
import { latestVersions } from './IntakeForms';
import { OriginalDocument } from './OriginalDocument';
import { DiagramReview } from './DiagramReview';
import { readDocumentRequirements } from './equipment-groups';
import { ContextReview } from './ContextReview';
import { ResponseDecision } from './ResponseDecision';
import type { ContextSource } from '../api/rfp-context';
import './RfpDocumentReview.css';

type Selection = { requirementId: string; version: number; row?: RequirementSummary; source?: ContextSource; revision: number };
export function RfpDocumentReview({ projectId, actorId, documents, busy = false, onContextStarted }: { projectId: string; actorId?: string; documents: DocumentVersion[]; busy?: boolean; onContextStarted?: (job:{job_id:string})=>void }) {
  const [picked, setPicked] = useState<string>();
  const [selection, setSelection] = useState<Selection>();
  const versions = latestVersions(documents);
  const document = documents.find(d => d.document_version_id === picked) ?? versions.find(d => d.kind === 'RFP_SPEC') ?? versions[0];
  const source = (ref:ContextSource) => {
    if (!documents.some(doc => doc.document_id === ref.document_id && doc.document_version_id === ref.document_version_id)) return;
    setPicked(ref.document_version_id);
    setSelection(previous => ({ requirementId:ref.requirement_id,version:ref.version,source:ref,revision:(previous?.revision??0)+1 }));
  };
  if (!document) return <div className="rfp-review-empty">문서를 올리면 원문과 고객 요구를 함께 검토할 수 있습니다.</div>;
  return <section id="rfp-document-review" className="rfp-document-review" aria-label="RFP 검토">
    <div className="rfp-review-heading"><div><h2>RFP 검토</h2><p>검토 대상 선택 → 고객 요구 확인 → 대응 방안 검토</p></div>
      <label>검토 문서<select aria-label="검토 문서" value={document.document_version_id} onChange={e => {setPicked(e.target.value);setSelection(undefined);}}>{documents.map(d => <option key={d.document_version_id} value={d.document_version_id}>{d.title} · 판본 {d.version}</option>)}</select></label>
    </div>
    <DocumentAnalysis key={`${actorId}:${projectId}:${document.document_version_id}`} projectId={projectId} actorId={actorId} document={document} documents={documents} busy={busy} onContextStarted={onContextStarted} selection={selection} onSource={source} onClearSelection={()=>setSelection(undefined)} onRevealQuote={()=>setSelection(previous=>previous&&{...previous,revision:previous.revision+1})} onSelect={(row,ref)=>{if(ref&&ref.document_version_id!==document.document_version_id)source(ref);else setSelection(previous=>({requirementId:row.requirement_id,version:row.version,row,source:ref,revision:(previous?.revision??0)+1}));}} />
  </section>;
}

function DocumentAnalysis({ projectId, actorId, document, documents, busy, onContextStarted, selection, onSource, onSelect, onClearSelection, onRevealQuote }: { projectId:string;actorId?:string;document:DocumentVersion;documents:DocumentVersion[];busy:boolean;onContextStarted?:(job:{job_id:string})=>void;selection?:Selection;onSource:(source:ContextSource)=>void;onSelect:(row:RequirementSummary,source?:ContextSource)=>void;onClearSelection:()=>void;onRevealQuote:()=>void }) {
  const mode=getApiMode();
  const project=useQuery({queryKey:['projects',mode,projectId],queryFn:()=>getProject(projectId),retry:false});
  const requirements=useQuery({queryKey:['rfp-requirements',mode,projectId,actorId,'unified-review',document.document_version_id],queryFn:({signal})=>readDocumentRequirements(projectId,{document_version_id:document.document_version_id},signal,{useMockFallback:false}),retry:false});
  const documentInfo=useQuery({queryKey:['rfp-document',mode,document.document_id,actorId,document.document_version_id],queryFn:()=>getDocument(document.document_id),retry:false});
  const currentDocument=!documentInfo.isError&&documentInfo.data?.data.document_version_id===document.document_version_id?documentInfo.data.data:undefined;
  const rows=requirements.isError?[]:requirements.data??[];
  const row=selection?rows.find(item=>item.requirement_id===selection.requirementId&&item.version===selection.version):undefined;
  const detail=useQuery({queryKey:['rfp-response-detail',mode,actorId,projectId,selection?.requirementId,selection?.version],queryFn:()=>getSupplementRequirement(selection!.requirementId),enabled:!!selection,retry:false});
  const value=detail.isSuccess&&detail.data?.data.requirement_id===selection?.requirementId&&detail.data.data.version===selection.version?detail.data.data:undefined;
  const sourceMatches=!selection?.source||selection.source.document_id===document.document_id&&selection.source.document_version_id===document.document_version_id;
  const selected=requirements.isSuccess&&!!row&&value&&sourceMatches&&value.origin.document_version_id===document.document_version_id?value:undefined;
  const canSelect=!!actorId&&project.isSuccess&&!project.isFetching&&!busy&&[project.data.data.sales_user_id,project.data.data.responsible_user_id].includes(actorId);
  const focusAnalysis=useCallback(()=>globalThis.document.getElementById('rfp-selected-analysis')?.focus({preventScroll:true}),[]);
  const saved=()=>{void requirements.refetch();void detail.refetch();};
  const [originalCollapsed,setOriginalCollapsed]=useState(false);
  const showQuote=()=>{setOriginalCollapsed(false);onRevealQuote();requestAnimationFrame(()=>globalThis.document.querySelector('.rfp-unified-columns .rfp-original-pane')?.scrollIntoView?.({block:'nearest'}));};
  return <>
    {(documentInfo.isError||documentInfo.isSuccess&&!currentDocument)&&<p className="rfp-review-warning" role="status">현재 판본 추출 안내 확인 불가</p>}
    {(currentDocument?.extraction_warnings??[]).length>0&&<div className="rfp-review-warning" role="status"><strong>원문 추출 확인 필요</strong><ul>{(currentDocument?.extraction_warnings??[]).map((warning,index)=><li key={index}>{warning}</li>)}</ul></div>}
    <DiagramReview document={document} actorId={actorId} projectId={projectId} />
    {document.kind==='DRAWING'&&requirements.isSuccess&&!rows.length&&<p className="rfp-review-help">추출된 문자 요구는 0건입니다. 도면의 장비·기호와 연결 후보는 위 도면 검토에서 확인하세요.</p>}
    <button type="button" className="kb rfp-unified-source-toggle" aria-expanded={!originalCollapsed} onClick={()=>setOriginalCollapsed(!originalCollapsed)}>{originalCollapsed?'원문 펼치기':'원문 접기'}</button>
    <div className={`rfp-review-columns rfp-unified-columns${originalCollapsed?' source-collapsed':''}`}>
      <div hidden={originalCollapsed}><OriginalDocument document={document} origin={selected?.origin} selectionRevision={selected?selection?.revision:0} onQuoteClick={focusAnalysis} /></div>
      <aside className="rfp-analysis-pane" aria-label="검토 대상·요구·대응">
        <ContextReview projectId={projectId} actorId={actorId} document={document} documents={documents} busy={busy} onStarted={onContextStarted} onSource={onSource} requirements={rows} requirementsLoading={requirements.isPending} requirementsError={requirements.error} selectedRequirementId={selection?.requirementId} onSelectRequirement={onSelect} onClearSelection={onClearSelection} />
        {project.isError&&<ErrorState error={project.error} onRetry={()=>project.refetch()}/>}
        {selection&&<div id="rfp-selected-analysis" className="rfp-selected-analysis rfp-unified-decision" tabIndex={-1}>
          {detail.isError?<ErrorState error={detail.error} onRetry={()=>detail.refetch()}/>:detail.isPending?<Loading label="선택 요구의 현재 판정 읽는 중"/>:!selected||!row?<><p role="alert">요구 또는 문서 판본이 일치하지 않아 원문 강조와 대응 선택을 보류했습니다.</p><Link to={`/tft/projects/${projectId}/requirements/${selection.requirementId}`}>요구 상세에서 확인</Link></>:actorId?<ResponseDecision key={`${actorId}:${selected.requirement_id}:${selected.version}`} projectId={projectId} actorId={actorId} requirement={row} canSelect={canSelect&&!requirements.isFetching} reviewDepartments={project.isSuccess?project.data.data.review_departments:undefined} projectVersion={project.isSuccess&&!project.isFetching?project.data.data.version:undefined} canEditProject={canSelect} onProjectSaved={()=>project.refetch()} onSaved={saved} onShowQuote={showQuote}/>:<p>대응 방안 선택은 계정을 확인한 뒤 이용할 수 있습니다.</p>}
        </div>}
      </aside>
    </div>
  </>;
}
