import { useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { me } from '../api/platform';
import { getProject } from '../api/tft';
import { requestCandidates, recommendCandidate, recordEvidence, type CheckStatus, type EvidenceKind, type Recommendation } from '../api/recommendations';
import { ErrorState, Loading, Preparing, Empty } from '../components/states';
import { Origin, formatDateTime } from '../components/tft';

const LABEL: Record<CheckStatus,string> = {UNSPECIFIED:'미기재',NEEDS_CHECK:'추가 확인',CONFIRMED:'확인',UNMET:'미충족'};
export default function DepartmentRequest() {
  const {projectId='',requestId=''}=useParams();
  const queryClient=useQueryClient();
  const mode=getApiMode();
  const user=useQuery({queryKey:['me'],queryFn:me,enabled:import.meta.env.DEV});
  const allowed=user.isSuccess && user.data?.data.roles.includes('dept_staff')===true && Boolean(user.data?.data.dept);
  const project=useQuery({queryKey:['projects',mode,projectId],queryFn:()=>getProject(projectId),enabled:import.meta.env.DEV && allowed});
  const data=project.data?.data;
  const item=data?.staffing_requests.find(r=>r.id===requestId);
  const role=data?.required_roles.find(r=>r.id===item?.required_role_id);
  const own=Boolean(item && item.organization_dept_code===user.data?.data.dept);
  const [cursor,setCursor]=useState<string>();
  const candidates=useQuery({queryKey:['request-candidates',requestId,cursor],queryFn:()=>requestCandidates(requestId,cursor),enabled:import.meta.env.DEV && allowed && own});
  const [candidate,setCandidate]=useState('');
  const [rationale,setRationale]=useState('');
  const [busy,setBusy]=useState(false);
  const [denied,setDenied]=useState(false);
  const [error,setError]=useState<unknown>(null);
  const [notice,setNotice]=useState('');
  const locked=denied || project.isError || !allowed || !own || !item || !['ISSUED','ANSWERED'].includes(item.status) || data?.status==='CONFIRMED' || busy || project.isFetching;
  const records=data?.recommendations.filter(r=>r.staffing_request_id===requestId) ?? [];
  async function save(action:()=>Promise<unknown>,reset:()=>void) {
    if(locked) return;
    setBusy(true);setError(null);setNotice('');
    try {await action();reset();setNotice('저장했습니다. 추천과 확인은 최종 확정이 아닙니다.');await queryClient.invalidateQueries({queryKey:['projects',mode,projectId]});}
    catch(e) {setError(e);if(e instanceof ApiError && [401,403,404].includes(e.status)){setDenied(true);void queryClient.invalidateQueries({queryKey:['me']});}if(e instanceof ApiError && [409,412].includes(e.status)) {setNotice('최신 판본을 다시 불러왔습니다. 입력을 확인한 뒤 다시 저장하세요.');await project.refetch();}}
    finally {setBusy(false);}
  }
  function submit(e:FormEvent) {e.preventDefault();if(!item || !candidate || !rationale.trim())return;void save(()=>recommendCandidate(requestId,item.version,candidate,rationale.trim()),()=>{setCandidate('');setRationale('');});}
  if(!import.meta.env.DEV)return <Preparing title="부서 후보 추천과 확인" />;
  return <main><p><Link to="/tft/requests">내 부서 인력 요청</Link></p><h1>{role?.title ?? '부서 후보 추천과 확인'}</h1>
    {user.isPending && <Loading label="접근 권한 확인 중" />}{user.isError && <ErrorState error={user.error} onRetry={()=>user.refetch()} />}
    {user.isSuccess && !allowed && <ErrorState error={new ApiError(403,'소속 부서 담당자만 입력할 수 있습니다.')} />}
    {allowed && <>{project.isPending && <Loading />}{project.isError && <ErrorState error={project.error} onRetry={()=>project.refetch()} />}
      {data && !own && <ErrorState error={new ApiError(404,'배정된 요청서를 찾을 수 없습니다.')} />}
      {item && own && <><p>{item.organization_name} · 요청 판본 {item.version} · {item.message ?? '요청 메시지 없음'}</p>
        <p>요청 기간 {role?.period_start ?? '미정'} ~ {role?.period_end ?? '미정'} · 투입률 {role?.allocation_rate ?? '미정'}%</p>
        {data?.status==='CONFIRMED' && <p role="status">확정된 프로젝트입니다. 재검토 전에는 입력할 수 없습니다.</p>}
        {notice && <p role="status">{notice}</p>}{error!=null && <ErrorState error={error} />}
        <section><h2>후보 추천</h2>{candidates.isPending && <Loading />}{candidates.isError && <ErrorState error={candidates.error} onRetry={()=>candidates.refetch()} />}
          <form className="department-form" onSubmit={submit}><fieldset disabled={locked || !candidates.data}><label>부서 후보<select aria-label="부서 후보" value={candidate} onChange={e=>setCandidate(e.target.value)}><option value="">후보 선택</option>{candidates.data?.data.items.map(c=><option key={c.id} value={c.id}>{c.display_name} · {c.job_title ?? '직무 미기재'} · {c.data_origin}</option>)}</select></label>
            <label>추천 이유<textarea required value={rationale} onChange={e=>setRationale(e.target.value)} /></label><button disabled={!candidate || !rationale.trim()} type="submit">추천 저장</button><button type="button" onClick={()=>{setCandidate('');setRationale('');setError(null);}}>취소</button></fieldset></form>
          {cursor && <button disabled={busy} onClick={()=>{setCursor(undefined);setCandidate('');}}>처음 후보</button>}{candidates.data?.data.next_cursor && <button disabled={busy} onClick={()=>{setCursor(candidates.data!.data.next_cursor!);setCandidate('');}}>다음 후보</button>}
        </section>
        <section><h2>추천과 검증 기록</h2>{records.length===0 && <Empty message="아직 추천한 후보가 없습니다" />}{records.map(r=><article key={r.id}><h3>{r.candidate_name} {r.status==='WITHDRAWN'?'철회됨':''}</h3><p>{r.rationale}</p><Origin dataOrigin={r.data_origin} derivationKind={r.derivation_kind} /><p>추천자 {r.recommended_by_user_id} · {formatDateTime(r.recommended_at)}</p>
          <EvidenceRecords recommendation={r} />
          <EvidenceForm key={r.id} disabled={locked || r.status==='WITHDRAWN'} competencies={role?.competencies ?? []} onSave={(kind,payload,reset)=>void save(()=>recordEvidence(requestId,item.version,r.id,kind,payload),reset)} />
        </article>)}</section>
        <p>역량 재검증·추천 철회·보완 회신은 후속 API 연결이 필요합니다. 같은 후보의 다른 역할 제안과 전사 일정은 이 화면만으로 확인 완료로 판단하지 않습니다.</p>
      </>}
    </>}
  </main>;
}

function EvidenceRecords({recommendation:r}:{recommendation:Recommendation}) {
  return <><h4>수행 경험</h4>{r.experience_evidence.map(e=><p key={e.id}>{e.summary} · {LABEL[e.status]} · 근거 {e.basis ?? '미기재'} · 확인자 {e.verified_by_user_id ?? '미기재'} · {formatDateTime(e.verified_at)}</p>)}
    <h4>역량 확인</h4>{r.competency_checks.map(e=><p key={e.id}>{e.competency_id} · {LABEL[e.status]} · 근거 {e.basis ?? '미기재'} · 확인자 {e.checked_by_user_id ?? '미기재'}</p>)}
    <h4>가용성 확인</h4>{r.availability_checks.map(e=><p key={e.id}>{e.period_start ?? '미정'} ~ {e.period_end ?? '미정'} · {e.allocation_rate ?? '미정'}% · {LABEL[e.status]} · 근거 {e.basis ?? '미기재'} · 확인자 {e.checked_by_user_id ?? '미기재'} · {formatDateTime(e.checked_at)}</p>)}
    {[...r.competency_checks,...r.availability_checks].some(e=>e.checked_by_user_id===r.recommended_by_user_id) || r.experience_evidence.some(e=>e.verified_by_user_id===r.recommended_by_user_id) ? <p>같은 담당자가 추천과 확인을 수행했습니다.</p>:null}</>;
}

type EvidencePayload=Parameters<typeof recordEvidence>[4];
export function EvidenceForm({disabled,competencies,onSave}:{disabled:boolean;competencies:{id:string;description:string;kind:string}[];onSave:(kind:EvidenceKind,payload:EvidencePayload,reset:()=>void)=>void}) {
  const [kind,setKind]=useState<EvidenceKind>('experience');
  const [status,setStatus]=useState<CheckStatus>('NEEDS_CHECK');
  const [summary,setSummary]=useState('');const [basis,setBasis]=useState('');const [start,setStart]=useState('');const [end,setEnd]=useState('');const [rate,setRate]=useState('');const [competency,setCompetency]=useState('');const [validation,setValidation]=useState('');
  function reset(){setSummary('');setBasis('');setStart('');setEnd('');setRate('');setCompetency('');setStatus('NEEDS_CHECK');setValidation('');}
  function submit(e:FormEvent){e.preventDefault();if(disabled)return;setValidation('');
    if((status==='CONFIRMED'||status==='UNMET')&&!basis.trim()){setValidation('확인·미충족에는 근거가 필요합니다.');return;}
    if(kind==='availability' && status==='CONFIRMED' && (!start||!end||rate==='')){setValidation('가용성 확인에는 기간·투입률·근거가 필요합니다.');return;}
    if(start&&end&&start>end){setValidation('종료일은 시작일 이후여야 합니다.');return;}
    const common={status,basis:basis.trim()||null};
    if(kind==='experience'){if(!summary.trim()){setValidation('실제 수행 업무를 입력하세요.');return;}onSave(kind,{...common,summary:summary.trim()},reset);}
    else if(kind==='availability')onSave(kind,{...common,period_start:start||null,period_end:end||null,allocation_rate:rate===''?null:Number(rate)},reset);
    else {if(!competency){setValidation('확인할 역량을 선택하세요.');return;}onSave(kind,{...common,competency_id:competency},reset);}
  }
  return <form className="department-form" onSubmit={submit}><fieldset disabled={disabled}><legend>확인 기록 추가</legend>
    <label>확인 종류<select value={kind} onChange={e=>{setKind(e.target.value as EvidenceKind);reset();}}><option value="experience">수행 경험</option><option value="availability">가용성</option><option value="competency">역량</option></select></label>
    {kind==='experience'&&<label>실제 수행 업무<textarea value={summary} onChange={e=>setSummary(e.target.value)} /></label>}
    {kind==='availability'&&<><label>시작일<input type="date" value={start} onChange={e=>setStart(e.target.value)} /></label><label>종료일<input type="date" value={end} onChange={e=>setEnd(e.target.value)} /></label><label>투입률<input type="number" min="0" max="100" value={rate} onChange={e=>setRate(e.target.value)} /></label></>}
    {kind==='competency'&&<label>역량<select value={competency} onChange={e=>setCompetency(e.target.value)}><option value="">역량 선택</option>{competencies.map(c=><option key={c.id} value={c.id}>{c.kind==='REQUIRED'?'필수':'우대'}: {c.description}</option>)}</select></label>}
    <label>확인 상태<select value={status} onChange={e=>setStatus(e.target.value as CheckStatus)}>{Object.entries(LABEL).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label><label>확인 근거<textarea value={basis} onChange={e=>setBasis(e.target.value)} /></label>
    {validation&&<p role="alert">{validation}</p>}<button type="submit">확인 기록 저장</button><button type="button" onClick={reset}>입력 취소</button>
  </fieldset></form>;
}
