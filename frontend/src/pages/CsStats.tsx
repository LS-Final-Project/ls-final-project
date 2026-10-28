import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getOverview, getOwners, getCustomerHistory, type StatsFilters } from '../api/cs-stats';
import { ErrorState, Loading, Preparing } from '../components/states';
import TicketWorkflow from '../components/TicketWorkflow';

const empty: StatsFilters = {start:'',end:'',dept:'',customer:''};
const departments = ['TS_LV','TS_MV','TS_TRF','TS_AUTO','TS_ENERGY','RND_FW','QA_CLAIM','FIELD_SVC','CERT','SALES_OPS','SCM','WARRANTY_DESK'];
const timestamp = (value: string|null) => value ? new Date(value).toLocaleString('ko-KR') : '기록 없음';
function Counts({values}: {values: Record<string,number>}) {
  return Object.keys(values).length ? <dl>{Object.entries(values).map(([key,count])=><div key={key}><dt>{key}</dt><dd>{count}</dd></div>)}</dl> : <p>저장된 집계 없음</p>;
}
function Unconfirmed({values}:{values:string[]}) {
  return <p>미확인 지표: {values.length ? values.map(value=>value==='SLA_DUE'?'SLA 기한':value==='RULE_ASSIGNED_DEPT'?'규칙상 담당 부서':`알 수 없는 지표 (${value})`).join(', ') : '없음'}</p>;
}
export default function CsStats(){
  if(!import.meta.env.DEV) return <Preparing title="C/S 현황" message="운영 인증 중계와 권한 검증 후 제공됩니다."/>;
  return <StatsView/>;
}
function StatsView(){
  const client=useQueryClient();
  const [draft,setDraft]=useState(empty);
  const [filters,setFilters]=useState(empty);
  const [error,setError]=useState('');
  const [view,setView]=useState<'overview'|'owners'|'customer'>('overview');
  const mode=getApiMode();
  const overview=useQuery({queryKey:['cs-stats',mode,'overview',filters],queryFn:({signal})=>getOverview(filters,signal),enabled:view==='overview',retry:false,staleTime:0});
  const owners=useQuery({queryKey:['cs-stats',mode,'owners',filters],queryFn:({signal})=>getOwners(filters,signal),enabled:view==='owners',retry:false,staleTime:0});
  const customer=useQuery({queryKey:['cs-stats',mode,'customer',filters],queryFn:({signal})=>getCustomerHistory(filters,signal),enabled:view==='customer'&&!!filters.customer,retry:false,staleTime:0});
  const active=view==='overview'?overview:view==='owners'?owners:customer;
  const o=view==='overview'&&!overview.isFetching&&overview.isSuccess?overview.data.data:null;
  const c=view==='customer'&&!customer.isFetching&&customer.isSuccess?customer.data.data:null;
  return <main><header><Link to="/cs/tickets">C/S 문의 목록</Link><h1>C/S 현황·고객 이력</h1>{mode==='mock'&&<p role="status">모의 응답 모드 — 계약 예시이며 실제 업무 집계가 아닙니다. 필터·고객별 예시가 없는 요청은 실패할 수 있습니다.</p>}<p>저장된 기록의 집계입니다. 실제 권한 범위는 서버에서 확인합니다.</p></header>
    <nav aria-label="현황 종류">{(['overview','owners','customer'] as const).map((key,i)=><button key={key} aria-pressed={view===key} onClick={()=>{if(view===key)return;void client.cancelQueries({queryKey:['cs-stats']});setView(key);}}>{['처리·개선 현황','담당자별 현황 (관리자)','고객·장비 이력'][i]}</button>)}</nav>
    {draft.customer.trim()!==filters.customer&&<p>입력한 고객 ID는 아직 적용되지 않았습니다. 필터 적용 후 해당 고객을 조회합니다.</p>}
    <form onSubmit={event=>{event.preventDefault();const f={...draft,customer:draft.customer.trim()};if(f.start&&f.end&&new Date(f.start)>=new Date(f.end)){setError('종료는 시작보다 늦어야 합니다.');return;}if(view==='customer'&&!f.customer){setError('고객 ID를 입력하세요.');return;}setError('');setFilters(f);}}>
      <label>시작 (포함, 현지 시각)<input type="datetime-local" value={draft.start} onChange={e=>setDraft({...draft,start:e.target.value})}/></label>
      <label>종료 (미포함, 현지 시각)<input type="datetime-local" value={draft.end} onChange={e=>setDraft({...draft,end:e.target.value})}/></label>
      <label>이관 부서<select disabled={view==='customer'} value={draft.dept} onChange={e=>setDraft({...draft,dept:e.target.value})}><option value="">전체</option>{departments.map(d=><option key={d}>{d}</option>)}</select></label>
      <label>고객 ID<input maxLength={64} disabled={view==='owners'} value={draft.customer} onChange={e=>setDraft({...draft,customer:e.target.value})}/></label>
      <button disabled={active.isFetching}>필터 적용</button><button type="button" onClick={()=>{setDraft(empty);setFilters(empty);setError('');}}>초기화</button>
    </form>
    <p>고객 이력은 부서 필터를 적용하지 않습니다. 담당자별 현황은 고객 필터를 적용하지 않습니다.</p>
    {error&&<p role="alert">{error}</p>}
    {view==='customer'&&!filters.customer?<p>고객 ID를 입력하고 필터를 적용하세요.</p>:<>
      <button disabled={active.isFetching} onClick={()=>active.refetch()}>최신 현황 조회</button>
      {active.isFetching&&<Loading/>}{!active.isFetching&&active.isError&&<ErrorState error={active.error} onRetry={()=>active.refetch()}/>}
    </>}
    {o&&<>
      <section><h2>처리 현황 ({o.scope==='ALL'?'전체':'본인 담당'})</h2><p>적용 기간: {o.period.start?timestamp(o.period.start):'처음부터'} ~ {o.period.end?timestamp(o.period.end):'현재까지'} (종료 미포함)</p><p>최초·최종 접수 기록: {timestamp(o.data_range.first_received_at)} ~ {timestamp(o.data_range.last_received_at)}</p><dl><dt>기간 내 접수</dt><dd>{o.tickets.received}</dd><dt>기간 내 종결</dt><dd>{o.tickets.closed}</dd><dt>기간 내 접수 중 현재 진행</dt><dd>{o.tickets.open}</dd></dl><Counts values={o.tickets.by_state}/><h3>자료 출처</h3><Counts values={o.tickets.by_origin}/><Unconfirmed values={o.unconfirmed}/><h3>진행 문의 접수 후 경과 기간 (SLA 지연 아님)</h3><Counts values={{'1일 미만':o.tickets.open_age.under_1_day,'1~3일':o.tickets.open_age.from_1_to_3_days,'3~7일':o.tickets.open_age.from_3_to_7_days,'7일 이상':o.tickets.open_age.over_7_days}}/></section>
      <section><h2>부서 이관 결과</h2>{o.departments.length?<table><thead><tr><th>부서</th><th>이관</th><th>결과 제출</th><th>결과 수용</th><th>반환·회수</th></tr></thead><tbody>{o.departments.map(d=><tr key={d.dept_code}><td>{d.dept_code}</td><td>{d.transferred}</td><td>{d.results_submitted}</td><td>{d.results_accepted}</td><td>{d.returned_or_recalled}</td></tr>)}</tbody></table>:<p>이관 기록 없음</p>}</section>
      <section><h2>고객별 접수 상위 10</h2>{o.top_customers.length?o.top_customers.map(t=><p key={t.customer_external_id}>{t.customer_external_id}: 접수 {t.received}, 진행 {t.open}</p>):<p>고객 집계 없음</p>}</section>
      <section><h2>개선 현황</h2>{o.improvements?<><p>진행 {o.improvements.open} / 수신 부서 미확인 {o.improvements.receiver_unconfirmed}</p><Counts values={o.improvements.by_status}/><Counts values={o.improvements.by_type}/></>:<p>개선 집계는 관리자 범위에서만 제공됩니다.</p>}</section>
    </>}
    {view==='owners'&&!owners.isFetching&&owners.isSuccess&&<section><h2>담당자별 현황</h2><p>접수·종결은 선택 기간 기준, 현재 진행 수는 기간과 무관합니다. 사용자 이름은 추측하지 않고 API 식별자로 표시합니다.</p>{owners.data.data.items.length?<table><thead><tr><th>담당자 ID</th><th>접수</th><th>종결</th><th>현재 진행 (전체 기간)</th></tr></thead><tbody>{owners.data.data.items.map((item,i)=><tr key={item.owner_user_id??i}><td>{item.owner_user_id??'담당자 미배정'}</td><td>{item.received}</td><td>{item.closed}</td><td>{item.open}</td></tr>)}</tbody></table>:<p>담당자 집계 없음</p>}</section>}
    {c&&<section><h2>고객 {c.customer_external_id}</h2><p>기간 내 접수 {c.received}건 · 최근 문의 최대 200건</p><Unconfirmed values={c.unconfirmed}/><Counts values={c.by_state}/><Counts values={c.by_product}/><h3>문의</h3>{c.tickets.length?c.tickets.map(t=><article key={t.id}><Link to={`/cs/tickets/${encodeURIComponent(t.id)}`}>{t.subject||t.external_id||t.id}</Link><p>{timestamp(t.received_at)} · {t.product_code} · {t.channel} · {t.data_origin}</p><TicketWorkflow state={t.workflow_state}/></article>):<p>문의 기록 없음</p>}<h3>설치 장비 (전체 이력, 기간 필터 미적용)</h3>{c.units.length?c.units.map(u=><p key={u.id}>{u.serial_no} · {u.product_code} · {u.unit_status} · {u.derivation_kind==='ASSUMED'?'가정 자료':u.derivation_kind} · 확정 문의 {u.confirmed_tickets}</p>):<p>설치 장비 기록 없음</p>}<h3>연결된 개선요청 (전체 이력, 기간 필터 미적용)</h3>{c.improvement_requests.length?c.improvement_requests.map(r=><p key={r.id}>{r.id} · {r.request_type} · {r.product_code} · {r.fault_id??'고장 ID 없음'} · {r.status} · 근거 문의 {r.evidence_tickets}</p>):<p>연결된 개선요청 없음</p>}</section>}
  </main>;
}
