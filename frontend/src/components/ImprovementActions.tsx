import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { listCsAssignees, me } from '../api/platform';
import {
  acknowledgeImprovement, addImprovementEvidence, assignImprovementOwner, completeImprovement, getFollowupSignals,
  planImprovementFollowup, recordImprovementAction, removeImprovementEvidence, replyImprovement, resendImprovement,
  reviewImprovement, reviewImprovementRejection, sendImprovement,
  type ActionBody, type ImprovementDetail, type ReplyBody, type SendBody,
} from '../api/cs-improvements';
import { DEPT_CODES, deptName, formatStamp, REQUEST_STATUS_LABEL } from '../ls-shell/cs-labels';
import { ErrorState, Loading } from './states';

type Data = ImprovementDetail['data'];
type Dept = NonNullable<SendBody['dept_codes']>[number];
type Kind = 'review'|'send'|'evidence-add'|'evidence-remove'|'resend'|'review-rejection'|'plan'|'complete'|'owner'|'acknowledge'|'reply'|'action';
type Open = {kind:Kind;target?:string;version:number};
type Row = {kind:Kind;label:string;target?:string;targetLabel:string;reason?:string};

const DEPT_STATUS:Record<string,string>={SENT:'받음(확인 전)',ACKNOWLEDGED:'수신 확인',ADOPTED:'채택',INFO_REQUESTED:'추가 정보 요청',REJECTED:'반려',ACTION_TAKEN:'조치 기록'};
const REPLY_LABEL:Record<ReplyBody['reply_type'],string>={ADOPTED:'채택',INFO_REQUESTED:'추가 정보 요청',REJECTED:'반려'};
const SCOPE_LABEL:Record<NonNullable<ActionBody['scope_kind']>,string>={OTHER:'그 밖(사람이 판단)',FIRMWARE_VERSION:'펌웨어 버전 이상',SHIPPED_FROM:'출하일 이후',LOT:'로트 목록'};
const SCOPE_HINT:Record<NonNullable<ActionBody['scope_kind']>,string>={OTHER:'',FIRMWARE_VERSION:'예: 1.72',SHIPPED_FROM:'예: 2026-01-31',LOT:'쉼표로 구분'};
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const stamp=(iso:string|null|undefined)=>iso?formatStamp(iso):'미확인';
const deptLabel=(code:string)=>deptName(code);

// 계약(contracts/cs/improvements.md 상태 전이)을 그대로 따라 지금 상태에서 열 수 있는 동작을 고른다. 최종 판단은 서버가 한다.
function rowsFor(data:Data,roles:string[],dept:string|null,managed:string[],now:number):Row[]{
  const r=data.request;const rows:Row[]=[];
  const completed=r.status==='COMPLETED';
  const tasks=data.next_owners;
  const rejectionClosed=data.departments.length>0&&data.departments.every(x=>x.status==='REJECTED'&&!!x.rejection_reviewed_at);
  const deptClosed=r.status==='FOLLOW_UP'||completed||rejectionClosed;
  const deptClosedReason=completed?'완료된 요청입니다.':r.status==='FOLLOW_UP'?'후속 확인 단계에서는 부서 처리를 바꾸지 않습니다.':'모든 부서의 반려를 확인해 종료된 회차입니다.';
  if(roles.includes('cs_agent')){
    rows.push({kind:'review',label:'검토 완료 기록',targetLabel:'요청',reason:r.status==='DRAFT'?undefined:'작성 중 상태에서만 검토를 기록할 수 있습니다.'});
    const sendReason=r.status!=='REVIEWED'?(r.status==='DRAFT'?'검토를 먼저 기록해야 합니다.':'이미 부서에 전달한 요청입니다. 새 회차는 이 화면에서 열지 않습니다.'):undefined;
    rows.push({kind:'send',label:'부서 전달 기록',targetLabel:'요청',reason:sendReason});
    for(const x of data.departments){
      if(x.status==='INFO_REQUESTED'||x.status==='REJECTED'||x.status==='SENT'||x.status==='ACKNOWLEDGED'){
        const due=x.reply_due_at?Date.parse(x.reply_due_at):NaN;
        const waiting=(x.status==='SENT'||x.status==='ACKNOWLEDGED')&&!(due<now);
        rows.push({kind:'resend',label:x.status==='INFO_REQUESTED'?'자료 보완 후 다시 요청':x.status==='REJECTED'?'새 근거로 다시 요청':'회신 재촉',target:x.dept_code,targetLabel:deptLabel(x.dept_code),
          reason:deptClosed?deptClosedReason:waiting?(x.reply_due_at?`회신 기한(${stamp(x.reply_due_at)})이 지난 뒤에만 재촉할 수 있습니다.`:'회신 기한을 확인할 수 없어 재촉할 수 없습니다.'):undefined});
      }
      if(x.status==='REJECTED'&&!x.rejection_reviewed_at)rows.push({kind:'review-rejection',label:'반려 확인',target:x.dept_code,targetLabel:deptLabel(x.dept_code),reason:deptClosed?deptClosedReason:undefined});
    }
    rows.push({kind:'plan',label:'후속 확인 계획 기록',targetLabel:'요청',reason:tasks.some(t=>t.task==='PLAN_FOLLOWUP')?undefined:r.status==='FOLLOW_UP'||completed?'이미 후속 확인 계획을 기록했습니다.':'모든 부서가 조치를 기록했거나 반려를 확인했고, 조치가 1건 이상일 때만 기록할 수 있습니다.'});
    rows.push({kind:'complete',label:'후속 확인 결과와 완료 기록',targetLabel:'요청',reason:r.status==='FOLLOW_UP'?undefined:completed?'이미 완료된 요청입니다.':'후속 확인 계획을 먼저 기록해야 합니다.'});
    rows.push({kind:'evidence-add',label:'근거 문의 추가',targetLabel:'요청',reason:completed?'완료된 요청은 고칠 수 없습니다.':undefined});
    for(const e of data.evidence.filter(e=>!e.removed_at))rows.push({kind:'evidence-remove',label:'근거 제외',target:e.id,targetLabel:`근거 ${e.ticket_external_id??'문의'}`,reason:completed?'완료된 요청은 고칠 수 없습니다.':undefined});
  }
  if(roles.includes('manager')){
    rows.push({kind:'owner',label:'C/S 담당자 재배정',targetLabel:'요청',reason:!managed.includes('GCS_L1')?'C/S 부서 관리자만 재배정할 수 있습니다.':completed?'완료된 요청은 고칠 수 없습니다.':undefined});
  }
  if(roles.includes('dept_staff')){
    const mine=dept?data.departments.find(x=>x.dept_code===dept):undefined;
    if(!mine)rows.push({kind:'acknowledge',label:'부서 처리',targetLabel:dept?deptLabel(dept):'내 부서',reason:'이 요청의 현재 회차에 내 부서가 받는 부서로 없습니다.'});
    else{
      const target=mine.dept_code;const targetLabel=deptLabel(target);
      const state=(need:string,what:string)=>deptClosed?deptClosedReason:mine.status===need?undefined:`부서 상태가 '${DEPT_STATUS[need]}'일 때만 ${what}할 수 있습니다. 지금은 '${DEPT_STATUS[mine.status]??'미확인'}'입니다.`;
      rows.push({kind:'acknowledge',label:'수신 확인',target,targetLabel,reason:state('SENT','수신 확인')});
      rows.push({kind:'reply',label:'회신(채택·추가 정보 요청·반려)',target,targetLabel,reason:state('ACKNOWLEDGED','회신')});
      rows.push({kind:'action',label:'개선 조치 기록',target,targetLabel,reason:state('ADOPTED','조치를 기록')});
    }
  }
  return rows;
}

export default function ImprovementActions({data,onSaved,onReload,onEditingChange}:{data:Data;onSaved:(result:ImprovementDetail)=>void;onReload:()=>void;onEditingChange?:(editing:boolean)=>void}){
  const r=data.request;
  const mode=getApiMode();
  const live=mode==='live';
  const identity=useQuery({queryKey:['cs-improvement-actor',mode],queryFn:me,enabled:live,retry:false,staleTime:0});
  const [open,setOpen]=useState<Open|null>(null);
  const [values,setValues]=useState<Record<string,string>>({});
  const [depts,setDepts]=useState<Dept[]>([]);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [uncertain,setUncertain]=useState(false);const [notice,setNotice]=useState('');
  const controller=useRef<AbortController|null>(null);
  const assignees=useQuery({queryKey:['cs-assignees',mode],queryFn:({signal})=>listCsAssignees(signal),enabled:live&&open?.kind==='owner',retry:false,staleTime:0});
  useEffect(()=>{onEditingChange?.(open!==null);return()=>onEditingChange?.(false);},[open,onEditingChange]);
  useEffect(()=>()=>controller.current?.abort(),[]);
  const roles=Array.isArray(identity.data?.data?.roles)?identity.data!.data.roles as string[]:[];
  const dept=identity.data?.data?.dept??null;
  const managed=Array.isArray(identity.data?.data?.managed_departments)?identity.data!.data.managed_departments:[];
  const rows=identity.isSuccess?rowsFor(data,roles,dept,managed,Date.now()):[];
  const set=(key:string)=>(e:{target:{value:string}})=>setValues(v=>({...v,[key]:e.target.value}));
  const v=(key:string)=>values[key]??'';
  const start=(row:Row)=>{
    if(busy||uncertain)return;
    setOpen({kind:row.kind,target:row.target,version:r.version});setError('');setNotice('');
    setValues(row.kind==='send'||row.kind==='resend'?{reply_due_days:'7'}:row.kind==='action'?{scope_kind:'OTHER'}:row.kind==='reply'?{reply_type:'ADOPTED'}:{});
    setDepts(row.kind==='review'||row.kind==='send'?[...r.proposed_depts] as Dept[]:[]);
  };
  const close=()=>{if(busy)return;setOpen(null);setError('');};
  const days=Number(v('reply_due_days'));const daysOk=Number.isInteger(days)&&days>=1&&days<=90;
  const scope=(v('scope_kind')||'OTHER') as NonNullable<ActionBody['scope_kind']>;
  const valid=(():boolean=>{
    if(!open)return false;
    switch(open.kind){
      case 'review':return r.request_type!=='FAULT'||depts.length>0;
      case 'send':return depts.length>0&&daysOk;
      case 'resend':return !!v('reason').trim()&&daysOk;
      case 'evidence-add':return uuid.test(v('ticket_id').trim())&&(r.request_type!=='FAULT'||uuid.test(v('case_id').trim()));
      case 'evidence-remove':return !!v('reason').trim();
      case 'plan':return !!v('followup_plan').trim();
      case 'complete':return !!v('followup_result').trim();
      case 'owner':return uuid.test(v('owner_user_id'));
      case 'reply':return v('reply_type')!=='REJECTED'||!!v('reason').trim();
      case 'action':return ['action','action_scope','action_evidence'].every(k=>!!v(k).trim())&&/^\d{4}-\d{2}-\d{2}$/.test(v('action_date'))&&(scope==='OTHER'||!!v('scope_value').trim());
      default:return true;
    }
  })();
  const submit=async()=>{
    if(!open||controller.current||uncertain||!valid||!live)return;
    const current=new AbortController();controller.current=current;setBusy(true);setError('');
    const {kind,target='',version}=open;const id=r.id;const s=current.signal;const text=(key:string)=>v(key).trim();
    const sameDepts=depts.length===r.proposed_depts.length&&depts.every(d=>r.proposed_depts.includes(d));
    const call=():Promise<ImprovementDetail>=>{switch(kind){
      case 'review':return reviewImprovement(id,{version,...(sameDepts?{}:{proposed_depts:depts})},s);
      case 'send':return sendImprovement(id,{version,dept_codes:depts,reply_due_days:days},s);
      case 'resend':return resendImprovement(id,target,{version,reason:text('reason'),reply_due_days:days},s);
      case 'review-rejection':return reviewImprovementRejection(id,target,{version},s);
      case 'evidence-add':return addImprovementEvidence(id,{version,ticket_id:text('ticket_id'),...(r.request_type==='FAULT'?{case_id:text('case_id')}:{})},s);
      case 'evidence-remove':return removeImprovementEvidence(id,target,{version,reason:text('reason')},s);
      case 'plan':return planImprovementFollowup(id,{version,followup_plan:text('followup_plan')},s);
      case 'complete':return completeImprovement(id,{version,followup_result:text('followup_result')},s);
      case 'owner':return assignImprovementOwner(id,{version,owner_user_id:v('owner_user_id')},s);
      case 'acknowledge':return acknowledgeImprovement(id,target,{version},s);
      case 'reply':return replyImprovement(id,target,{version,reply_type:v('reply_type') as ReplyBody['reply_type'],...(text('reason')?{reason:text('reason')}:{})},s);
      case 'action':return recordImprovementAction(id,target,{version,action:text('action'),action_scope:text('action_scope'),action_date:v('action_date'),action_evidence:text('action_evidence'),scope_kind:scope,...(scope==='OTHER'?{}:{scope_value:text('scope_value')})},s);
    }};
    try{const result=await call();if(!s.aborted){setOpen(null);setNotice(`기록을 저장했습니다(판본 ${result.data.request.version}). 아래는 서버가 돌려준 현재 상태이며, 부서의 수용이나 고객 장비 적용을 뜻하지 않습니다.`);onSaved(result);}}
    catch(e){if(!s.aborted){
      const conflict=e instanceof ApiError&&e.status===409;
      setError(e instanceof ApiError?e.message:'처리 결과를 확인할 수 없습니다.');
      setUncertain(!(e instanceof ApiError)||e.status===408||conflict||e.status>=500);
    }}
    finally{if(controller.current===current)controller.current=null;if(!s.aborted)setBusy(false);}
  };
  const deptBoxes=(legend:string)=><fieldset><legend>{legend}</legend>{DEPT_CODES.map(code=><label key={code}><input type="checkbox" checked={depts.includes(code as Dept)} onChange={e=>setDepts(c=>e.target.checked?[...c,code as Dept]:c.filter(x=>x!==code))}/>{deptLabel(code)}</label>)}</fieldset>;
  const dueField=<label>회신 기한(일)<input type="number" min={1} max={90} required value={v('reply_due_days')} onChange={set('reply_due_days')}/></label>;
  const fields=():ReactNode=>{switch(open?.kind){
    case 'review':return <>{deptBoxes(r.request_type==='FAULT'?'제안 부서 (1개 이상 필수)':'제안 부서 (미확인 가능)')}<p>검토 기록만 남깁니다. 부서 전달은 따로 기록합니다.</p></>;
    case 'send':return <>{deptBoxes('받는 부서 (1개 이상 필수)')}{!depts.length&&<p>받는 부서를 고르세요. 문서 요청은 받는 부서를 확인한 뒤 전달합니다.</p>}{dueField}<p>전달 기록만 남기며 부서에 메일을 보내지 않습니다.</p></>;
    case 'resend':return <><label>다시 요청하는 이유<textarea required maxLength={2000} value={v('reason')} onChange={set('reason')}/></label>{dueField}<p>반려 뒤 다시 요청할 때는 반려 사유와 다른 새 근거를 먼저 추가해야 합니다.</p></>;
    case 'review-rejection':return <p>부서의 반려 회신을 확인했다고 기록합니다. 부서 상태는 반려 그대로입니다.</p>;
    case 'evidence-add':return <><label>문의 ID<input required placeholder="시스템 ID" value={v('ticket_id')} onChange={set('ticket_id')}/></label>{r.request_type==='FAULT'&&<label>해결 사례 ID<input required placeholder="시스템 ID" value={v('case_id')} onChange={set('case_id')}/></label>}<p>같은 제품의 문의만 근거가 됩니다{r.request_type==='FAULT'?'. 고장 요청은 같은 고장의 해결 사례가 필요합니다':''}.</p></>;
    case 'evidence-remove':return <label>제외 사유<textarea required maxLength={2000} value={v('reason')} onChange={set('reason')}/></label>;
    case 'plan':return <label>후속 확인 계획<textarea required maxLength={10000} value={v('followup_plan')} onChange={set('followup_plan')}/></label>;
    case 'complete':return <><label>후속 확인 결과<textarea required maxLength={10000} value={v('followup_result')} onChange={set('followup_result')}/></label><p>완료는 이 개선요청 기록을 닫을 뿐이며 고객별 적용·재발 여부를 확인했다는 뜻이 아닙니다.</p></>;
    case 'owner':return assignees.isFetching?<Loading label="담당자 후보 조회 중"/>:assignees.isError?<ErrorState error={assignees.error} onRetry={()=>assignees.refetch()}/>:<label>새 C/S 담당자<select required value={v('owner_user_id')} onChange={set('owner_user_id')}><option value="">선택하세요</option>{(assignees.data?.data.items??[]).map(a=><option key={a.user_id} value={a.user_id} disabled={a.user_id===r.cs_owner}>{a.display_name}{a.user_id===r.cs_owner?' (현재 담당)':''}</option>)}</select></label>;
    case 'acknowledge':return <p>요청을 받았다고 기록합니다. 채택·반려 판단은 회신에서 따로 남깁니다.</p>;
    case 'reply':return <><label>회신 유형<select value={v('reply_type')} onChange={set('reply_type')}>{(Object.keys(REPLY_LABEL) as ReplyBody['reply_type'][]).map(k=><option key={k} value={k}>{REPLY_LABEL[k]}</option>)}</select></label><label>사유{v('reply_type')==='REJECTED'?' (반려는 필수)':' (선택)'}<textarea required={v('reply_type')==='REJECTED'} maxLength={2000} value={v('reason')} onChange={set('reason')}/></label></>;
    case 'action':return <>
      <label>조치 내용<textarea required maxLength={10000} value={v('action')} onChange={set('action')}/></label>
      <label>적용 대상 설명<textarea required maxLength={10000} value={v('action_scope')} onChange={set('action_scope')}/></label>
      <label>적용 범위 기준<select value={scope} onChange={e=>setValues(c=>({...c,scope_kind:e.target.value,scope_value:''}))}>{(Object.keys(SCOPE_LABEL) as (keyof typeof SCOPE_LABEL)[]).map(k=><option key={k} value={k}>{SCOPE_LABEL[k]}</option>)}</select></label>
      {scope!=='OTHER'&&<label>적용 범위 값<input required maxLength={500} placeholder={SCOPE_HINT[scope]} value={v('scope_value')} onChange={set('scope_value')}/></label>}
      <label>실제 적용일<input type="date" required value={v('action_date')} onChange={set('action_date')}/></label>
      <label>조치 근거<textarea required maxLength={10000} value={v('action_evidence')} onChange={set('action_evidence')}/></label>
      <p>부서의 개선 조치만 기록합니다. 특정 고객 장비의 수리·적용 여부는 여기에 적지 않습니다.</p></>;
    default:return null;
  }};
  const openRow=open?rows.find(x=>x.kind===open.kind&&x.target===open.target):undefined;
  return <section><h2>검토·전달·부서 처리</h2>
    <p>현재 상태: {REQUEST_STATUS_LABEL[r.status]??'미확인'} · 판본 {r.version} · 회차 {r.current_round}. 상태는 사람이 고르지 않고 부서 처리 결과로 서버가 정합니다. 아래 동작은 지금 상태와 내 역할에서 가능한 것만 실행할 수 있습니다.</p>
    {!live&&<p>모의 응답에서는 저장하지 않습니다.</p>}
    {live&&identity.isFetching&&<Loading label="내 역할 확인 중"/>}
    {live&&!identity.isFetching&&identity.isError&&<ErrorState error={identity.error} onRetry={()=>identity.refetch()}/>}
    {live&&!identity.isFetching&&identity.isSuccess&&(!identity.data?<p>로그인이 필요합니다.</p>:!rows.length?<p>내 역할로 이 요청에서 할 수 있는 동작이 없습니다.</p>:<table><thead><tr><th scope="col">동작</th><th scope="col">대상</th><th scope="col">가능 여부</th></tr></thead><tbody>
      {rows.map(row=><tr key={`${row.kind}:${row.target??''}`}><td>{row.label}</td><td>{row.targetLabel}</td><td>{row.reason?<span>할 수 없음: {row.reason}</span>:<button disabled={busy||uncertain||open!==null} onClick={()=>start(row)}>{row.label}</button>}</td></tr>)}
    </tbody></table>)}
    {notice&&<p role="status">{notice}</p>}
    {open&&<form aria-label={openRow?`${openRow.label} 입력`:'처리 입력'} aria-busy={busy} onSubmit={e=>{e.preventDefault();void submit();}}>
      <p>{openRow?`${openRow.label} · ${openRow.targetLabel}`:''}. 읽은 판본: {open.version}. 그사이 다른 사람이 바꿨다면 저장하지 않고 다시 읽기를 안내합니다.</p>
      <fieldset disabled={busy||uncertain}>{fields()}</fieldset>
      {error&&<p role="alert">{error}</p>}
      <button type="submit" disabled={busy||uncertain||!valid}>{busy?'기록 중':'기록 저장'}</button>
      <button type="button" disabled={busy} onClick={close}>입력 취소</button>
    </form>}
    {uncertain&&<><p>{error&&!open?`${error} `:''}다른 곳에서 먼저 바뀌었거나 처리 결과를 확인할 수 없습니다. 이미 반영됐을 수 있으니 다시 보내지 말고 최신 상태를 다시 읽으세요.</p><button type="button" onClick={()=>{setOpen(null);onReload();}}>입력 버리고 최신 상태 다시 읽기</button></>}
    <p>저장 중 화면을 떠나도 서버에 반영됐을 수 있으니 다시 들어와 최신 상태를 확인하세요.</p>
    <FollowupSignals data={data}/>
  </section>;
}

const PHASE:Record<string,string>={NOT_SENT:'아직 부서에 전달 전',BEFORE_ACTION:'조치 전(재요청 규칙)',AFTER_ACTION:'조치 뒤(재발 규칙)'};
const DECISION:Record<string,string>={REQUEST_AGAIN:'재요청 후보',RECURRENCE:'재발 후보',NONE:'후보 없음'};
const REASON:Record<string,string>={NEW_RECORDS_3:'직전 전달 뒤 새 해결 기록 3건 이상',APPLIED_UNIT_RECURRENCE:'조치가 적용된 장비에서 다시 발생',BELOW_3:'새 해결 기록이 3건 미만',NO_RECURRENCE:'조치 뒤 재발 기록 없음',NOT_SENT:'전달 전이라 판정하지 않음',
  PRE_ACTION_UNIT_FIRMWARE:'펌웨어 수정판 미적용 장비',PRE_ACTION_UNIT_SHIPMENT:'기준일 전 출하 장비',PRE_ACTION_UNIT_LOT:'대상 로트 밖 장비',UNKNOWN_FW_VERSION:'고장 당시 펌웨어 버전 미확인',UNKNOWN_SHIP_DATE:'출하일 미확인',UNKNOWN_LOT:'로트 미확인',UNKNOWN_SCOPE:'적용 범위를 사람이 판단해야 함',SAME_DAY_AS_ACTION:'조치일과 같은 날 접수'};
const CLASS:Record<string,string>={NEW_SINCE_SENT:'직전 전달 뒤 새 기록',RECURRENCE:'재발',PRE_ACTION_UNIT:'조치 전 장비',APPLICATION_UNKNOWN:'적용 여부 미확인',BEFORE_ACTION_DATE:'조치일 전 발생',IN_EARLIER_ROUND:'이전 회차에서 다룸'};
const label=(map:Record<string,string>,code:string|null|undefined)=>code?map[code]??'기타':'-';

// KAN-117 판정은 읽을 때 계산하는 후보일 뿐 요청 상태를 바꾸지 않는다. 새 회차 열기는 이 화면에 없다.
function FollowupSignals({data}:{data:Data}){
  const r=data.request;const mode=getApiMode();
  const [opened,setOpened]=useState(false);
  const fault=r.request_type==='FAULT';
  const signals=useQuery({queryKey:['cs-improvement-signals',mode,r.id],queryFn:({signal})=>getFollowupSignals(r.id,signal),enabled:fault&&opened,retry:false,staleTime:0});
  const refresh=()=>{if(opened)void signals.refetch();else setOpened(true);};
  const s=signals.data?.data;
  return <section><h3>재요청·재발 신호</h3><p>같은 묶음의 새 해결 기록을 읽을 때 계산한 후보입니다. 요청 상태를 바꾸지 않고 자동으로 다시 보내지 않습니다.</p>
    {!fault?<p>문서 요청은 재요청·재발 판정 대상이 아닙니다.</p>:<button disabled={signals.isFetching} onClick={refresh}>{opened?'신호 새로고침':'신호 조회'}</button>}
    {signals.isFetching&&<Loading/>}{opened&&!signals.isFetching&&signals.isError&&<ErrorState error={signals.error} onRetry={refresh}/>}
    {opened&&!signals.isFetching&&signals.isSuccess&&s&&<>
      <dl><dt>기준 시각</dt><dd>{stamp(s.as_of)}</dd><dt>단계</dt><dd>{label(PHASE,s.phase)}</dd><dt>판정</dt><dd>{label(DECISION,s.decision)} · {label(REASON,s.reason_code)}</dd>
        <dt>재요청 진행</dt><dd>{s.counts.new_since_sent}/{s.counts.threshold}{s.counts.candidate_index?` (후보 ${s.counts.candidate_index}번)`:''}</dd>
        <dt>재발 / 조치 전 장비 / 적용 미확인</dt><dd>{s.counts.recurrence}건 / {s.counts.pre_action_unit}건 / {s.counts.application_unknown}건</dd>
        <dt>업데이트 안내 대상 고객</dt><dd>{s.update_notice_customers.length}명</dd></dl>
      {s.next_action!=='NONE'&&<p>새 회차 후보입니다. 근거를 확인한 뒤 담당자가 새 회차를 열어야 하며, 이 화면에서는 새 회차를 열지 않습니다.</p>}
      {s.records.length?<table><thead><tr><th scope="col">문의</th><th scope="col">알려진 시각</th><th scope="col">분류</th><th scope="col">사유</th></tr></thead><tbody>
        {s.records.map((x,i)=><tr key={`${x.ticket_id}:${x.case_id}:${i}`}><td><Link to={`/cs/tickets/${encodeURIComponent(x.ticket_id)}`}>문의 보기</Link></td><td>{stamp(x.known_at)}</td><td>{label(CLASS,x.class)}</td><td>{label(REASON,x.reason_code)}</td></tr>)}
      </tbody></table>:<p>판정에 쓴 기록이 없습니다.</p>}
    </>}
  </section>;
}
