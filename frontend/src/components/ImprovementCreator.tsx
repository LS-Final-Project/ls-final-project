import { useEffect, useRef, useState } from 'react';
import { ApiError, getApiMode } from '../api/client';
import { createImprovement, type ImprovementCreate, type ImprovementDetail } from '../api/cs-improvements';

const departments:NonNullable<ImprovementCreate['proposed_depts']> = ['TS_LV','TS_MV','TS_TRF','TS_AUTO','TS_ENERGY','RND_FW','QA_CLAIM','FIELD_SVC','CERT','SALES_OPS','SCM','WARRANTY_DESK'];
const signals:NonNullable<ImprovementCreate['doc_signal']>[] = ['NOT_FOUND','VALUE_CONFLICT','BROKEN_LINK','REPEATED_SPEC'];
const fields={problem_statement:'검토할 문제',confirmed_facts:'확인된 사실',inconsistencies:'불일치',missing_info:'부족한 정보',not_claimed:'단정하지 않는 내용',review_task:'검토 과제'} as const;
type Field=keyof typeof fields;
const empty=()=>Object.fromEntries(Object.keys(fields).map(key=>[key,''])) as Record<Field,string>;

export default function ImprovementCreator({onCreated,onEditingChange,onReload}:{onReload?:()=>void;onEditingChange?:(editing:boolean)=>void;onCreated:(data:ImprovementDetail)=>void}){
  const [opened,setOpened]=useState(false);
  const [type,setType]=useState<ImprovementCreate['request_type']>('FAULT');
  const [product,setProduct]=useState('');const [fault,setFault]=useState('');
  const [docSignal,setDocSignal]=useState<NonNullable<ImprovementCreate['doc_signal']>|''>('');
  const [depts,setDepts]=useState<NonNullable<ImprovementCreate['proposed_depts']>>([]);
  const [values,setValues]=useState(empty);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [uncertain,setUncertain]=useState(false);
  const controller=useRef<AbortController|null>(null);const opener=useRef<HTMLButtonElement|null>(null);const wasOpened=useRef(false);
  useEffect(()=>{if(wasOpened.current&&!opened)opener.current?.focus();wasOpened.current=opened;},[opened]);
  useEffect(()=>{onEditingChange?.(opened);return()=>onEditingChange?.(false);},[opened,onEditingChange]);
  useEffect(()=>()=>controller.current?.abort(),[]);
  if(!import.meta.env.DEV)return null;
  const available=getApiMode()==='live';
  const valid=!!product.trim()&&!!values.problem_statement.trim()&&(type==='FAULT'?/^FLT-[0-9]{3}$/.test(fault.trim())&&depts.length>0:!!docSignal);
  const reset=()=>{setOpened(false);setType('FAULT');setProduct('');setFault('');setDocSignal('');setDepts([]);setValues(empty());setError('');setUncertain(false);};
  const save=async()=>{
    if(controller.current||!available||!valid||uncertain)return;
    const current=new AbortController();controller.current=current;setBusy(true);setError('');
    const body:ImprovementCreate={request_type:type,product_code:product.trim(),problem_statement:values.problem_statement.trim(),proposed_depts:depts,condition_status:'UNKNOWN',evidence:[],...(type==='FAULT'?{fault_id:fault.trim()}:{doc_signal:docSignal as NonNullable<ImprovementCreate['doc_signal']>})};
    for(const key of Object.keys(fields) as Field[])if(key!=='problem_statement'&&values[key].trim())body[key]=values[key].trim();
    try{const result=await createImprovement(body,current.signal);if(!current.signal.aborted){reset();onCreated(result);}}
    catch(e){if(!current.signal.aborted){setError(e instanceof ApiError?e.message:'저장 결과를 확인할 수 없습니다.');setUncertain(!(e instanceof ApiError)||e.status===408||e.status===409||e.status>=500);}}
    finally{if(controller.current===current)controller.current=null;if(!current.signal.aborted)setBusy(false);}
  };
  return <section><h2>수동 개선요청 초안</h2><p>초안만 저장합니다. AI 판정·근거 연결·검토 승인·부서 전달은 수행하지 않습니다. 조건은 미확인으로 기록됩니다.</p>
    {!available&&<p>모의 응답에서는 새 요청을 저장하지 않습니다.</p>}
    {!opened?<button ref={opener} disabled={!available} onClick={()=>setOpened(true)}>새 개선요청 작성</button>:<form aria-label="개선요청 초안 작성" aria-busy={busy} onSubmit={e=>{e.preventDefault();void save();}}>
      <fieldset disabled={busy||uncertain}><legend>초안 내용</legend>
        <label>요청 유형<select autoFocus value={type} onChange={e=>{setType(e.target.value as ImprovementCreate['request_type']);setFault('');setDocSignal('');setDepts([]);}}><option value="FAULT">고장</option><option value="DOCUMENT">문서</option></select></label>
        <label>제품 코드<input required maxLength={200} value={product} onChange={e=>setProduct(e.target.value)}/></label>
        {type==='FAULT'?<label>고장 ID<input required pattern="FLT-[0-9]{3}" placeholder="FLT-000" value={fault} onChange={e=>setFault(e.target.value)}/></label>:<label>문서 신호<select required value={docSignal} onChange={e=>setDocSignal(e.target.value as typeof docSignal)}><option value="">선택하세요</option>{signals.map(signal=><option key={signal}>{signal}</option>)}</select></label>}
        <fieldset><legend>제안 부서{type==='FAULT'?' (1개 이상 필수)':' (미확인 가능)'}</legend>{departments.map(dept=><label key={dept}><input type="checkbox" checked={depts.includes(dept)} onChange={e=>setDepts(current=>e.target.checked?[...current,dept]:current.filter(value=>value!==dept))}/>{dept}</label>)}</fieldset>
        {type==='DOCUMENT'&&!depts.length&&<p>수신 부서 미확인 초안입니다. 부서 확인 전 전달 준비가 끝난 상태가 아닙니다.</p>}
        {Object.entries(fields).map(([key,label])=><label key={key}>{label}<textarea required={key==='problem_statement'} maxLength={10000} value={values[key as Field]} onChange={e=>setValues(current=>({...current,[key]:e.target.value}))}/></label>)}
      </fieldset>
      {error&&<p role="alert">{error}</p>}
      {uncertain&&<p>저장 결과가 불확실해 다시 작성하지 않습니다. 목록에서 실제 생성 여부를 먼저 확인하세요. 화면을 나가거나 새로고침해도 이미 생성된 요청은 취소되지 않습니다.</p>}
      {uncertain&&<button type="button" onClick={()=>{reset();onReload?.();}}>입력 버리고 목록 새로 조회</button>}
      <p>저장 중 이탈은 서버 생성을 취소하지 못할 수 있습니다. 재진입 시 목록에서 생성 여부를 확인하세요.</p>
      <button type="submit" disabled={busy||uncertain||!valid}>{busy?'초안 저장 중':'초안 저장'}</button>
      <button type="button" disabled={busy||uncertain} onClick={reset}>작성 취소</button>
    </form>}
  </section>;
}
