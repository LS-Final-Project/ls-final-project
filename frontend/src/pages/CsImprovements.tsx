import ImprovementVersions from '../components/ImprovementVersions';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getImprovement, listImprovements } from '../api/cs-improvements';
import { ErrorState, Loading, Preparing } from '../components/states';
export default function CsImprovements(){
  const {requestId}=useParams();
  if(!import.meta.env.DEV)return <Preparing title="개선요청" message="운영 인증 중계와 권한 검증 후 제공됩니다."/>;
  return <ImprovementView key={requestId??'list'}/>;
}
function ImprovementView(){
  const {requestId}=useParams();
  const mode=getApiMode();
  const list=useQuery({queryKey:['cs-improvements',mode,'list'],queryFn:({signal})=>listImprovements(signal),enabled:!requestId,retry:false});
  const detail=useQuery({queryKey:['cs-improvements',mode,requestId],queryFn:({signal})=>getImprovement(requestId!,signal),enabled:!!requestId,retry:false});
  const active=requestId?detail:list;
  const d=requestId&&detail.isSuccess?detail.data.data:null;
  const r=d?.request;
  return <main><header><Link to="/cs/tickets">C/S 문의</Link> · <Link to="/cs/improvements">개선요청 목록</Link><h1>개선요청 {requestId?'상세':'목록'}</h1>{mode==='mock'&&<p role="status">모의 응답 모드 — 계약 예시이며 실제 업무 기록이 아닙니다.</p>}<p>저장된 요청을 조회합니다. 문의 종결, 부서 조치, 고객 적용 및 재발 확인은 서로 다른 기록입니다.</p></header>
    <button disabled={active.isFetching} onClick={()=>active.refetch()}>최신 요청 조회</button>
    {active.isFetching&&<Loading/>}{active.isError&&<ErrorState error={active.error} onRetry={()=>active.refetch()}/>}
    {!requestId&&list.isSuccess&&<section><h2>최신 요청 (최대 200건)</h2>{list.data.data.items.length?list.data.data.items.map(item=><article key={item.id}><h3><Link to={`/cs/improvements/${encodeURIComponent(item.id)}`}>{item.problem_statement}</Link></h3><p>{item.request_type==='FAULT'?'고장':'문서'} · {item.product_code} · {item.status} · 판본 {item.version} · 근거 {item.evidence_count}건</p><p>담당자 ID: {item.cs_owner??'미배정'}</p></article>):<p>조회 가능한 개선요청이 없습니다.</p>}</section>}
    {r&&d&&<>
      <section><h2>{r.problem_statement}</h2><dl><dt>유형</dt><dd>{r.request_type==='FAULT'?'고장':'문서'}</dd><dt>제품</dt><dd>{r.product_code}</dd><dt>고장 / 문서 신호</dt><dd>{r.fault_id??r.doc_signal??'미확인'}</dd><dt>문서 ID</dt><dd>{r.doc_id??'미확인'}</dd><dt>상태 / 판본 / 회차</dt><dd>{r.status} / {r.version} / {r.current_round}</dd><dt>출처</dt><dd>{r.data_origin} · {r.derivation_kind}</dd><dt>조건 확인</dt><dd>{r.condition_status==='UNKNOWN'?'미확인':r.condition_status}</dd><dt>사용 조건</dt><dd>{r.condition_summary??'미확인'}</dd><dt>수정 FW / 배포일</dt><dd>{r.fw_fixed_version??'미확인'} / {r.fw_released_on??'미확인'}</dd><dt>확인된 사실</dt><dd>{r.confirmed_facts??'기록 없음'}</dd><dt>불일치</dt><dd>{r.inconsistencies??'기록 없음'}</dd><dt>부족한 정보</dt><dd>{r.missing_info??'기록 없음'}</dd><dt>단정하지 않는 내용</dt><dd>{r.not_claimed??'기록 없음'}</dd><dt>검토 과제</dt><dd>{r.review_task??'기록 없음'}</dd><dt>제안 부서</dt><dd>{r.proposed_depts.join(', ')||'미확인'}</dd></dl>{r.receiver_unconfirmed&&<p>수신 부서 미확인 — 전달 준비가 끝난 상태가 아닙니다.</p>}</section>
      <section><h2>다음 담당과 할 일 (API 조회 결과)</h2>{d.next_owners.length?d.next_owners.map((n,i)=><p key={i}>{n.actor} · {n.user_id??n.dept_code??'담당자 미배정'} · {n.task}</p>):<p>API가 반환한 다음 작업 없음</p>}<p>이 화면은 작업을 실행하거나 완료 처리하지 않습니다.</p></section>
      <section><h2>근거 문의</h2>{d.evidence.length?d.evidence.map(e=><article key={e.id}><Link to={`/cs/tickets/${encodeURIComponent(e.ticket_id)}`}>{e.ticket_external_id??e.ticket_id}</Link><p>해결 사례: {e.case_external_id??e.case_id??'연결 없음'} · {e.removed_at?`제외됨: ${e.removal_reason??'사유 미확인'}`:'유효한 근거'}</p></article>):<p>연결된 근거 없음</p>}</section>
      <section><h2>현재 회차 수신 부서</h2>{d.departments.length?d.departments.map(x=><p key={`${x.round}-${x.dept_code}`}>{x.dept_code} · {x.status} · 전달 판본 {x.sent_version??'미확인'} · 기한 {x.reply_due_at??'미확인'} · {x.carried_over?'이월':'이번 회차'}</p>):<p>수신 부서 기록 없음</p>}<h3>부서 회신</h3>{d.replies.length?d.replies.map(x=><p key={x.id}>{x.dept_code} · 회차 {x.round} · 판본 {x.request_version} · {x.reply_type} · {x.reason??'사유 기록 없음'}</p>):<p>회신 기록 없음</p>}</section>
      <section><h2>부서 조치</h2>{d.actions.length?d.actions.map(x=><article key={x.id}><h3>{x.dept_code} · {x.action_date}</h3><p>{x.action}</p><p>적용 대상: {x.action_scope}</p><p>근거: {x.action_evidence}</p></article>):<p>조치 기록 없음</p>}<h3>후속 확인 (고객 적용·재발 확인과 구분)</h3><p>계획: {r.followup_plan??'미기록'}</p><p>결과: {r.followup_result??'미기록'}</p><p>고객별 적용·재발 여부는 이 응답만으로 확인할 수 없습니다.</p></section>
      <ImprovementVersions key={r.id} requestId={r.id}/>
      <Preparing inline title="검토·전달·조치 입력" message="작성·전달 API의 인증 연결과 후속 상태 전이 검증이 필요합니다. 이번 화면은 조회만 제공합니다."/>
    </>}
  </main>;
}
