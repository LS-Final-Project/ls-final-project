import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, getApiMode } from '../api/client';
import { CONTEXT_CATEGORIES, getContext, processContext, updateContext, type ContextAssignment, type ContextCategory, type ContextEnvelope, type ContextGroup, type ContextSource } from '../api/rfp-context';
import type { DocumentVersion } from '../api/rfp';
import { ErrorState, Loading } from '../components/states';
import { settled, useAttemptKey } from './proposal-shared';
import { STATUS_LABEL, label } from './labels';
import './ContextReview.css';

const unique = (ids:string[]) => [...new Set(ids)];
export function ContextReview({projectId,actorId,document,documents,busy=false,onStarted,onSource}: {
  projectId:string;actorId?:string;document:DocumentVersion;documents:DocumentVersion[];busy?:boolean;
  onStarted?:(job:{job_id:string})=>void;onSource:(source:ContextSource)=>void;
}) {
  const mode=getApiMode(), client=useQueryClient();
  const query=useQuery({queryKey:['rfp-context',mode,projectId,actorId],queryFn:({signal})=>getContext(projectId,signal),retry:false});
  const [tab,setTab]=useState<'groups'|'categories'>('groups');
  const [scope,setScope]=useState<'document'|'project'>('document');
  const [category,setCategory]=useState<ContextCategory>('EQUIPMENT');
  const [groupId,setGroupId]=useState<string>();
  const [edit,setEdit]=useState<string>();
  const [pagination,setPagination]=useState({key:'',page:1});
  const [key,nextKey]=useAttemptKey(projectId);const sending=useRef(false);
  const process=useMutation({mutationFn:()=>processContext(projectId,key),onSuccess:r=>{nextKey();onStarted?.(r.data);},onError:e=>{if(settled(e))nextKey();}});
  const data=query.data?.data.project_id===projectId?query.data.data:undefined;
  const result=data?.result;
  const requirementMap=new Map((data?.requirements??[]).map(r=>[r.requirement_id,r]));
  const refs=useMemo(()=>new Map((data?.source_refs??[]).map(r=>[r.requirement_id,r])),[data]);
  const assignments=useMemo(()=>[...new Map((result?.assignments??[]).map(a=>[a.requirement_id,a])).values()].filter(a=>scope==='project'||refs.get(a.requirement_id)?.document_version_id===document.document_version_id),[result,scope,refs,document.document_version_id]);
  const groups=result?.groups??[];
  const group=groups.find(g=>g.entity_id===groupId);
  const filtered=assignments.filter(a=>tab==='categories'?(a.primary_category===category||a.additional_categories.includes(category as Exclude<ContextCategory,'UNCLASSIFIED'>)):!groupId||a.group_ids.includes(groupId));
  const pageKey=JSON.stringify([projectId,document.document_version_id,scope,tab,category,groupId]);
  useEffect(()=>{setPagination({key:pageKey,page:1});setEdit(undefined);},[pageKey]);
  const pages=Math.max(1,Math.ceil(filtered.length/20));
  const page=pagination.key===pageKey?Math.min(pagination.page,pages):1;
  const pageStart=Math.max(1,Math.min(page-2,pages-4));
  const pageNumbers=Array.from({length:Math.min(5,pages)},(_,i)=>pageStart+i);
  const changePage=(value:number)=>{setPagination({key:pageKey,page:Math.max(1,Math.min(value,pages))});setEdit(undefined);};
  const run=()=>{if(sending.current||busy||!onStarted)return;sending.current=true;process.mutate(undefined,{onSettled:()=>{sending.current=false;}});};
  const sourceButton=(id:string)=>{const r=refs.get(id);if(!r)return <span key={id}>근거 위치 미확인</span>;const available=documents.some(d=>d.document_version_id===r.document_version_id&&d.document_id===r.document_id);return <button key={id} type="button" className="kb" disabled={!available} onClick={()=>onSource(r)}>{r.code} 원문{r.document_version_id!==document.document_version_id?' · 문서 이동':''}{!available?' · 이전 판본 없음':''}</button>;};
  return <section className="rfp-context" aria-label="맥락별 분석 검토">
    <header><h3>맥락별 정리</h3><button type="button" className="kb" disabled={query.isFetching} onClick={()=>query.refetch()}>맥락 새로고침</button><button className="kb" disabled={busy||process.isPending||!onStarted||!actorId} onClick={run}>맥락별 정리</button></header>
    <p>프로젝트 전체의 기존 추출 요구를 AI가 묶습니다. 원문을 다시 추출하거나 기존 대응 판정을 바꾸지 않습니다. 실행 시 모델 사용 비용이 발생합니다.</p>
    {process.isError&&<ErrorState error={process.error}/>}
    {query.isError?<ErrorState error={query.error} onRetry={()=>query.refetch()}/>:query.isPending?<Loading label="맥락 정리 읽는 중"/>:!data?<p role="alert">프로젝트가 일치하는 맥락 결과를 확인할 수 없습니다.</p>:<>
      {data.status==='NOT_ANALYZED'||!result?<p role="status">아직 맥락별 정리를 실행하지 않았습니다. 기존 요구가 준비된 뒤 실행하세요.</p>:<>
        <p role="status">맥락 판본 {data.version} · {data.provenance==='human_input'?'사람 수정':'AI 정리 제안'} · {result.outcome==='PARTIAL'?'일부 정리 · 확인 필요':'정리 결과 · 검토 필요'}. 담당자 승인과 다릅니다.</p>
        {data.status==='STALE'&&<p className="rfp-context-warning" role="alert">원문 또는 요구 판본이 바뀐 이전 결과입니다. 현재 자료로 다시 정리하기 전에는 수정할 수 없습니다.</p>}
        {data.reason&&<p>수정 사유: {data.reason}</p>}
        <label>표시 범위<select value={scope} onChange={e=>{setScope(e.target.value as typeof scope);setGroupId(undefined);}}><option value="document">선택 문서의 요구만</option><option value="project">프로젝트 전체 문서</option></select></label>
        <p>{scope==='document'?`선택 문서: ${document.title} v${document.version}`:'프로젝트 전체 문서'} · 고유 요구 {assignments.length}건. 공통 요구는 여러 묶음에 연결돼도 전체 건수에 한 번만 셉니다.</p>
        <div role="tablist" aria-label="맥락 보기"><button type="button" role="tab" aria-selected={tab==='groups'} onClick={()=>setTab('groups')}>설비·과업 구조</button><button type="button" role="tab" aria-selected={tab==='categories'} onClick={()=>setTab('categories')}>8개 분류</button></div>
        {tab==='groups'?<div className="rfp-context-groups"><button type="button" aria-pressed={!groupId} onClick={()=>setGroupId(undefined)}>전체 구조</button>{groups.map(g=><button type="button" key={g.entity_id} aria-pressed={groupId===g.entity_id} onClick={()=>setGroupId(g.entity_id)}><span>{groupPath(g,groups)}</span><small>{({SYSTEM:'계통',EQUIPMENT:'설비',TASK:'과업',COMMON:'공통'})[g.kind]} · {assignments.filter(a=>a.group_ids.includes(g.entity_id)).length}개 요구{g.needs_review?' · 확인 필요':''}</small></button>)}</div>:<div className="rfp-context-categories">{Object.entries(CONTEXT_CATEGORIES).map(([code,name])=><button type="button" key={code} aria-pressed={category===code} onClick={()=>setCategory(code as ContextCategory)}>{name} {assignments.filter(a=>a.primary_category===code||a.additional_categories.includes(code as Exclude<ContextCategory,'UNCLASSIFIED'>)).length}</button>)}</div>}
        {group&&tab==='groups'&&<article className="rfp-context-group-detail"><strong>{group.name}</strong><p>{[group.tag&&`태그 ${group.tag}`,group.site&&`위치 ${group.site}`].filter(Boolean).join(' · ')||'태그·위치 미확정'}</p><p>묶음 근거</p><div className="rfp-context-sources">{unique(group.evidence_requirement_ids).map(sourceButton)}</div>{group.diagram_source_ids.length>0&&<p>도면 근거 {unique(group.diagram_source_ids).length}개 · 위 단선결선도 검토에서 원본과 연결 후보를 대조하세요.</p>}</article>}
        <div className="rfp-context-assignments">{filtered.length===0?<p>이 범위에 연결된 요구가 없습니다. 분류·표시 범위를 바꿔 확인하세요.</p>:filtered.slice((page-1)*20,page*20).map(a=><article key={a.requirement_id}><h4>{refs.get(a.requirement_id)?.code??'요구 위치 미확인'} · {CONTEXT_CATEGORIES[a.primary_category]}</h4><p>{[requirementMap.get(a.requirement_id)?.equipment,requirementMap.get(a.requirement_id)?.task,requirementMap.get(a.requirement_id)?.attribute,requirementMap.get(a.requirement_id)?.value,requirementMap.get(a.requirement_id)?.unit].filter(Boolean).join(' · ')||'요구 내용 확인 필요'}</p><blockquote>{refs.get(a.requirement_id)?.quote||'원문 인용 미확인'}</blockquote>{requirementMap.get(a.requirement_id)?.status&&<small>정리 당시 대응 판정: {label(STATUS_LABEL,requirementMap.get(a.requirement_id)!.status)} · 이 화면에서 판정은 바뀌지 않습니다.</small>}<p>정리 근거: {a.reason}</p><p>{a.group_ids.map(id=>groups.find(g=>g.entity_id===id)?.name??'묶음 미확인').join(' · ')||'설비·과업 연결 미확정'}</p>{a.relations.length>0&&<p>연결 후보: {a.relations.map(r=>`${groups.find(g=>g.entity_id===r.group_id)?.name??'묶음 미확인'} — ${{SAME_EQUIPMENT:'동일 설비 후보',PART_OF:'하위 구성 후보',APPLIES_TO:'적용 대상',RELATED:'참고 연결'}[r.relation]}`).join(' / ')}</p>}{a.additional_categories.length>0&&<p>함께 적용: {a.additional_categories.map(c=>CONTEXT_CATEGORIES[c]).join(', ')}</p>}{a.needs_review&&<p className="rfp-context-warning">담당자 확인 필요</p>}<div className="rfp-context-sources">{unique([a.requirement_id,...a.evidence_requirement_ids]).map(sourceButton)}</div><button type="button" className="kb" disabled={busy||data.status!=='CURRENT'||!actorId} onClick={()=>setEdit(a.requirement_id)}>분류·묶음 수정</button>{edit===a.requirement_id&&<AssignmentEditor key={`${data.version}:${a.requirement_id}`} busy={busy} data={data} assignment={a} projectId={projectId} onCancel={()=>setEdit(undefined)} onSaved={next=>{client.setQueryData(['rfp-context',mode,projectId,actorId],{data:next});setEdit(undefined);}}/>}</article>)}</div>
        {filtered.length>0&&<nav className="rfp-context-pagination" aria-label="맥락 요구 목록 페이지"><span>총 {filtered.length}건 · 한 페이지 20건 · {page}쪽</span><div><button type="button" className="kb" disabled={page===1} onClick={()=>changePage(page-1)}>이전</button>{pageStart>1&&<><button type="button" className="kb" aria-label="1페이지" onClick={()=>changePage(1)}>1</button><span>…</span></>}{pageNumbers.map(n=><button type="button" className="kb" key={n} aria-label={`${n}페이지`} aria-current={page===n?'page':undefined} onClick={()=>changePage(n)}>{n}</button>)}{pageStart+pageNumbers.length<=pages&&<><span>…</span><button type="button" className="kb" aria-label={`${pages}페이지`} onClick={()=>changePage(pages)}>{pages}</button></>}<button type="button" className="kb" disabled={page===pages} onClick={()=>changePage(page+1)}>다음</button></div></nav>}
        {result.issues.length>0&&<details><summary>미확정·누락 안내 {result.issues.length}건</summary><ul>{result.issues.map((issue,i)=><li key={i}>{issue.message}</li>)}</ul></details>}
      </>}
    </>}
  </section>;
}
function groupPath(group:ContextGroup,groups:ContextGroup[]) {const path=[group.name],seen=new Set([group.entity_id]);let parent=group.parent_id;while(parent&&!seen.has(parent)){seen.add(parent);const next=groups.find(g=>g.entity_id===parent);if(!next)break;path.unshift(next.name);parent=next.parent_id;}return path.join(' › ');}
function AssignmentEditor({data,assignment,projectId,onCancel,onSaved,busy}:{busy:boolean;data:ContextEnvelope;assignment:ContextAssignment;projectId:string;onCancel:()=>void;onSaved:(data:ContextEnvelope)=>void}) {
  const [category,setCategory]=useState(assignment.primary_category),[groupIds,setGroupIds]=useState(assignment.group_ids),[reason,setReason]=useState('');
  const result={...data.result!,outcome:'PARTIAL' as const,assignments:data.result!.assignments.map(a=>a.requirement_id!==assignment.requirement_id?a:{...a,primary_category:category,additional_categories:a.additional_categories.filter(c=>c!==category),group_ids:groupIds,relations:groupIds.map(group_id=>a.relations.find(r=>r.group_id===group_id)??{group_id,relation:'RELATED' as const}),reason:reason.trim(),needs_review:true})};
  const body={expected_version:data.version!,expected_source_fingerprint:data.source_fingerprint!,reason:reason.trim(),result};
  const [key,nextKey]=useAttemptKey(JSON.stringify(body));const sending=useRef(false);
  const save=useMutation({mutationFn:()=>updateContext(projectId,body,key),onSuccess:r=>{nextKey();onSaved(r.data);},onError:e=>{if(settled(e))nextKey();}});
  return <form className="rfp-context-editor" onSubmit={e=>{e.preventDefault();if(sending.current||busy||!reason.trim())return;sending.current=true;save.mutate(undefined,{onSettled:()=>{sending.current=false;}});}}>
    <label>주 분류<select value={category} onChange={e=>setCategory(e.target.value as ContextCategory)}>{Object.entries(CONTEXT_CATEGORIES).map(([code,name])=><option key={code} value={code}>{name}</option>)}</select></label>
    <fieldset><legend>연결할 기존 묶음</legend>{data.result!.groups.map(g=><label key={g.entity_id}><input type="checkbox" checked={groupIds.includes(g.entity_id)} onChange={e=>setGroupIds(e.target.checked?[...groupIds,g.entity_id]:groupIds.filter(id=>id!==g.entity_id))}/>{g.name}</label>)}</fieldset>
    <p>추가 묶음은 참고 연결로 기록합니다. 사람 수정도 동일 설비 확정이나 승인으로 처리하지 않습니다.</p>
    <label>수정 사유<textarea required maxLength={1000} value={reason} onChange={e=>setReason(e.target.value)}/></label>
    <button className="kb" type="button" disabled={save.isPending} onClick={onCancel}>취소</button><button className="kb" type="submit" disabled={busy||save.isPending||!reason.trim()}>새 판본으로 저장</button>
    {save.isError&&(save.error instanceof ApiError&&save.error.status===409?<p role="alert">다른 수정이나 원문 변경이 있습니다. 맥락 결과를 다시 불러온 뒤 수정하세요.</p>:<ErrorState error={save.error}/>)}
  </form>;
}
