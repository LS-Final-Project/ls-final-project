import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getImprovementVersions } from '../api/cs-improvements';
import { ErrorState, Loading } from './states';

const fields = {
  problem_statement:'검토할 문제', confirmed_facts:'확인된 사실', inconsistencies:'불일치',
  missing_info:'부족한 정보', not_claimed:'단정하지 않는 내용', review_task:'검토 과제',
  condition_status:'조건 확인 상태', condition_summary:'사용 조건', fw_fixed_version:'수정 FW',
  fw_released_on:'FW 배포일', doc_id:'문서 ID', cs_owner:'담당자 ID',
};
const recordedText = (value:unknown) => typeof value==='string'&&value ? value : '미기록';
function Evidence({value}:{value:unknown}) {
  if(!Array.isArray(value))return <p>근거 목록 미확인</p>;
  if(!value.length)return <p>이 판본에 연결된 근거 없음</p>;
  return <ul>{value.map((entry:unknown,i)=>{
    if(!entry||typeof entry!=='object'||!('ticket_id' in entry)||typeof entry.ticket_id!=='string'||!entry.ticket_id)return <li key={i}>근거 형식 확인 필요</li>;
    return <li key={i}><Link to={`/cs/tickets/${encodeURIComponent(entry.ticket_id)}`}>{entry.ticket_id}</Link> · 해결 사례 {recordedText('case_id' in entry?entry.case_id:null)}</li>;
  })}</ul>;
}
export default function ImprovementVersions({requestId}:{requestId:string}){
  const [opened,setOpened]=useState(false);
  const mode=getApiMode();
  const versions=useQuery({queryKey:['cs-improvement-versions',mode,requestId],queryFn:({signal})=>getImprovementVersions(requestId,signal),enabled:import.meta.env.DEV&&opened,retry:false,staleTime:0});
  if(!import.meta.env.DEV)return null;
  const refresh=()=>{if(opened)void versions.refetch();else setOpened(true);};
  return <section><h2>작성 판본 이력</h2><p>각 판본에 저장된 작성 내용과 유효 근거입니다. 검토 승인·전달 완료를 뜻하지 않으며 현재 상태와 구분합니다.</p>
    <button disabled={versions.isFetching} onClick={refresh}>{opened?'판본 이력 새로고침':'판본 이력 조회'}</button>
    {versions.isFetching&&<Loading/>}{opened&&versions.isError&&<ErrorState error={versions.error} onRetry={refresh}/>}
    {opened&&!versions.isFetching&&versions.isSuccess&&(versions.data.data.items.length?versions.data.data.items.map(v=><article key={v.version}>
      <h3>판본 {v.version}</h3><p>변경 시각: {v.changed_at} · 변경자 ID: {v.changed_by??'미확인'}</p>
      <dl>{Object.entries(fields).map(([field,label])=><div key={field}><dt>{label}</dt><dd>{recordedText(v.content[field])}</dd></div>)}</dl>
      <p>제안 부서: {Array.isArray(v.content.proposed_depts)&&v.content.proposed_depts.every(d=>typeof d==='string')?v.content.proposed_depts.join(', ')||'미기록':'미확인'}</p>
      <h4>해당 판본의 근거</h4><Evidence value={v.content.evidence}/>
    </article>):<p>저장된 판본이 없습니다.</p>)}
  </section>;
}
