import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { ApiError, getApiMode } from '../api/client';
import { CONTEXT_CATEGORIES, getContext, processContext, updateContext, type ContextAssignment, type ContextCategory, type ContextEnvelope, type ContextSource } from '../api/rfp-context';
import type { DocumentVersion, RequirementSummary } from '../api/rfp';
import { ErrorState, Loading } from '../components/states';
import { settled, useAttemptKey } from './proposal-shared';
import { attributeName, STATUS_LABEL, label } from './labels';
import { readDocumentRequirements } from './equipment-groups';
import { REVIEW_CATEGORY_LABELS, reviewPresentation, reviewRequirementLabel, reviewTargetName } from './context-review-presentation';
import { GroupSummary } from './GroupActions';
import { SITUATION_LABEL, situationOf, type Situation } from './group-actions';
import './ContextReview.css';

export function ContextReview({projectId,actorId,document,documents,busy=false,onStarted,onSource,requirements=[],requirementsLoading=false,requirementsError,selectedRequirementId,onSelectRequirement,onClearSelection}: {
  projectId:string;actorId?:string;document:DocumentVersion;documents:DocumentVersion[];busy?:boolean;
  onStarted?:(job:{job_id:string})=>void;onSource:(source:ContextSource)=>void;
  requirements?:RequirementSummary[];requirementsLoading?:boolean;requirementsError?:unknown;selectedRequirementId?:string;
  onClearSelection?:()=>void;
  onSelectRequirement?:(row:RequirementSummary,source?:ContextSource)=>void;
}) {
  const mode=getApiMode(), client=useQueryClient();
  const query=useQuery({queryKey:['rfp-context',mode,projectId,actorId],queryFn:({signal})=>getContext(projectId,signal),retry:false});
  const [scope,setScope]=useState<'document'|'project'>('document');
  const projectRows=useQuery({queryKey:['rfp-review-project-requirements',mode,projectId,actorId],queryFn:({signal})=>readDocumentRequirements(projectId,{},signal,{useMockFallback:false}),enabled:scope==='project',retry:false});
  const [targetKey,setTargetKey]=useState<string>();
  const [reviewFilter,setReviewFilter]=useState<'needs-review'|'all'>('needs-review');
  const [targetsOpen,setTargetsOpen]=useState(!selectedRequirementId);
  const [requirementsOpen,setRequirementsOpen]=useState(!selectedRequirementId);
  const [targetSearch,setTargetSearch]=useState('');
  const [targetPagination,setTargetPagination]=useState({key:'',page:1});
  const [pagination,setPagination]=useState({key:'',page:1});
  const [edit,setEdit]=useState<string>();
  const [situation,setSituation]=useState<Situation>();
  const [key,nextKey]=useAttemptKey(projectId);const sending=useRef(false);
  const process=useMutation({mutationFn:()=>processContext(projectId,key),onSuccess:r=>{nextKey();onStarted?.(r.data);},onError:e=>{if(settled(e))nextKey();}});
  const data=!query.isError&&query.data?.data.project_id===projectId?query.data.data:undefined;
  const rows=scope==='document'?requirements:projectRows.isError?[]:projectRows.data??[];
  const loading=scope==='document'?requirementsLoading:projectRows.isPending;
  const rowsError=scope==='document'?requirementsError:projectRows.error;
  const presentation=reviewPresentation(projectId,document,documents,rows,data,scope);
  const needsReview=(row:RequirementSummary)=>row.status!=='EVIDENCE_FOUND'||row.needs_review||row.needs_rereview||row.open_questions>0||row.rnd_check==='PENDING';
  const uniqueRows=[...new Map(rows.map(row=>[row.requirement_id,row])).values()];
  const pendingCount=uniqueRows.filter(needsReview).length;
  const filteredTargets=presentation.targets.filter(item=>reviewFilter==='all'||item.rows.some(needsReview));
  const targetListKey=JSON.stringify([document.document_version_id,scope,data?.version,presentation.current,targetSearch,reviewFilter]);
  const searchedTargets=filteredTargets.filter(item=>item.name.toLocaleLowerCase().includes(targetSearch.trim().toLocaleLowerCase()));
  const targetPages=Math.max(1,Math.ceil(searchedTargets.length/6));
  const targetPage=targetPagination.key===targetListKey?Math.min(targetPagination.page,targetPages):1;
  const target=presentation.targets.find(item=>item.key===targetKey)??(!targetKey&&selectedRequirementId?presentation.targets.find(item=>item.rows.some(row=>row.requirement_id===selectedRequirementId)):undefined);
  const pageKey=JSON.stringify([document.document_version_id,scope,target?.key,data?.version,presentation.current,reviewFilter,situation]);
  const filteredRows=(target?.rows??[]).filter(row=>situation?situationOf(row)===situation:reviewFilter==='all'||needsReview(row));
  const pages=Math.max(1,Math.ceil(filteredRows.length/10));
  const page=pagination.key===pageKey?Math.min(pagination.page,pages):1;
  const categoryOrder=Object.keys(CONTEXT_CATEGORIES);
  const sortedRows=[...filteredRows].sort((a,b)=>Number(needsReview(b))-Number(needsReview(a))||categoryOrder.indexOf(presentation.assignments.get(a.requirement_id)?.primary_category??'UNCLASSIFIED')-categoryOrder.indexOf(presentation.assignments.get(b.requirement_id)?.primary_category??'UNCLASSIFIED'));
  const visible=sortedRows.slice((page-1)*10,page*10);
  const selected=target?.rows.find(row=>row.requirement_id===selectedRequirementId);
  const assignment=selected?presentation.assignments.get(selected.requirement_id):undefined;
  const groups=data?.result?.groups??[];
  const pageStart=Math.max(1,Math.min(page-2,pages-4));
  const run=()=>{if(sending.current||busy||!onStarted||!actorId)return;sending.current=true;process.mutate(undefined,{onSettled:()=>{sending.current=false;}});};
  const sourceButton=(id:string,index:number)=>{const ref=presentation.refs.get(id);if(!ref)return <span key={id}>근거 위치 미확인</span>;const available=documents.some(doc=>doc.document_id===ref.document_id&&doc.document_version_id===ref.document_version_id);return <button key={id} type="button" className="kb" disabled={!available} onClick={()=>onSource(ref)}>{reviewRequirementLabel(ref.code,index,ref.page)} 원문{ref.document_version_id!==document.document_version_id?' · 문서 이동':''}{!available?' · 이전 판본 없음':''}</button>;};
  const chooseTarget=(value:string)=>{setTargetKey(value);setSituation(undefined);setEdit(undefined);setTargetsOpen(false);setRequirementsOpen(true);onClearSelection?.();};
  return <section className="rfp-context" aria-label="RFP 검토 대상과 고객 요구">
    <header><h3>검토 대상</h3><button type="button" className="kb" disabled={query.isFetching} onClick={()=>query.refetch()}>검토 대상 새로 조회</button><button className="kb" disabled={busy||process.isPending||!onStarted||!actorId} onClick={run}>검토 대상 정리</button><small className="rfp-context-cost">AI 사용료 발생</small></header>
    <details className="rfp-context-help"><summary>검토 대상 정리 안내</summary><p>기존 고객 요구를 설비·업무별로 정리합니다. 원문 추출이나 대응 판정을 바꾸지 않습니다. 버튼을 누르면 모델 사용 비용이 발생합니다.</p>{data?.version!=null&&<p>정리 판본 {data.version} · {data.provenance==='human_input'?'사람 수정':'AI 정리 제안'} · 담당자 승인과 다릅니다.</p>}</details>
    {process.isError&&<ErrorState error={process.error}/>}
    {query.isError&&<ErrorState error={query.error} onRetry={()=>query.refetch()}/>}
    {query.isPending&&<Loading label="검토 대상 읽는 중"/>}
    {!query.isPending&&!presentation.current&&<p className="rfp-context-warning" role="status">{query.isError?'검토 대상 조회 실패':data?.status==='STALE'?'원문 또는 요구가 바뀐 이전 결과':data?.status==='NOT_ANALYZED'||!data?.result?'아직 검토 대상을 정리하지 않았습니다':'프로젝트·원문·요구 판본 확인 필요'}. 현재 고객 요구는 미정리 요구에서 확인하세요. 다시 정리하기 전에는 대상 연결을 수정할 수 없습니다.</p>}
    <label>표시 범위<select aria-label="표시 범위" value={scope} onChange={e=>{setScope(e.target.value as typeof scope);setTargetKey(undefined);setSituation(undefined);setEdit(undefined);setTargetsOpen(true);setRequirementsOpen(true);onClearSelection?.();}}><option value="document">선택 문서</option><option value="project">프로젝트 전체 문서</option></select></label>
    {rowsError?<ErrorState error={rowsError} onRetry={scope==='project'?()=>projectRows.refetch():undefined}/>:loading?<Loading label="고객 요구 읽는 중"/>:<>
      <div className="rfp-context-review-summary" aria-label="고객 요구 검토 요약"><strong>확인 필요 {pendingCount}건</strong><span>근거 있음 {uniqueRows.length-pendingCount}건</span><small>미해결 질문·개발 확인 대기·재검토 표시가 있거나 충족 근거가 확인되지 않은 요구입니다.</small></div>
      <div className="rfp-context-review-filter" role="group" aria-label="고객 요구 표시"><button className="kb" type="button" aria-pressed={reviewFilter==='needs-review'} onClick={()=>setReviewFilter('needs-review')}>확인 필요한 요구</button><button className="kb" type="button" aria-pressed={reviewFilter==='all'} onClick={()=>setReviewFilter('all')}>전체 요구</button></div>
      <p className="rfp-context-counts">고객 요구 {presentation.uniqueCount}건 / 검토 대상 {presentation.targets.length}개</p><details className="rfp-context-help"><summary>요구 집계 기준</summary><p>고유 요구 {presentation.uniqueCount}건 · 대상 소속 {presentation.membershipCount}건. 여러 대상에 적용되는 공통 요구는 전체 요구 수에 한 번만 포함합니다. 상위 대상의 요구 수는 하위 대상 포함 요약이며 같은 장비로 합친 결과가 아닙니다.</p></details><details className="rfp-context-target-list" open={targetsOpen} onToggle={e=>setTargetsOpen(e.currentTarget.open)}><summary><b title={target?.name}>{target?`${target.name} · 고객 요구 ${target.rows.length}건`:'검토할 설비·업무 선택'}</b><span>{target?'다른 대상 선택':`검토 대상 ${presentation.targets.length}개`}</span></summary><label className="rfp-context-target-search">검토 대상 찾기<input aria-label="검토 대상 찾기" value={targetSearch} onChange={e=>setTargetSearch(e.target.value)} placeholder="설비·업무 이름, 태그, 위치"/></label>
      <div className="rfp-context-targets" role="group" aria-label="검토 대상 선택">{searchedTargets.slice((targetPage-1)*6,targetPage*6).map(item=><button type="button" key={item.key} aria-pressed={target?.key===item.key} onClick={()=>chooseTarget(item.key)}><strong title={item.name}>{item.name}</strong><span>{item.group?({SYSTEM:'계통',EQUIPMENT:'설비',TASK:'업무',COMMON:'공통 업무'})[item.group.kind]:'대상 연결 미확정'} · 고객 요구 {item.rows.length}건{item.includesChildren?' · 하위 대상 포함':''}{item.group?.needs_review?' · 확인 필요':''}</span></button>)}</div>
      {targetPages>1&&<nav className="rfp-context-pagination" aria-label="검토 대상 페이지"><span>{targetPage} / {targetPages}쪽 · 한 페이지 6개</span><div><button className="kb" disabled={targetPage===1} onClick={()=>setTargetPagination({key:targetListKey,page:targetPage-1})}>이전 대상</button>{Array.from({length:Math.min(5,targetPages)},(_,i)=>Math.max(1,Math.min(targetPage-2,targetPages-4))+i).map(n=><button className="kb" key={n} aria-label={`검토 대상 ${n}페이지`} aria-current={targetPage===n?'page':undefined} onClick={()=>setTargetPagination({key:targetListKey,page:n})}>{n}</button>)}<button className="kb" disabled={targetPage===targetPages} onClick={()=>setTargetPagination({key:targetListKey,page:targetPage+1})}>다음 대상</button></div></nav>}
      {rows.length>0&&!searchedTargets.length&&<p>{reviewFilter==='needs-review'&&!targetSearch?'확인 필요한 요구가 없습니다. 전체 요구에서 저장된 근거를 확인하세요.':'검색한 검토 대상이 없습니다.'}</p>}</details>
      {!rows.length&&<p>이 범위에 추출된 고객 요구가 없습니다.</p>}
      {!target&&rows.length>0&&<p className="rfp-context-next">검토할 설비 또는 업무를 선택하세요.</p>}
      {selected&&reviewFilter==='needs-review'&&!needsReview(selected)&&<p className="rfp-context-warning" role="status">선택한 요구는 현재 필터 밖에 있습니다. 선택한 요구의 검토는 유지됩니다.</p>}
      {target&&<section className="rfp-context-customer" aria-label="선택 대상 고객 요구"><header><h4>{target.name}</h4><span>고객 요구 {target.rows.length}건{target.includesChildren?' · 하위 대상 요구를 중복 없이 포함':''}</span></header>
        <GroupSummary key={target.key} projectId={projectId} actorId={actorId} name={target.name} rows={target.rows} busy={busy} collapsed={!!selected} onShowRows={value=>{setSituation(value);setEdit(undefined);setRequirementsOpen(true);onClearSelection?.();requestAnimationFrame(()=>globalThis.document.querySelector('.rfp-context-customer-list')?.scrollIntoView?.({block:'nearest'}));}}/>
        {situation&&<p className="rfp-context-situation" role="status"><span>{SITUATION_LABEL[situation]} {filteredRows.length}건만 보는 중</span><button type="button" className="kb" onClick={()=>setSituation(undefined)}>전체 보기</button></p>}
        <details className="rfp-context-customer-list" open={requirementsOpen} onToggle={e=>setRequirementsOpen(e.currentTarget.open)}><summary><b>{selected?[attributeName(selected.attribute_key,selected.attribute),selected.value_text].filter(Boolean).join(' · ')||'선택 고객 요구':`고객 요구 ${target.rows.length}건`}</b><span>{selected?'다른 요구 선택':'검토할 요구 선택'}</span></summary><div className="rfp-context-requirements">{!visible.length&&<p>이 대상에 확인 필요한 요구가 없습니다. 전체 요구로 전환해 확인하세요.</p>}{visible.map((row,index)=>{const entry=presentation.assignments.get(row.requirement_id),ref=presentation.refs.get(row.requirement_id);const previous=visible[index-1],previousCategory=previous?presentation.assignments.get(previous.requirement_id)?.primary_category:undefined;return <div key={row.requirement_id}>{(index===0||entry?.primary_category!==previousCategory)&&<h5>{entry?REVIEW_CATEGORY_LABELS[entry.primary_category]:'종류 확인 필요'}</h5>}<button type="button" aria-pressed={selectedRequirementId===row.requirement_id} onClick={()=>{setEdit(undefined);setRequirementsOpen(false);onSelectRequirement?.(row,ref);}}><span className="rfp-context-requirement-label">{reviewRequirementLabel(row.code,(page-1)*10+index,ref?.page)}<small>{label(STATUS_LABEL,row.status)}{row.needs_review||row.needs_rereview?' · 재검토 필요':''}</small></span><strong>{[attributeName(row.attribute_key,row.attribute),row.task,row.value_text].filter(Boolean).join(' · ')||'요구 내용 확인 필요'}</strong>{entry?.additional_categories.length? <small>함께 적용: {entry.additional_categories.map(category=>REVIEW_CATEGORY_LABELS[category]).join(', ')}</small>:null}</button></div>;})}</div>
        <nav className="rfp-context-pagination" aria-label="고객 요구 페이지"><span>{page} / {pages}쪽 · 한 페이지 10건</span><div><button className="kb" disabled={page===1} onClick={()=>{setPagination({key:pageKey,page:page-1});setEdit(undefined);onClearSelection?.();}}>이전</button>{Array.from({length:Math.min(5,pages)},(_,i)=>pageStart+i).map(n=><button className="kb" key={n} aria-label={`${n}페이지`} aria-current={page===n?'page':undefined} onClick={()=>{setPagination({key:pageKey,page:n});setEdit(undefined);onClearSelection?.();}}>{n}</button>)}<button className="kb" disabled={page===pages} onClick={()=>{setPagination({key:pageKey,page:page+1});setEdit(undefined);onClearSelection?.();}}>다음</button></div></nav></details>
        {selected&&<details className="rfp-context-selected-info"><summary>선택 요구의 원문 근거·대상 연결</summary>{presentation.refs.has(selected.requirement_id)&&<blockquote>{presentation.refs.get(selected.requirement_id)!.quote||'원문 인용 미확인'}</blockquote>}{assignment&&<><p>연결 사유: {assignment.reason}</p><p>{assignment.group_ids.map(id=>groups.find(group=>group.entity_id===id)).filter((group):group is typeof groups[number]=>!!group).map(group=>reviewTargetName(group,groups)).join(' · ')||'대상 연결 미확정'}</p>{assignment.needs_review&&<p className="rfp-context-warning">담당자 확인 필요</p>}<div className="rfp-context-sources">{[...new Set([selected.requirement_id,...assignment.evidence_requirement_ids])].map(sourceButton)}</div><button className="kb" disabled={busy||!presentation.current||!actorId} onClick={()=>setEdit(selected.requirement_id)}>요구 종류·대상 연결 수정</button>{edit===selected.requirement_id&&data&&<AssignmentEditor key={`${data.version}:${selected.requirement_id}`} busy={busy} data={data} assignment={assignment} projectId={projectId} onCancel={()=>setEdit(undefined)} onSaved={next=>{client.setQueryData(['rfp-context',mode,projectId,actorId],{data:next});setEdit(undefined);}}/>}</>}</details>}
      </section>}
      {data?.result?.issues.length? <details><summary>미확정·누락 안내 {data.result.issues.length}건</summary><ul>{data.result.issues.map((issue,i)=><li key={i}>{issue.message}</li>)}</ul></details>:null}
    </>}
  </section>;
}
function AssignmentEditor({data,assignment,projectId,onCancel,onSaved,busy}:{busy:boolean;data:ContextEnvelope;assignment:ContextAssignment;projectId:string;onCancel:()=>void;onSaved:(data:ContextEnvelope)=>void}) {
  const [category,setCategory]=useState(assignment.primary_category),[groupIds,setGroupIds]=useState(assignment.group_ids),[reason,setReason]=useState('');
  const result={...data.result!,outcome:'PARTIAL' as const,assignments:data.result!.assignments.map(a=>a.requirement_id!==assignment.requirement_id?a:{...a,primary_category:category,additional_categories:a.additional_categories.filter(c=>c!==category),group_ids:groupIds,relations:groupIds.map(group_id=>a.relations.find(r=>r.group_id===group_id)??{group_id,relation:'RELATED' as const}),reason:reason.trim(),needs_review:true})};
  const body={expected_version:data.version!,expected_source_fingerprint:data.source_fingerprint!,reason:reason.trim(),result};
  const [key,nextKey]=useAttemptKey(JSON.stringify(body));const sending=useRef(false);
  const save=useMutation({mutationFn:()=>updateContext(projectId,body,key),onSuccess:r=>{nextKey();onSaved(r.data);},onError:e=>{if(settled(e))nextKey();}});
  return <form className="rfp-context-editor" onSubmit={e=>{e.preventDefault();if(sending.current||busy||!reason.trim())return;sending.current=true;save.mutate(undefined,{onSettled:()=>{sending.current=false;}});}}>
    <label>주 요구 종류<select value={category} onChange={e=>setCategory(e.target.value as ContextCategory)}>{Object.entries(REVIEW_CATEGORY_LABELS).map(([code,name])=><option key={code} value={code}>{name}</option>)}</select></label>
    <fieldset><legend>연결할 검토 대상</legend>{data.result!.groups.map(g=><label key={g.entity_id}><input type="checkbox" checked={groupIds.includes(g.entity_id)} onChange={e=>setGroupIds(e.target.checked?[...groupIds,g.entity_id]:groupIds.filter(id=>id!==g.entity_id))}/>{reviewTargetName(g,data.result!.groups)}</label>)}</fieldset>
    <p>추가 대상은 참고 연결로 기록합니다. 이 수정은 동일 설비 확정이나 담당자 승인이 아닙니다.</p>
    <label>수정 사유<textarea required maxLength={1000} value={reason} onChange={e=>setReason(e.target.value)}/></label>
    <button className="kb" type="button" disabled={save.isPending} onClick={onCancel}>취소</button><button className="kb" type="submit" disabled={busy||save.isPending||!reason.trim()}>새 판본으로 저장</button>
    {save.isError&&(save.error instanceof ApiError&&save.error.status===409?<p role="alert">다른 수정이나 원문 변경이 있습니다. 검토 대상을 다시 불러온 뒤 수정하세요.</p>:<ErrorState error={save.error}/>)}
  </form>;
}
