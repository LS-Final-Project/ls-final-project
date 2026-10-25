import { useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { createComposition, type Assessment, type CompositionInput } from '../api/decisions';
import type { ProjectDetail } from '../api/tft';
import { ErrorState } from '../components/states';
type Member=CompositionInput['members'][number];
export default function CompositionEditor({project,assessment,userId,disabled,onRefresh}:{project:ProjectDetail;assessment:Assessment|null;userId:string;disabled:boolean;onRefresh:()=>Promise<unknown>}){
  const [members,setMembers]=useState<Member[]>(()=>project.current_composition?.members.map(({required_role_id,recommendation_id,status,rationale,remaining_conditions,gap_note,next_action_organization_id,follow_up_due_at})=>({required_role_id,recommendation_id,status,rationale,remaining_conditions,gap_note,next_action_organization_id,follow_up_due_at}))??[]);
  const [note,setNote]=useState('');const [confirming,setConfirming]=useState(false);const [busy,setBusy]=useState(false);const active=useRef(false);
  const [error,setError]=useState<unknown>(null);const [notice,setNotice]=useState('');const [denied,setDenied]=useState(false);
  const locked=disabled||denied||busy||project.status==='CONFIRMED'||project.required_roles.length===0;
  const isResponsible=project.responsible_user_id===userId;
  const ready=(id:string)=>isResponsible&&assessment?.roles.some(r=>r.candidates.some(c=>c.recommendation_id===id&&c.ready_for_review&&c.preferred_competencies==='CONFIRMED'))===true;
  function toggle(role:string,recommendation:string,selected:boolean){setConfirming(false);setMembers(old=>selected?[...old.filter(m=>m.required_role_id!==role||m.recommendation_id),{required_role_id:role,recommendation_id:recommendation,status:'PROPOSED'}]:old.filter(m=>m.recommendation_id!==recommendation));}
  function update(index:number,patch:Partial<Member>){setConfirming(false);setMembers(old=>old.map((m,i)=>i===index?{...m,...patch}:m));}
  function payload():CompositionInput|null{
    if(!note.trim()){setNotice('구성안 변경 사유를 입력하세요.');return null;}
    if(project.required_roles.some(r=>!members.some(m=>m.required_role_id===r.id))){setNotice('모든 역할에 후보를 선택하거나 공백 사유를 기록하세요.');return null;}
    if(members.some(m=>!m.recommendation_id&&!m.gap_note?.trim()||m.status==='NEEDS_REINFORCEMENT'&&!m.remaining_conditions?.trim())){setNotice('공백 사유와 보완 조건을 확인하세요.');return null;}
    if(members.some(m=>m.status==='CONFIRMED'&&(!m.recommendation_id||!ready(m.recommendation_id)))){setNotice('역할 확정은 현재 책임자가 검증 완료 후보에게만 할 수 있습니다.');return null;}
    return {members,note:note.trim()};
  }
  async function save(){if(locked||active.current)return;const body=payload();if(!body)return;active.current=true;setBusy(true);setError(null);setNotice('');
    try{await createComposition(project.id,project.version,body);setConfirming(false);setNote('');setNotice('새 구성안 판본을 저장했습니다. 이전 판본은 보존됩니다.');await onRefresh();}
    catch(e){setError(e);if(e instanceof ApiError&&[401,403,404].includes(e.status))setDenied(true);if(e instanceof ApiError&&[409,412].includes(e.status)){setConfirming(false);setNotice('입력은 보존했습니다. 최신 프로젝트와 비교한 뒤 새 판본 저장을 다시 확인하세요.');await onRefresh();}}
    finally{active.current=false;setBusy(false);}
  }
  return <section><h2>새 구성안 작성</h2><p>현재 판본을 덮어쓰지 않습니다. 역할 확정은 전체 최종 승인과 별도입니다.</p>{project.status==='CONFIRMED'&&<p>새 판본 작성 전에 재검토를 시작하세요.</p>}
    {error!=null&&<ErrorState error={error}/>} {notice&&<p role="status">{notice}</p>}
    <fieldset disabled={locked||confirming}>{project.required_roles.map(role=><div key={role.id}><h3>{role.title}</h3>{project.recommendations.filter(r=>r.required_role_id===role.id&&r.status==='RECOMMENDED').map(r=><label key={r.id}><input type="checkbox" checked={members.some(m=>m.recommendation_id===r.id)} onChange={e=>toggle(role.id,r.id,e.target.checked)}/>{r.candidate_name} 선택</label>)}
      <button type="button" onClick={()=>{setMembers(old=>[...old.filter(m=>m.required_role_id!==role.id),{required_role_id:role.id,recommendation_id:null,status:'PROPOSED',gap_note:''}]);setConfirming(false);}}>{role.title} 공백으로 기록</button>
      {members.map((m,index)=>m.required_role_id!==role.id?null:<div key={m.recommendation_id??role.id}><p>{project.recommendations.find(r=>r.id===m.recommendation_id)?.candidate_name??'미추천 공백'}</p>
        {m.recommendation_id&&<label>역할 상태 {index+1}<select value={m.status??'PROPOSED'} onChange={e=>update(index,{status:e.target.value as Member['status']})}><option value="PROPOSED">제안</option><option value="NEEDS_REINFORCEMENT">보완 필요</option><option value="CONFIRMED" disabled={!ready(m.recommendation_id)}>역할 확정</option></select></label>}
        <label>선정 사유 {index+1}<textarea maxLength={4000} value={m.rationale??''} onChange={e=>update(index,{rationale:e.target.value||null})}/></label>
        {!m.recommendation_id&&<label>공백 사유 {index+1}<textarea maxLength={4000} value={m.gap_note??''} onChange={e=>update(index,{gap_note:e.target.value||null})}/></label>}
        {m.status==='NEEDS_REINFORCEMENT'&&<label>남은 보완 조건 {index+1}<textarea maxLength={4000} value={m.remaining_conditions??''} onChange={e=>update(index,{remaining_conditions:e.target.value||null})}/></label>}
      </div>)}
    </div>)}<label>구성안 변경 사유<textarea maxLength={4000} value={note} onChange={e=>setNote(e.target.value)}/></label></fieldset>
    {!confirming?<button disabled={locked} onClick={()=>{setNotice('');if(payload())setConfirming(true);}}>새 판본 저장</button>:<><p>프로젝트 판본 {project.version}을 기준으로 새 구성안을 저장할까요?</p><button disabled={locked} onClick={()=>void save()}>{busy?'구성안 저장 중':'새 판본 저장 확인'}</button><button disabled={busy} onClick={()=>setConfirming(false)}>구성안 저장 취소</button></>}
  </section>;
}
