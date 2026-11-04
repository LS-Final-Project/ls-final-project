import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { listCsAssignees, me } from '../api/platform';
import {
  acknowledgeImprovement, assignImprovementOwner, completeImprovement, getFollowupSignals,
  planImprovementFollowup, recordImprovementAction, replyImprovement, resendImprovement,
  reviewImprovement, reviewImprovementRejection, sendImprovement,
  type ActionBody, type ImprovementDetail, type ReplyBody, type SendBody,
} from '../api/cs-improvements';
import { DEPT_CODES, deptName, formatStamp, REQUEST_STATUS_LABEL } from '../ls-shell/cs-labels';
import { ErrorState, Loading } from './states';

type Data = ImprovementDetail['data'];
type Dept = NonNullable<SendBody['dept_codes']>[number];
type Kind = 'review'|'send'|'resend'|'review-rejection'|'plan'|'complete'|'owner'|'acknowledge'|'reply'|'action';
type Open = {kind:Kind;target?:string;version:number};
// done: 이미 지난 단계(다시 할 일이 아님). 화면에서 할 일 목록에 올리지 않는다.
export type ActionRow = {kind:Kind;label:string;target?:string;targetLabel:string;reason?:string;done?:boolean};
type Row = ActionRow;
// 부서 단위 동작. 상세 화면에서는 수신 부서 표의 그 부서 줄에 버튼을 두고, 입력도 그 표 아래(ia-slot-dept)에 연다.
const DEPT_KINDS:Kind[]=['resend','review-rejection','acknowledge','reply','action'];
const PRIMARY:Kind[]=['review','send','review-rejection','resend','plan','complete','acknowledge','reply','action'];

const DEPT_STATUS:Record<string,string>={SENT:'받음(확인 전)',ACKNOWLEDGED:'수신 확인',ADOPTED:'채택',INFO_REQUESTED:'추가 정보 요청',REJECTED:'반려',ACTION_TAKEN:'조치 기록'};
const REPLY_LABEL:Record<ReplyBody['reply_type'],string>={ADOPTED:'채택',INFO_REQUESTED:'추가 정보 요청',REJECTED:'반려'};
const SCOPE_LABEL:Record<NonNullable<ActionBody['scope_kind']>,string>={OTHER:'그 밖(사람이 판단)',FIRMWARE_VERSION:'펌웨어 버전 이상',SHIPPED_FROM:'출하일 이후',LOT:'로트 목록'};
const SCOPE_HINT:Record<NonNullable<ActionBody['scope_kind']>,string>={OTHER:'',FIRMWARE_VERSION:'예: 1.72',SHIPPED_FROM:'예: 2026-01-31',LOT:'쉼표로 구분'};
const stamp=(iso:string|null|undefined)=>iso?formatStamp(iso):'미확인';
const deptLabel=(code:string)=>deptName(code);

// 계약(contracts/cs/improvements.md 상태 전이)을 그대로 따라 지금 상태에서 열 수 있는 동작을 고른다. 최종 판단은 서버가 한다.
function rowsFor(data:Data,roles:string[],dept:string|null,managed:string[],now:number,reviewBlocked:string|null=null,quick=false):Row[]{
  const r=data.request;const rows:Row[]=[];
  const completed=r.status==='COMPLETED';
  const tasks=data.next_owners;
  const rejectionClosed=data.departments.length>0&&data.departments.every(x=>x.status==='REJECTED'&&!!x.rejection_reviewed_at);
  const deptClosed=r.status==='FOLLOW_UP'||completed||rejectionClosed;
  const deptClosedReason=completed?'완료된 요청입니다.':r.status==='FOLLOW_UP'?'후속 확인 단계에서는 부서 처리를 바꾸지 않습니다.':'모든 부서의 반려를 확인해 종료된 회차입니다.';
  if(roles.includes('cs_agent')){
    rows.push({kind:'review',label:'검토 완료 기록',targetLabel:'요청',reason:r.status==='DRAFT'?reviewBlocked??undefined:'작성 중 상태에서만 검토를 기록할 수 있습니다.',done:r.status!=='DRAFT'});
    const sendReason=r.status!=='REVIEWED'?(r.status==='DRAFT'?'검토를 먼저 기록해야 합니다.':'이미 부서에 전달한 요청입니다. 새 회차는 이 화면에서 열지 않습니다.'):quick&&!r.proposed_depts.length?'제안 부서가 없습니다. 개선 요청 작성의 "내용 수정"에서 제안 부서를 고르세요.':undefined;
    rows.push({kind:'send',label:'부서 전달',targetLabel:'요청',reason:sendReason,done:r.status!=='DRAFT'&&r.status!=='REVIEWED'});
    for(const x of data.departments){
      if(x.status==='INFO_REQUESTED'||x.status==='REJECTED'||x.status==='SENT'||x.status==='ACKNOWLEDGED'){
        const due=x.reply_due_at?Date.parse(x.reply_due_at):NaN;
        const waiting=(x.status==='SENT'||x.status==='ACKNOWLEDGED')&&!(due<now);
        rows.push({kind:'resend',label:x.status==='INFO_REQUESTED'?'자료 보완 후 다시 요청':x.status==='REJECTED'?'새 근거로 다시 요청':'회신 재촉',target:x.dept_code,targetLabel:deptLabel(x.dept_code),
          reason:deptClosed?deptClosedReason:waiting?(x.reply_due_at?`회신 기한(${stamp(x.reply_due_at)})이 지난 뒤에만 재촉할 수 있습니다.`:'회신 기한을 확인할 수 없어 재촉할 수 없습니다.'):undefined});
      }
      if(x.status==='REJECTED'&&!x.rejection_reviewed_at)rows.push({kind:'review-rejection',label:'반려 확인',target:x.dept_code,targetLabel:deptLabel(x.dept_code),reason:deptClosed?deptClosedReason:undefined});
    }
    rows.push({kind:'plan',label:'후속 확인 계획 기록',targetLabel:'요청',reason:tasks.some(t=>t.task==='PLAN_FOLLOWUP')?undefined:r.status==='FOLLOW_UP'||completed?'이미 후속 확인 계획을 기록했습니다.':'모든 부서가 조치를 기록했거나 반려를 확인했고, 조치가 1건 이상일 때만 기록할 수 있습니다.',done:r.status==='FOLLOW_UP'||completed});
    rows.push({kind:'complete',label:'후속 확인 결과와 완료 기록',targetLabel:'요청',reason:r.status==='FOLLOW_UP'?undefined:completed?'이미 완료된 요청입니다.':'후속 확인 계획을 먼저 기록해야 합니다.',done:completed});
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

// uncertain(결과 불명·충돌)은 상위 화면이 갖는다. 다시 읽기로 최신 상세를 실제로 받은 뒤에만 상위가 해제하고, 다시 읽기가 실패하면 유지한다.
// locked는 같은 화면의 다른 입력(작성 내용 수정)이 열려 있다는 뜻이다. 저장 응답이 다른 입력을 지우지 않도록 동시에 열지 않는다.
// onPrimary: 지금 할 수 있는 첫 동작을 상위 화면(단계 바의 '지금 할 일')에 알린다. 누르면 이 상자의 같은 입력을 연다.
export type PrimaryAction={kind:string;label:string;blocked:boolean;why?:string;run:()=>void}|null;
// inline=false(상세 화면): 동작 목록 표를 그리지 않는다. 화면이 onControl로 받은 동작을 단계 바·부서 표·제목 줄 버튼으로 보이고,
// 이 컴포넌트는 입력 칸·저장 알림·결과 불명 안내만 그린다. inline=true(기본)는 표까지 그린다.
export type ActionControl={rows:ActionRow[];blocked:boolean;start:(row:ActionRow)=>void};
export default function ImprovementActions({data,onSaved,onReload,onEditingChange,uncertain=false,onUncertain,locked=false,onPrimary,inline=true,onControl,reviewBlocked=null}:{data:Data;onSaved:(result:ImprovementDetail)=>void;onReload:()=>void;onEditingChange?:(editing:boolean)=>void;uncertain?:boolean;onUncertain?:()=>void;locked?:boolean;onPrimary?:(action:PrimaryAction)=>void;inline?:boolean;onControl?:(control:ActionControl|null)=>void;reviewBlocked?:string|null}){
  const r=data.request;
  const mode=getApiMode();
  const live=mode==='live';
  const identity=useQuery({queryKey:['cs-improvement-actor',mode],queryFn:me,enabled:live,retry:false,staleTime:0});
  const [open,setOpen]=useState<Open|null>(null);
  const [values,setValues]=useState<Record<string,string>>({});
  const [depts,setDepts]=useState<Dept[]>([]);
  const [pickDepts,setPickDepts]=useState(false);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  const controller=useRef<AbortController|null>(null);
  const assignees=useQuery({queryKey:['cs-assignees',mode],queryFn:({signal})=>listCsAssignees(signal),enabled:live&&open?.kind==='owner',retry:false,staleTime:0});
  useEffect(()=>{onEditingChange?.(open!==null);return()=>onEditingChange?.(false);},[open,onEditingChange]);
  useEffect(()=>()=>controller.current?.abort(),[]);
  const roles=Array.isArray(identity.data?.data?.roles)?identity.data!.data.roles as string[]:[];
  const dept=identity.data?.data?.dept??null;
  const managed=Array.isArray(identity.data?.data?.managed_departments)?identity.data!.data.managed_departments:[];
  const rows=identity.isSuccess?rowsFor(data,roles,dept,managed,Date.now(),reviewBlocked,!inline):[];
  const set=(key:string)=>(e:{target:{value:string}})=>setValues(v=>({...v,[key]:e.target.value}));
  const v=(key:string)=>values[key]??'';
  const start=(row:Row)=>{
    // 상세 화면(inline=false)의 부서 전달은 입력 없이 바로 보낸다: 받는 부서 = 제안 부서, 회신 기한 = 서버 기본 7일.
    if(!inline&&row.kind==='send'){void sendNow();return;}
    if(busy||uncertain||locked)return;
    setOpen({kind:row.kind,target:row.target,version:r.version});setError('');setNotice('');
    setValues(row.kind==='send'||row.kind==='resend'?{reply_due_days:'7'}:row.kind==='action'?{scope_kind:'OTHER'}:row.kind==='reply'?{reply_type:'ADOPTED'}:{});
    setDepts(row.kind==='review'||row.kind==='send'?[...r.proposed_depts] as Dept[]:[]);setPickDepts(false);
    setTimeout(()=>document.getElementById('ia-form')?.scrollIntoView?.({block:'nearest',behavior:'smooth'}),0);
  };
  // 지금 할 수 있는 첫 동작. 근거 추가·제외·재배정처럼 언제든 하는 동작은 '지금 할 일'로 올리지 않는다.
  // 검토가 막혀 있으면(요청 내용 비었음 등) 그 버튼을 회색으로 두고 이유를 보인다.
  const primaryRow=rows.find(row=>!row.reason&&PRIMARY.includes(row.kind))??rows.find(row=>row.kind==='review'&&!row.done&&!!reviewBlocked);
  const startRef=useRef(start);startRef.current=start;
  const primaryRef=useRef(primaryRow);primaryRef.current=primaryRow;
  const blocked=busy||uncertain||locked||open!==null;
  const primaryKey=primaryRow?`${primaryRow.kind}:${primaryRow.target??''}:${primaryRow.label}:${primaryRow.reason??''}`:'';
  useEffect(()=>{onPrimary?.(primaryKey?{kind:primaryRef.current!.kind,label:primaryRef.current!.label,blocked:blocked||!!primaryRef.current!.reason,why:primaryRef.current!.reason,run:()=>{if(primaryRef.current)startRef.current(primaryRef.current);}}:null);},[primaryKey,blocked,onPrimary]);
  useEffect(()=>()=>onPrimary?.(null),[onPrimary]);
  const rowsRef=useRef(rows);rowsRef.current=rows;
  const controlKey=`${blocked}|${rows.map(row=>`${row.kind}:${row.target??''}:${row.reason??''}:${row.done?1:0}`).join(',')}`;
  useEffect(()=>{onControl?.({rows:rowsRef.current,blocked,start:row=>startRef.current(row)});},[controlKey,blocked,onControl]);
  useEffect(()=>()=>onControl?.(null),[onControl]);
  const close=()=>{if(busy)return;setOpen(null);setError('');};
  const days=Number(v('reply_due_days'));const daysOk=Number.isInteger(days)&&days>=1&&days<=90;
  const scope=(v('scope_kind')||'OTHER') as NonNullable<ActionBody['scope_kind']>;
  const valid=(():boolean=>{
    if(!open)return false;
    switch(open.kind){
      case 'review':return r.request_type!=='FAULT'||depts.length>0;
      case 'send':return depts.length>0&&daysOk;
      case 'resend':return !!v('reason').trim()&&daysOk;
      case 'plan':return !!v('followup_plan').trim();
      case 'complete':return !!v('followup_result').trim();
      case 'owner':return v('owner_user_id')!==r.cs_owner&&(assignees.data?.data.items??[]).some(a=>a.user_id===v('owner_user_id'));
      case 'reply':return v('reply_type')!=='REJECTED'||!!v('reason').trim();
      case 'action':return ['action','action_scope','action_evidence'].every(k=>!!v(k).trim())&&/^\d{4}-\d{2}-\d{2}$/.test(v('action_date'))&&(scope==='OTHER'||!!v('scope_value').trim());
      default:return true;
    }
  })();
  const sendNow=async()=>{
    if(controller.current||uncertain||!live||busy||locked||open)return;
    const current=new AbortController();controller.current=current;setBusy(true);setError('');setNotice('');
    const s=current.signal;
    try{const result=await sendImprovement(r.id,{version:r.version,dept_codes:[...r.proposed_depts] as Dept[],reply_due_days:7},s);if(!s.aborted){setNotice(`${r.proposed_depts.map(deptLabel).join(', ')}에 전달했습니다(판본 ${result.data.request.version}).`);onSaved(result);}}
    catch(e){if(!s.aborted){
      setError(e instanceof ApiError?e.message:'전달 결과를 확인할 수 없습니다.');
      if(!(e instanceof ApiError)||e.status===408||e.status===409||e.status>=500)onUncertain?.();
    }}
    finally{if(controller.current===current)controller.current=null;if(!s.aborted)setBusy(false);}
  };
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
      if(!(e instanceof ApiError)||e.status===408||conflict||e.status>=500)onUncertain?.();
    }}
    finally{if(controller.current===current)controller.current=null;if(!s.aborted)setBusy(false);}
  };
  const deptBoxes=(legend:string)=><fieldset><legend>{legend}</legend>{DEPT_CODES.map(code=><label key={code}><input type="checkbox" checked={depts.includes(code as Dept)} onChange={e=>setDepts(c=>e.target.checked?[...c,code as Dept]:c.filter(x=>x!==code))}/>{deptLabel(code)}</label>)}</fieldset>;
  const dueField=<label>회신 기한(일)<input type="number" min={1} max={90} required value={v('reply_due_days')} onChange={set('reply_due_days')}/></label>;
  const fields=():ReactNode=>{switch(open?.kind){
    case 'review':return <>{deptBoxes(r.request_type==='FAULT'?'제안 부서 (1개 이상 필수)':'제안 부서 (미확인 가능)')}<p>검토 기록만 남깁니다. 부서 전달은 따로 기록합니다.</p></>;
    case 'send':return <>{r.proposed_depts.length>0&&!pickDepts
      ?<div className="fr"><span className="k">받는 부서</span><span className="chips">{depts.map(code=><span key={code} className="dept-chip">{deptLabel(code)}</span>)}<button type="button" className="mini" onClick={()=>setPickDepts(true)}>받는 부서 바꾸기</button></span></div>
      :deptBoxes('받는 부서 (1개 이상 필수)')}{!depts.length&&<p>받는 부서를 고르세요. 문서 요청은 받는 부서를 확인한 뒤 전달합니다.</p>}{dueField}<p className="meta">전달하면 받는 부서 담당자 화면의 "받은 요청"에 올라갑니다. 메일은 따로 보내지 않습니다.</p></>;
    case 'resend':return <><label>다시 요청하는 이유<textarea required maxLength={2000} value={v('reason')} onChange={set('reason')}/></label>{dueField}<p>반려 뒤 다시 요청할 때는 반려 사유와 다른 새 근거를 먼저 추가해야 합니다.</p></>;
    case 'review-rejection':return <p>부서의 반려 회신을 확인했다고 기록합니다. 부서 상태는 반려 그대로입니다.</p>;
    case 'plan':return <label>후속 확인 계획<textarea required maxLength={10000} value={v('followup_plan')} onChange={set('followup_plan')}/></label>;
    case 'complete':return <><label>후속 확인 결과<textarea required maxLength={10000} value={v('followup_result')} onChange={set('followup_result')}/></label><p>완료는 이 개선요청 기록을 닫을 뿐이며 고객별 적용·재발 여부를 확인했다는 뜻이 아닙니다.</p></>;
    case 'owner':{
      if(assignees.isFetching)return <Loading label="담당자 후보 조회 중"/>;
      if(assignees.isError)return <ErrorState error={assignees.error} onRetry={()=>assignees.refetch()}/>;
      const items=assignees.data?.data.items??[];const current=items.find(a=>a.user_id===r.cs_owner);
      return <><p>현재 담당: {current?current.display_name:r.cs_owner?'후보 목록에 없는 사용자':'미배정'}</p>
        {items.some(a=>a.user_id!==r.cs_owner)?<label>새 C/S 담당자<select required value={v('owner_user_id')} onChange={set('owner_user_id')}><option value="">선택하세요</option>{items.map(a=><option key={a.user_id} value={a.user_id} disabled={a.user_id===r.cs_owner}>{a.display_name}{a.user_id===r.cs_owner?' (현재 담당)':''}</option>)}</select></label>:<p>재배정할 수 있는 C/S 담당자가 없습니다.</p>}</>;
    }
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
  const flow=rows.filter(row=>!row.done);
  const ready=flow.filter(row=>!row.reason);
  const waiting=flow.filter(row=>row.reason);
  const cols=<colgroup><col/><col style={{width:150}}/><col style={{width:'40%'}}/></colgroup>;
  const actionRow=(row:Row)=><tr key={`${row.kind}:${row.target??''}`}>
    <td>{row.label}</td>
    <td>{row.targetLabel}</td>
    <td>{row.reason?<span className="why-line">{row.reason}</span>:<button className={row===primaryRow?'kb3 sm pri':'kb3 sm'} disabled={blocked} onClick={()=>start(row)}>{row.label}</button>}</td>
  </tr>;
  const formNode=open&&<form id="ia-form" className="sendbox" aria-label={openRow?`${openRow.label} 입력`:'처리 입력'} aria-busy={busy} onSubmit={e=>{e.preventDefault();void submit();}}>
      <div className="ia-head">{open.kind==='send'?<b>부서 전달 요청 사항</b>:<><b>{openRow?openRow.label:'처리 입력'}</b>{openRow&&<span className="chip x">{openRow.targetLabel}</span>}</>}<span className="meta">읽은 판본 {open.version}</span></div>
      <p className="meta">{openRow?`${openRow.label} · ${openRow.targetLabel}`:''}. 읽은 판본: {open.version}. 그사이 다른 사람이 바꿨다면 저장하지 않고 다시 읽기를 안내합니다.</p>
      <fieldset disabled={busy||uncertain}>{fields()}</fieldset>
      {error&&<p role="alert">{error}</p>}
      <div className="row2"><button className="kb3 sm" type="button" disabled={busy} onClick={close}>{open.kind==='send'?'전달 취소':'입력 취소'}</button><button className="kb3 sm pri" type="submit" disabled={busy||uncertain||!valid}>{open.kind==='send'?(busy?'전달 중':'전달'):(busy?'기록 중':'기록 저장')}</button></div>
    </form>;
  // 부서 단위 입력은 상세 화면의 부서 표 아래 자리에 연다. 자리가 없으면 이 자리에 그린다.
  const slotId=!open||inline?null:DEPT_KINDS.includes(open.kind)?'ia-slot-dept':null;
  const slot=slotId&&typeof document!=='undefined'?document.getElementById(slotId):null;
  const uncertainNode=uncertain&&<><p className="stale">{error&&!open?`${error} `:''}다른 곳에서 먼저 바뀌었거나 처리 결과를 확인할 수 없습니다. 이미 반영됐을 수 있으니 다시 보내지 말고 최신 상태를 다시 읽으세요.</p><button className="kb3 sm" type="button" onClick={()=>{setOpen(null);onReload();}}>입력 버리고 최신 상태 다시 읽기</button></>;
  if(!inline)return <>
    {live&&!identity.isFetching&&identity.isError&&<ErrorState error={identity.error} onRetry={()=>identity.refetch()}/>}
    {notice&&<p role="status">{notice}</p>}
    {!open&&error&&!uncertain&&<p role="alert">{error}</p>}
    {open&&(slot?createPortal(formNode,slot):formNode)}
    {uncertainNode}
  </>;
  return <section className="ia"><div className="ph"><h2>검토·전달·부서 처리</h2><div className="r"><span className="chip">{REQUEST_STATUS_LABEL[r.status]??'미확인'}</span></div></div>
    {!live&&<p>모의 응답에서는 저장하지 않습니다.</p>}
    {live&&identity.isFetching&&<Loading label="내 역할 확인 중"/>}
    {live&&!identity.isFetching&&identity.isError&&<ErrorState error={identity.error} onRetry={()=>identity.refetch()}/>}
    {live&&!identity.isFetching&&identity.isSuccess&&(!identity.data?<p>로그인이 필요합니다.</p>:!rows.length?<p>내 역할로 이 요청에서 할 수 있는 동작이 없습니다.</p>:<>
      <div className="ia-grid">
        <div><h3>지금 할 수 있는 일 <small>{ready.length}건</small></h3>
          {ready.length?<table className="log ia-ready">{cols}<thead><tr><th scope="col">동작</th><th scope="col">대상</th><th scope="col">실행</th></tr></thead><tbody>{ready.map(actionRow)}</tbody></table>:<p className="none">지금 바로 할 수 있는 동작이 없습니다. 오른쪽의 조건을 확인하세요.</p>}</div>
        <div><h3>조건이 맞아야 하는 일 <small>{waiting.length}건</small></h3>
          {waiting.length?<table className="log ia-wait">{cols}<thead><tr><th scope="col">동작</th><th scope="col">대상</th><th scope="col">할 수 없는 이유</th></tr></thead><tbody>{waiting.map(actionRow)}</tbody></table>:<p className="none">없음</p>}</div>
      </div>
    </>)}
    {locked&&!open&&<p className="stale">작성 내용 수정이 열려 있어 처리 동작을 시작할 수 없습니다. 수정을 저장하거나 취소한 뒤 진행하세요.</p>}
    {notice&&<p role="status">{notice}</p>}
    {open&&(slot?createPortal(formNode,slot):formNode)}
    {uncertainNode}
    {r.request_type==='FAULT'&&!['DRAFT','REVIEWED'].includes(r.status)&&<FollowupSignals data={data}/>}
  </section>;
}

const PHASE:Record<string,string>={NOT_SENT:'아직 부서에 전달 전',BEFORE_ACTION:'조치 전(재요청 규칙)',AFTER_ACTION:'조치 뒤(재발 규칙)'};
const DECISION:Record<string,string>={REQUEST_AGAIN:'재요청 후보',RECURRENCE:'재발 후보',NONE:'후보 없음'};
const REASON:Record<string,string>={NEW_RECORDS_3:'직전 전달 뒤 새 해결 기록 3건 이상',APPLIED_UNIT_RECURRENCE:'조치가 적용된 장비에서 다시 발생',BELOW_3:'새 해결 기록이 3건 미만',NO_RECURRENCE:'조치 뒤 재발 기록 없음',NOT_SENT:'전달 전이라 판정하지 않음',
  PRE_ACTION_UNIT_FIRMWARE:'펌웨어 수정판 미적용 장비',PRE_ACTION_UNIT_SHIPMENT:'기준일 전 출하 장비',PRE_ACTION_UNIT_LOT:'대상 로트 밖 장비',UNKNOWN_FW_VERSION:'고장 당시 펌웨어 버전 미확인',UNKNOWN_SHIP_DATE:'출하일 미확인',UNKNOWN_LOT:'로트 미확인',UNKNOWN_SCOPE:'적용 범위를 사람이 판단해야 함',SAME_DAY_AS_ACTION:'조치일과 같은 날 접수'};
const CLASS:Record<string,string>={NEW_SINCE_SENT:'직전 전달 뒤 새 기록',RECURRENCE:'재발',PRE_ACTION_UNIT:'조치 전 장비',APPLICATION_UNKNOWN:'적용 여부 미확인',BEFORE_ACTION_DATE:'조치일 전 발생',IN_EARLIER_ROUND:'이전 회차에서 다룸'};
const label=(map:Record<string,string>,code:string|null|undefined)=>code?map[code]??'기타':'-';

// KAN-117 판정은 읽을 때 계산하는 후보일 뿐 요청 상태를 바꾸지 않는다. 새 회차 열기는 이 화면에 없다.
export function FollowupSignals({data}:{data:Data}){
  const r=data.request;const mode=getApiMode();
  const [opened,setOpened]=useState(false);
  const fault=r.request_type==='FAULT';
  const signals=useQuery({queryKey:['cs-improvement-signals',mode,r.id],queryFn:({signal})=>getFollowupSignals(r.id,signal),enabled:fault&&opened,retry:false,staleTime:0});
  const refresh=()=>{if(opened)void signals.refetch();else setOpened(true);};
  const s=signals.data?.data;
  return <section className="sig"><h3>재요청·재발 신호</h3><p>같은 묶음의 새 해결 기록을 읽을 때 계산한 후보입니다. 요청 상태를 바꾸지 않고 자동으로 다시 보내지 않습니다.</p>
    {!fault?<p>문서 요청은 재요청·재발 판정 대상이 아닙니다.</p>:<button className="kb3 sm" disabled={signals.isFetching} onClick={refresh}>{opened?'신호 새로고침':'신호 조회'}</button>}
    {signals.isFetching&&<Loading/>}{opened&&!signals.isFetching&&signals.isError&&<ErrorState error={signals.error} onRetry={refresh}/>}
    {opened&&!signals.isFetching&&signals.isSuccess&&s&&<>
      <dl className="kvl"><dt>기준 시각</dt><dd>{stamp(s.as_of)}</dd><dt>단계</dt><dd>{label(PHASE,s.phase)}</dd><dt>판정</dt><dd>{label(DECISION,s.decision)} · {label(REASON,s.reason_code)}</dd>
        <dt>재요청 진행</dt><dd>{s.counts.new_since_sent}/{s.counts.threshold}{s.counts.candidate_index?` (후보 ${s.counts.candidate_index}번)`:''}</dd>
        <dt>재발 / 조치 전 장비 / 적용 미확인</dt><dd>{s.counts.recurrence}건 / {s.counts.pre_action_unit}건 / {s.counts.application_unknown}건</dd>
        <dt>업데이트 안내 대상 고객</dt><dd>{s.update_notice_customers.length}명</dd></dl>
      {s.next_action!=='NONE'&&<p>새 회차 후보입니다. 근거를 확인한 뒤 담당자가 새 회차를 열어야 하며, 이 화면에서는 새 회차를 열지 않습니다.</p>}
      {s.records.length?<table className="log"><thead><tr><th scope="col">문의</th><th scope="col">알려진 시각</th><th scope="col">분류</th><th scope="col">사유</th></tr></thead><tbody>
        {s.records.map((x,i)=><tr key={`${x.ticket_id}:${x.case_id}:${i}`}><td><Link to={`/cs/tickets/${encodeURIComponent(x.ticket_id)}`}>문의 보기</Link></td><td>{stamp(x.known_at)}</td><td>{label(CLASS,x.class)}</td><td>{label(REASON,x.reason_code)}</td></tr>)}
      </tbody></table>:<p>판정에 쓴 기록이 없습니다.</p>}
    </>}
  </section>;
}
