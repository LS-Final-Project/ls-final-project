import { useEffect, useRef, useState } from 'react';
import { ApiError, getApiMode } from '../api/client';
import { updateImprovement, type ImprovementDetail, type ImprovementPatch } from '../api/cs-improvements';
const fields={problem_statement:'검토할 문제',confirmed_facts:'확인된 사실',inconsistencies:'불일치',missing_info:'부족한 정보',not_claimed:'단정하지 않는 내용',review_task:'검토 과제'} as const;
type Field=keyof typeof fields;
export default function ImprovementEditor({record,onSaved,onReload,onEditingChange}:{onEditingChange?:(editing:boolean)=>void;record:ImprovementDetail['data']['request'];onSaved:(data:ImprovementDetail)=>void;onReload:()=>void}){
  const [opened,setOpened]=useState(false);
  const [values,setValues]=useState(()=>Object.fromEntries(Object.keys(fields).map(k=>[k,record[k as Field]??''])) as Record<Field,string>);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [uncertain,setUncertain]=useState(false);
  const controller=useRef<AbortController|null>(null);
  const opener=useRef<HTMLButtonElement|null>(null);
  const wasOpened=useRef(false);
  useEffect(()=>{if(wasOpened.current&&!opened)opener.current?.focus();wasOpened.current=opened;},[opened]);
  useEffect(()=>{onEditingChange?.(opened);return()=>onEditingChange?.(false);},[opened,onEditingChange]);
  useEffect(()=>()=>{controller.current?.abort();},[]);
  if(!import.meta.env.DEV)return null;
  const available=getApiMode()==='live'&&record.status!=='COMPLETED';
  const changes:ImprovementPatch={version:record.version};
  for(const key of Object.keys(fields) as Field[]){if(values[key]===(record[key]??''))continue;const value=values[key].trim();if(value!==(record[key]??''))changes[key]=key==='problem_statement'?value:value||null;}
  const dirty=Object.keys(changes).length>1;
  const save=async()=>{
    if(controller.current||!available||!dirty||!values.problem_statement.trim()||uncertain)return;
    const current=new AbortController();controller.current=current;setBusy(true);setError('');
    try{const result=await updateImprovement(record.id,changes,current.signal);if(!current.signal.aborted){setOpened(false);onSaved(result);}}
    catch(e){if(!current.signal.aborted){setError(e instanceof ApiError?e.message:'저장 결과를 확인할 수 없습니다. 최신 내용을 다시 조회하세요.');setUncertain(!(e instanceof ApiError)||e.status===408||e.status===409||e.status>=500);}}
    finally{if(controller.current===current)controller.current=null;if(!current.signal.aborted)setBusy(false);}
  };
  return <section><h2>작성 내용 수정</h2><p>내용만 새 판본으로 저장합니다. 검토 승인·전달·조치 완료는 실행하지 않습니다. 저장 중 화면을 떠나도 서버에 반영됐을 수 있으므로 재진입 후 최신 내용을 확인하세요.</p>
    {!available&&<p>{getApiMode()==='mock'?'모의 응답에서는 저장하지 않습니다.':'완료된 요청은 여기서 수정할 수 없습니다.'}</p>}
    {!opened?<button ref={opener} disabled={!available} onClick={()=>setOpened(true)}>내용 수정</button>:<form aria-busy={busy} onSubmit={e=>{e.preventDefault();void save();}}>
      <p>읽은 판본: {record.version}. 제품·고장·문서 신호와 조건 확정 상태는 변경하지 않습니다.</p>
      {Object.entries(fields).map(([key,label])=><label key={key}>{label}<textarea autoFocus={key==='problem_statement'} required={key==='problem_statement'} maxLength={10000} disabled={busy} value={values[key as Field]} onChange={e=>setValues(v=>({...v,[key]:e.target.value}))}/></label>)}
      {error&&<p role="alert">{error}</p>}{uncertain&&<p>충돌하거나 저장 결과가 불확실합니다. 반복 저장하지 말고 최신 내용을 확인하세요.</p>}
      <button type="submit" disabled={busy||uncertain||!dirty||!values.problem_statement.trim()}>{busy?'저장 중':'내용 저장'}</button>
      <button type="button" disabled={busy} onClick={()=>{setOpened(false);setValues(Object.fromEntries(Object.keys(fields).map(k=>[k,record[k as Field]??''])) as Record<Field,string>);setError('');}}>수정 취소</button>
      {uncertain&&<button type="button" onClick={()=>{setOpened(false);onReload();}}>입력 버리고 최신 내용 조회</button>}
    </form>}
  </section>;
}
