import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { me } from '../api/platform';
import { getProject, type ProjectDetail } from '../api/tft';
import { composition, decide, decisions, verification, type Composition, type Decision } from '../api/decisions';
import { Empty, ErrorState, Loading, Preparing } from '../components/states';
import { Origin, formatDateTime } from '../components/tft';

const STATES:Record<string,string>={NEEDS_REVIEW:'검토 필요',PARTIALLY_CONFIRMED:'부분 확정 — 전체 미확정',READY_FOR_FINAL_REVIEW:'최종 검토 가능 — 전체 미확정',CONFIRMED:'전체 확정',PROPOSED:'제안',NEEDS_REINFORCEMENT:'보완 필요',UNSPECIFIED:'미기재',NEEDS_CHECK:'미확인',UNMET:'미충족'};
const CHECKS:Record<string,string>={CONFIRMED:'확인',UNSPECIFIED:'미기재',NEEDS_CHECK:'미확인',UNMET:'미충족'};
const reasonLabel=(code:string)=>{const known:Record<string,string>={NO_RECOMMENDATION:'추천 후보 없음',HEADCOUNT_UNSPECIFIED:'필요 인원 미정',INSUFFICIENT_RECOMMENDATIONS:'추천 인원 부족',OVERLAPPING_ROLE_PROPOSALS:'동일 기간 다른 역할에 중복 제안',CONFIRMATION_NEEDS_REVIEW:'기존 확정 후 확인 정보 변경 — 재검토 필요'};if(known[code])return known[code];for(const [prefix,label] of Object.entries({EXPERIENCE:'수행 경험',REQUIRED_COMPETENCY:'필수 역량',AVAILABILITY:'가용성'})){if(code.startsWith(prefix+'_'))return `${label} ${CHECKS[code.slice(prefix.length+1)]??code}`;}return code;};
const ACTIONS:Record<Decision,string>={confirm:'최종 승인',reject:'반려',reopen:'재검토 시작'};
export default function CompositionReview(){
  const {projectId=''}=useParams();
  return import.meta.env.DEV ? <CompositionPanel projectId={projectId}/> : <Preparing title="구성안 검토"/>;
}
export function CompositionPanel({projectId}:{projectId:string}){
  const cache=useQueryClient();const mode=getApiMode();
  const user=useQuery({queryKey:['me'],queryFn:me,retry:false});
  const allowed=user.isSuccess && user.data?.data.roles.some(r=>r==='sales'||r==='manager')===true;
  const project=useQuery({queryKey:['projects',mode,projectId],queryFn:()=>getProject(projectId),enabled:allowed,retry:false});
  const report=useQuery({queryKey:['verification',projectId],queryFn:()=>verification(projectId),enabled:allowed && project.isSuccess,retry:false});
  const data=project.isSuccess?project.data.data:null;
  const assessment=report.isSuccess?report.data.data:null;
  const [action,setAction]=useState<Decision|null>(null);const [reason,setReason]=useState('');
  const [busy,setBusy]=useState(false);const inFlight=useRef(false);const [denied,setDenied]=useState(false);
  const [error,setError]=useState<unknown>(null);const [notice,setNotice]=useState('');
  const responsible=Boolean(data && data.responsible_user_id===user.data?.data.id);
  const locked=!allowed || !responsible || denied || !data || !assessment || project.isFetching || report.isFetching || busy || assessment.composition_version!==data.current_composition?.version;
  async function save(){
    if(locked || inFlight.current || !action || !reason.trim() || !data?.current_composition)return;
    inFlight.current=true;setBusy(true);setError(null);setNotice('');
    try{
      await decide(projectId,data.version,action,data.current_composition.version,reason.trim());
      setAction(null);setReason('');setNotice('결정을 저장했습니다. 최신 구성과 검증 상태를 확인하세요.');
      await Promise.all([cache.invalidateQueries({queryKey:['projects',mode,projectId]}),cache.invalidateQueries({queryKey:['verification',projectId]}),cache.invalidateQueries({queryKey:['decisions',projectId]})]);
    }catch(e){setError(e);
      if(e instanceof ApiError && [401,403,404].includes(e.status)){setDenied(true);void cache.invalidateQueries({queryKey:['me']});}
      if(e instanceof ApiError && [409,412].includes(e.status)){setAction(null);setNotice('판본 또는 상태가 변경됐습니다. 입력 사유를 보존했습니다. 최신 내용을 검토하고 결정을 다시 선택하세요.');await Promise.all([project.refetch(),report.refetch()]);}
    }finally{inFlight.current=false;setBusy(false);}
  }
  return <main><p><Link to={`/tft/projects/${projectId}`}>프로젝트 현황</Link></p><h1>구성안 비교와 책임자 결정</h1>
    {user.isPending && <Loading label="접근 권한 확인 중"/>}{user.isError && <ErrorState error={user.error} onRetry={()=>user.refetch()}/>}
    {user.isSuccess&&!allowed&&<ErrorState error={new ApiError(403,'프로젝트 담당자 또는 관리 범위의 관리자만 검토할 수 있습니다.')}/>}
    {allowed&&<>{project.isPending&&<Loading/>}{project.isError&&<ErrorState error={project.error} onRetry={()=>project.refetch()}/>}
      {data&&<><h2>{data.name}</h2><p>프로젝트 판본 {data.version} · 책임자 {data.responsible_user_id??'미지정'}</p><Origin dataOrigin={data.data_origin} derivationKind={data.derivation_kind}/>
        {!responsible&&<p role="note">현재 지정된 책임자만 최종 결정을 내릴 수 있습니다. 책임자 지정은 별도 권한 연결이 필요합니다.</p>}
        {report.isPending&&<Loading label="검증 상태 확인 중"/>}{report.isError&&<ErrorState error={report.error} onRetry={()=>report.refetch()}/>}
        {assessment&&<section><h2>팀 공백과 다음 행동</h2><p role="status">{STATES[assessment.state]}</p><p>확정 역할 {assessment.confirmed_role_count} / {assessment.total_role_count}. 부분 확정은 전체 완료가 아닙니다.</p>
          {assessment.roles.map(r=><article key={r.role_id}><h3>{data.required_roles.find(x=>x.id===r.role_id)?.title??r.role_id}</h3><p>확정 인원 {r.confirmed_count} / {r.required_count??'미정'}</p><p>{r.reasons.map(reasonLabel).join(' · ')||'판정 사유 없음'}</p>
            {r.candidates.length===0&&<Empty message="추천 후보 없음"/>}
            {r.candidates.map(c=><div key={c.recommendation_id}><strong>{data.recommendations.find(x=>x.id===c.recommendation_id)?.candidate_name??c.candidate_id}</strong><p>경험 {CHECKS[c.experience]??c.experience} · 필수 역량 {CHECKS[c.required_competencies]??c.required_competencies} · 우대 역량 {CHECKS[c.preferred_competencies]??c.preferred_competencies} · 가용성 {CHECKS[c.availability]??c.availability}</p><p>{c.reasons.map(reasonLabel).join(' · ')}</p>{c.overlapping_role_ids.length>0&&<p>다른 역할과 기간 중복 제안: {c.overlapping_role_ids.join(', ')}. 전사 실제 투입 중복 판정은 아닙니다.</p>}</div>)}
            <p>다음 행동: {r.next_action==='DEPARTMENT_CHECK'?'부서 확인':r.next_action==='ASSIGN_RESPONSIBLE'?'책임자 지정':'책임자 검토'} · 담당 사용자 {r.next_user_id??'미지정'} · 부서 {r.next_organization_ids.map(id=>data.staffing_requests.find(x=>x.organization_id===id)?.organization_name??id).join(', ')||'미정'}</p>
          </article>)}
        </section>}
        <CompositionTable title="현재 구성안" value={data.current_composition} project={data}/>
        <Compare project={data}/>
        <section><h2>최종 결정</h2><p>근거와 가용성 변경은 기존 확정 판본을 보존하며 재검토해야 합니다.</p>
          {notice&&<p role="status">{notice}</p>}{error!=null&&<ErrorState error={error}/>}
          <label>결정 사유<textarea value={reason} maxLength={4000} disabled={busy} onChange={e=>setReason(e.target.value)}/></label>
          {!action?<><button disabled={locked||data.status==='CONFIRMED'||assessment?.state!=='READY_FOR_FINAL_REVIEW'} onClick={()=>setAction('confirm')}>최종 승인</button><button disabled={locked||data.status==='CONFIRMED'} onClick={()=>setAction('reject')}>반려</button><button disabled={locked||data.status!=='CONFIRMED'} onClick={()=>setAction('reopen')}>재검토 시작</button></>:<div><p>구성안 판본 {data.current_composition?.version}에 {ACTIONS[action]}을 적용할까요?</p><button disabled={locked||!reason.trim()} onClick={()=>void save()}>{busy?'저장 중':`${ACTIONS[action]} 확인`}</button><button disabled={busy} onClick={()=>{setAction(null);setError(null);}}>취소</button></div>}
        </section>
        <section><h2>추천 근거</h2>{data.recommendations.map(r=><article key={r.id}><h3>{r.candidate_name} · {r.status}</h3><p>{r.rationale??'추천 사유 없음'}</p><Origin dataOrigin={r.data_origin} derivationKind={r.derivation_kind}/>{r.experience_evidence.map(e=><p key={e.id}>{e.summary} · {e.basis??'근거 미기재'} · {e.status} · {e.verified_by_user_id??'확인자 미기재'} · {formatDateTime(e.verified_at)}</p>)}{r.competency_checks.map(e=><p key={e.id}>역량 {e.competency_id} · {e.status} · {e.basis??'근거 미기재'} · {e.checked_by_user_id??'확인자 미기재'}</p>)}{r.availability_checks.map(e=><p key={e.id}>가용성 {e.period_start} ~ {e.period_end} · {e.allocation_rate??'미정'}% · {e.status} · {e.basis??'근거 미기재'}</p>)}</article>)}</section>
        <History projectId={projectId}/>
      </>}
    </>}
  </main>;
}
function CompositionTable({title,value,project}:{title:string;value:Composition|null;project:ProjectDetail}){
  return <section><h2>{title}</h2>{!value?<Empty message="구성안 없음"/>:<><p>판본 {value.version} · {formatDateTime(value.created_at)} · 작성자 {value.created_by_user_id}</p><p>{value.note}</p><Origin dataOrigin={value.data_origin} derivationKind={value.derivation_kind}/><table><thead><tr><th>역할</th><th>후보</th><th>상태</th><th>사유와 남은 조건</th><th>다음 행동</th></tr></thead><tbody>{value.members.map(m=><tr key={m.id}><td>{project.required_roles.find(r=>r.id===m.required_role_id)?.title??m.required_role_id}</td><td>{m.recommendation_id?project.recommendations.find(r=>r.id===m.recommendation_id)?.candidate_name??m.recommendation_id:'미추천 공백'}</td><td>{m.status==='CONFIRMED'?'역할 확정':STATES[m.status]??m.status}</td><td>{[m.rationale,m.remaining_conditions,m.gap_note].filter(Boolean).join(' · ')||'미기재'}</td><td>{m.next_action_organization_id??'미지정'} · {formatDateTime(m.follow_up_due_at)}</td></tr>)}</tbody></table></>}</section>;
}
function Compare({project}:{project:ProjectDetail}){
  const [input,setInput]=useState('');const [version,setVersion]=useState<number>();
  const query=useQuery({queryKey:['composition',project.id,version],queryFn:()=>composition(project.id,version!),enabled:version!==undefined,retry:false});
  return <section><h2>과거 판본 비교</h2><p>과거 구성안은 저장된 내용이며 현재 검증 판정과 구분됩니다.</p><form onSubmit={e=>{e.preventDefault();const n=Number(input);if(Number.isSafeInteger(n)&&n>0)setVersion(n);}}><label>비교할 판본<input type="number" min="1" step="1" required value={input} onChange={e=>setInput(e.target.value)}/></label><button disabled={query.isFetching}>판본 조회</button></form>{version!==undefined&&query.isPending&&<Loading/>}{query.isError&&<ErrorState error={query.error}/>} {query.isSuccess&&<CompositionTable title="과거 구성안" value={query.data.data} project={project}/>}</section>;
}
function History({projectId}:{projectId:string}){
  const [after,setAfter]=useState(0);const query=useQuery({queryKey:['decisions',projectId,after],queryFn:()=>decisions(projectId,after),retry:false});
  return <section><h2>결정 이력</h2>{query.isPending&&<Loading/>}{query.isError&&<ErrorState error={query.error} onRetry={()=>query.refetch()}/>} {query.data?.data.map(d=><p key={d.id}>프로젝트 판본 {d.project_version} · 구성안 {d.composition_version??'없음'} · {d.action} · {d.reason} · 사용자 {d.actor_user_id} · {formatDateTime(d.occurred_at)}</p>)}{after>0&&<button onClick={()=>setAfter(0)}>처음 이력</button>}{query.data?.data.length===100&&<button onClick={()=>setAfter(query.data!.data.at(-1)!.project_version)}>다음 이력</button>}</section>;
}
