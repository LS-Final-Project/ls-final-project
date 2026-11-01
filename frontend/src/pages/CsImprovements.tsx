import { useState } from 'react';
import ImprovementCreator from '../components/ImprovementCreator';
import ImprovementEditor from '../components/ImprovementEditor';
import ImprovementVersions from '../components/ImprovementVersions';
import ImprovementTransitions, { type TransitionMessage } from '../components/ImprovementTransitions';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getImprovement, listImprovements } from '../api/cs-improvements';
import { ErrorState, Loading, Preparing } from '../components/states';
import { KvGrid, KvKey, KvTitle, type KvColumn } from '../ls-shell/kv';
import { REQUEST_STATUS_LABEL, REQUEST_TYPE_LABEL } from '../ls-shell/cs-labels';
import type { RequestSummary } from '../ls-shell/cs-labels';
export default function CsImprovements(){
  const {requestId}=useParams();
  if(!import.meta.env.DEV)return <Preparing title="개선요청" message="운영 인증 중계와 권한 검증 후 제공됩니다."/>;
  return <ImprovementView key={requestId??'list'}/>;
}
function ImprovementView(){
  const client=useQueryClient();
  const navigate=useNavigate();
  const {requestId}=useParams();
  const mode=getApiMode();
  const [editing,setEditing]=useState(false);
  const [transitionMessage,setTransitionMessage]=useState<TransitionMessage>(null);
  const list=useQuery({queryKey:['cs-improvements',mode,'list'],queryFn:({signal})=>listImprovements(signal),enabled:!requestId&&!editing,retry:false,staleTime:0});
  const detail=useQuery({queryKey:['cs-improvements',mode,requestId],queryFn:({signal})=>getImprovement(requestId!,signal),enabled:!!requestId&&!editing,retry:false,staleTime:0});
  const active=requestId?detail:list;
  const d=requestId&&!detail.isFetching&&detail.isSuccess?detail.data.data:null;
  const r=d?.request;
  const listColumns:KvColumn<RequestSummary>[]=[
    {key:'no',header:'No',width:40,className:'n',cell:(_item,index)=>index+1},
    {key:'title',header:'문제',className:'t',headClass:'',cell:item=><Link to={`/cs/improvements/${encodeURIComponent(item.id)}`}>{item.problem_statement}</Link>},
    {key:'type',header:'유형',width:64,className:'c',cell:item=>REQUEST_TYPE_LABEL[item.request_type]},
    {key:'product',header:'제품',width:150,headClass:'',cell:item=>item.product_code},
    {key:'status',header:'상태',width:110,className:'c',cell:item=>REQUEST_STATUS_LABEL[item.status]??item.status},
    {key:'version',header:'판본',width:56,className:'c',cell:item=>item.version},
    {key:'evidence',header:'근거',width:64,className:'c',cell:item=>`${item.evidence_count}건`},
    {key:'owner',header:'담당',width:230,headClass:'',cell:item=>`담당자 ID: ${item.cs_owner??'미배정'}`},
  ];
  return <main>
    <KvTitle path="C/S 관리" title={`개선요청 ${requestId?'상세':'목록'}`} code="CS0301">
      <button className="kb primary" disabled={active.isFetching||editing} onClick={()=>active.refetch()}>최신 요청 조회<KvKey>F8</KvKey></button>
      <Link className="kb" to="/cs/tickets">C/S 문의</Link>
      {requestId&&<Link className="kb" to="/cs/improvements">개선요청 목록</Link>}
    </KvTitle>
    {mode==='mock'&&<div className="kv-sub" role="status" style={{margin:'6px 14px 0'}}>모의 응답 모드 — 계약 예시이며 실제 업무 기록이 아닙니다.</div>}
    <div className="kv-sub" style={{margin:'6px 14px 0'}}>저장된 요청을 조회합니다. 문의 종결, 부서 조치, 고객 적용 및 재발 확인은 서로 다른 기록입니다.</div>
    {editing&&<div className="kv-sub" style={{margin:'6px 14px 0'}}>작성·수정 중에는 입력 보존을 위해 자동 조회를 잠시 멈춥니다. 저장·취소 후 최신 내용을 확인하세요.</div>}
    {active.isFetching&&<Loading/>}{!active.isFetching&&active.isError&&<ErrorState error={active.error} onRetry={()=>active.refetch()}/>}
    {!requestId&&!list.isFetching&&list.isSuccess&&<>
      <div className="kv-gridhead"><span>최신 요청 (최대 200건)</span></div>
      <KvGrid label="개선요청 목록" columns={listColumns} rows={list.data.data.items} rowKey={item=>item.id} fallback="조회 가능한 개선요청이 없습니다."/>
    </>}
    {!requestId&&!list.isFetching&&list.isSuccess&&<div className="ls-legacy ls-compact" style={{minHeight:0,padding:'0 14px'}}><ImprovementCreator onReload={()=>{void list.refetch();}} onEditingChange={setEditing} onCreated={result=>{void client.invalidateQueries({queryKey:['cs-improvements',mode,'list']});navigate(`/cs/improvements/${encodeURIComponent(result.data.request.id)}`);}}/></div>}
    {r&&d&&<div className="ls-legacy ls-compact" style={{minHeight:0,padding:'0 14px'}}><>
      <section><h2>{r.problem_statement}</h2><dl><dt>유형</dt><dd>{r.request_type==='FAULT'?'고장':'문서'}</dd><dt>제품</dt><dd>{r.product_code}</dd><dt>고장 / 문서 신호</dt><dd>{r.fault_id??r.doc_signal??'미확인'}</dd><dt>문서 ID</dt><dd>{r.doc_id??'미확인'}</dd><dt>상태 / 판본 / 회차</dt><dd>{r.status} / {r.version} / {r.current_round}</dd><dt>출처</dt><dd>{r.data_origin} · {r.derivation_kind}</dd><dt>조건 확인</dt><dd>{r.condition_status==='UNKNOWN'?'미확인':r.condition_status}</dd><dt>사용 조건</dt><dd>{r.condition_summary??'미확인'}</dd><dt>수정 FW / 배포일</dt><dd>{r.fw_fixed_version??'미확인'} / {r.fw_released_on??'미확인'}</dd><dt>확인된 사실</dt><dd>{r.confirmed_facts??'기록 없음'}</dd><dt>불일치</dt><dd>{r.inconsistencies??'기록 없음'}</dd><dt>부족한 정보</dt><dd>{r.missing_info??'기록 없음'}</dd><dt>단정하지 않는 내용</dt><dd>{r.not_claimed??'기록 없음'}</dd><dt>검토 과제</dt><dd>{r.review_task??'기록 없음'}</dd><dt>제안 부서</dt><dd>{r.proposed_depts.join(', ')||'미확인'}</dd></dl>{r.receiver_unconfirmed&&<p>수신 부서 미확인 — 전달 준비가 끝난 상태가 아닙니다.</p>}</section>
      <section><h2>다음 담당과 할 일 (API 조회 결과)</h2>{d.next_owners.length?d.next_owners.map((n,i)=><p key={i}>{n.actor} · {n.user_id??n.dept_code??'담당자 미배정'} · {n.task}</p>):<p>API가 반환한 다음 작업 없음</p>}<p>표시된 다음 작업을 실행하거나 완료 처리하지 않습니다.</p></section>
      <section><h2>근거 문의</h2>{d.evidence.length?d.evidence.map(e=><article key={e.id}><Link to={`/cs/tickets/${encodeURIComponent(e.ticket_id)}`}>{e.ticket_external_id??e.ticket_id}</Link><p>해결 사례: {e.case_external_id??e.case_id??'연결 없음'} · {e.removed_at?`제외됨: ${e.removal_reason??'사유 미확인'}`:'유효한 근거'}</p></article>):<p>연결된 근거 없음</p>}</section>
      <section><h2>현재 회차 수신 부서</h2>{d.departments.length?d.departments.map(x=><p key={`${x.round}-${x.dept_code}`}>{x.dept_code} · {x.status} · 전달 판본 {x.sent_version??'미확인'} · 기한 {x.reply_due_at??'미확인'} · {x.carried_over?'이월':'이번 회차'}</p>):<p>수신 부서 기록 없음</p>}<h3>부서 회신</h3>{d.replies.length?d.replies.map(x=><p key={x.id}>{x.dept_code} · 회차 {x.round} · 판본 {x.request_version} · {x.reply_type} · {x.reason??'사유 기록 없음'}</p>):<p>회신 기록 없음</p>}</section>
      <section><h2>부서 조치</h2>{d.actions.length?d.actions.map(x=><article key={x.id}><h3>{x.dept_code} · {x.action_date}</h3><p>{x.action}</p><p>적용 대상: {x.action_scope}</p><p>근거: {x.action_evidence}</p></article>):<p>조치 기록 없음</p>}<h3>후속 확인 (고객 적용·재발 확인과 구분)</h3><p>계획: {r.followup_plan??'미기록'}</p><p>결과: {r.followup_result??'미기록'}</p><p>고객별 적용·재발 여부는 이 응답만으로 확인할 수 없습니다.</p></section>
      <ImprovementEditor onEditingChange={setEditing} key={`${r.id}:${r.version}`} record={r} onSaved={result=>{client.setQueryData(['cs-improvements',mode,requestId],result);void client.invalidateQueries({queryKey:['cs-improvement-versions',mode,r.id]});void client.invalidateQueries({queryKey:['cs-improvements',mode,'list']});}} onReload={()=>{void detail.refetch();}}/>
      <ImprovementVersions key={r.id} requestId={r.id}/>
      <ImprovementTransitions detail={d} message={transitionMessage} setMessage={setTransitionMessage} onChanged={result=>{if(result)client.setQueryData(['cs-improvements',mode,requestId],result);else void detail.refetch();void client.invalidateQueries({queryKey:['cs-improvement-versions',mode,r.id]});void client.invalidateQueries({queryKey:['cs-improvements',mode,'list']});}}/>
    </></div>}
  </main>;
}
