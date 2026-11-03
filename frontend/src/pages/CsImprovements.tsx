import { useCallback, useState } from 'react';
import ImprovementCreator from '../components/ImprovementCreator';
import ImprovementEditor from '../components/ImprovementEditor';
import ImprovementVersions from '../components/ImprovementVersions';
import ImprovementActions, { type PrimaryAction } from '../components/ImprovementActions';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { listFaults } from '../components/ResolutionCaseForm';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getImprovement, listImprovements, type ImprovementDetail } from '../api/cs-improvements';
import { ErrorState, Loading } from '../components/states';
import { useHotkey } from '../ls-shell/kv';
import LogTable, { type LogColumn } from './LogTable';
import { CAUSE_LABEL, REQUEST_STATUS_LABEL, REQUEST_TYPE_LABEL, dayOf, deptName, formatStamp } from '../ls-shell/cs-labels';
import { getCaseComparison, type CaseComparison } from '../api/cs-repeat-cases';
import type { RequestSummary } from '../ls-shell/cs-labels';
import './ticket-detail-v3.css';
import './improvements-v3.css';

// 단계 바: 상태는 서버가 정한다. 화면은 지금 어느 단계인지만 보인다.
const STEPS = ['작성', '검토', '부서 전달', '부서 처리', '후속 확인', '완료'];
const STEP_OF: Record<string, number> = { DRAFT: 0, REVIEWED: 1, SENT: 2, ACKNOWLEDGED: 2, ADOPTED: 3, INFO_REQUESTED: 3, REJECTED: 3, ACTION_TAKEN: 3, FOLLOW_UP: 4, COMPLETED: 5 };
// 서버가 정한 다음 할 일(next_owners.task)의 화면 이름.
const TASK_LABEL: Record<string, string> = {
  REVIEW: '검토 완료 기록', CONFIRM_RECEIVER: '받는 부서 확인', SEND: '부서 전달 기록', SUPPLEMENT: '자료 보완 후 다시 요청',
  REVIEW_REJECTION: '반려 확인', PLAN_FOLLOWUP: '후속 확인 계획 기록', RECORD_FOLLOWUP: '후속 확인 결과와 완료 기록',
  ACKNOWLEDGE: '수신 확인', REPLY: '회신(채택·추가 정보 요청·반려)', RECORD_ACTION: '개선 조치 기록',
};
const ACTOR_LABEL: Record<string, string> = { CS_OWNER: 'C/S 담당자', DEPARTMENT: '부서' };
const taskName = (task: string) => TASK_LABEL[task] ?? task;
type Titled = { origin: string; request_type: string; product_code: string; fault_id: string | null; problem_statement: string };
// 시스템 후보의 문제 문장은 "제품 고장ID: 판정 사유"다(candidates.draft). 화면 제목에는 판정 사유를 빼고 고장 증상을 보인다.
// 사람이 쓴 문장(수동 요청이나 내용 수정)은 그대로 보인다.
const systemTitle = (r: Titled) => r.origin === 'SYSTEM_CANDIDATE' && r.request_type === 'FAULT' && !!r.fault_id && r.problem_statement.startsWith(`${r.product_code} ${r.fault_id}: `);
// 고장 목록(KAN-209)에서 제품별 증상을 읽는다. 못 읽으면 고장 ID만 보인다.
function useSymptoms(products: string[]) {
  const mode = getApiMode();
  const unique = [...new Set(products)];
  const results = useQueries({ queries: unique.map(product => ({ queryKey: ['cs-faults', mode, product], queryFn: ({ signal }: { signal: AbortSignal }) => listFaults(product, signal), retry: false, staleTime: 300000 })) });
  const map = new Map<string, string>();
  results.forEach((result, index) => result.data?.data.items.forEach(item => map.set(`${unique[index]}:${item.fault_id}`, item.symptom)));
  return (r: Titled) => systemTitle(r) ? `${r.fault_id} · ${map.get(`${r.product_code}:${r.fault_id}`) ?? '고장 증상 미확인'}` : r.problem_statement;
}
const DEPT_STATUS: Record<string, string> = { SENT: '받음(확인 전)', ACKNOWLEDGED: '수신 확인', ADOPTED: '채택', INFO_REQUESTED: '추가 정보 요청', REJECTED: '반려', ACTION_TAKEN: '조치 기록' };

type CaseRow = CaseComparison['data']['items'][number];
// 규칙 비교(기획서 16쪽: 사용 조건이 구조화된 칸에 있으면 규칙으로 비교). 해결 기록 칸 값을 항목별로 모아
// 같은지·다른지와 그 무게를 먼저 보이고, 표에서 근거별 값을 확인한다. 묶음이 맞는지는 사람이 정한다.
// split: 다르면 같은 고장 ID라도 나눌 수 있는 조건(기획서 15쪽: 펌웨어·운전 조건, 원인). ref: 누가·어떻게 고쳤는지라 묶음과 무관한 참고.
const COMPARE: { key: string; label: string; weight: 'split' | 'ref'; value: (row: CaseRow) => string | null }[] = [
  { key: 'cause', label: '원인 유형', weight: 'split', value: row => row.root_cause_type ? CAUSE_LABEL[row.root_cause_type] ?? row.root_cause_type : null },
  { key: 'fw', label: '고장 당시 FW', weight: 'split', value: row => row.fw_version_at_issue ?? null },
  // 해결 부서에는 이관 부서 목록 밖의 C/S 1차(GCS_L1)도 온다(해결 기록 입력 화면과 같은 이름).
  { key: 'dept', label: '해결 부서', weight: 'ref', value: row => row.resolved_by_dept ? (row.resolved_by_dept === 'GCS_L1' ? 'C/S 1차 (GCS_L1)' : deptName(row.resolved_by_dept)) : null },
  { key: 'onsite', label: '현장 방문', weight: 'ref', value: row => row.onsite_visit == null ? null : row.onsite_visit ? '있음' : '없음' },
];
type Verdict = 'same' | 'check' | 'ref' | 'none';
function summarize(rows: CaseRow[]) {
  return COMPARE.map(field => {
    const counts = new Map<string, number>();
    rows.forEach(row => { const value = field.value(row); if (value !== null) counts.set(value, (counts.get(value) ?? 0) + 1); });
    const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    // 가장 많은 값이 하나로 정해질 때만 기준으로 삼는다. 동률이면 기준 없이 모두 다른 값으로 본다.
    const base = sorted.length && (sorted.length === 1 || sorted[0][1] > sorted[1][1]) ? sorted[0][0] : null;
    const verdict: Verdict = !sorted.length ? 'none' : sorted.length === 1 ? 'same' : field.weight === 'split' ? 'check' : 'ref';
    const differs = (row: CaseRow) => { const value = field.value(row); return sorted.length > 1 && value !== null && (base === null || value !== base); };
    return { ...field, base, verdict, differs };
  });
}
function EvidenceCompare({ requestId }: { requestId: string }) {
  const mode = getApiMode();
  const comparison = useQuery({ queryKey: ['cs-repeat-cases', mode, requestId, 'comparison'], queryFn: ({ signal }) => getCaseComparison(requestId, signal), retry: false, staleTime: 0 });
  const rows = (comparison.data?.data.items ?? []).filter(row => row.row_kind !== 'CANDIDATE');
  const stats = summarize(rows);
  const checks = stats.filter(stat => stat.verdict === 'check').length;
  const refs = stats.filter(stat => stat.verdict === 'ref').length;
  const needsLook = (row: CaseRow) => stats.some(stat => stat.weight === 'split' && stat.differs(row));
  return <div className="pane cmp-pane">
    <div className="ph"><h2>근거 비교</h2><span className="meta">근거 {rows.length}건의 해결 기록 값을 항목별로 맞춰 봅니다</span>
      <div className="r">{comparison.isSuccess && (checks || refs
        ? <>{checks > 0 && <span className="chip w">판단 필요 {checks}</span>}{refs > 0 && <span className="chip x">참고 {refs}</span>}</>
        : <span className="chip">모두 같음</span>)}</div></div>
    {comparison.isFetching && <Loading label="근거 비교를 불러오는 중" />}
    {!comparison.isFetching && comparison.isError && <div className="pb"><ErrorState error={comparison.error} onRetry={() => comparison.refetch()} /></div>}
    {!comparison.isFetching && comparison.isSuccess && <>
      <div className="pb">
        {checks > 0
          ? <p className="meta">노란 값은 묶음을 가르는 조건(원인 유형·고장 당시 FW)이 다수와 다른 곳입니다. "원문 확인" 줄을 열어 같은 문제인지 보고, 다르면 근거 관리에서 빼고, 같으면 이유를 내용 수정의 사용 조건에 적으세요. 회색 값은 참고(해결 부서·현장 방문)입니다.</p>
          : <p className="meta">묶음을 가르는 조건이 모두 같습니다. 참고 항목은 누가·어떻게 고쳤는지의 차이라 근거를 빼지 않아도 됩니다.</p>}
      </div>
      <LogTable label="근거 비교" rows={rows} rowKey={row => `${row.ticket_id}:${row.case_id ?? ''}`} fallback="비교할 근거가 없습니다."
        columns={[
          { key: 'ticket', header: '문의', width: 120, cell: row => <Link className="link" to={`/cs/tickets/${encodeURIComponent(row.ticket_id)}`}>{row.ticket_external_id ?? row.ticket_id.slice(0, 8)}</Link> },
          { key: 'customer', header: '고객', width: 96, cell: row => row.customer_external_id },
          { key: 'received', header: '접수일', width: 92, className: 'c', cell: row => dayOf(row.ticket_received_at) },
          ...stats.map(stat => ({ key: stat.key, header: stat.label, cell: (row: CaseRow) => {
            const value = stat.value(row);
            if (value === null) return <span className="none">미기록</span>;
            const cls = !stat.differs(row) ? 'cmp' : stat.weight === 'split' ? 'cmp diff' : 'cmp ref';
            return <span className={cls}>{value}</span>;
          } })),
          { key: 'check', header: '확인', width: 88, className: 'c', cell: row => needsLook(row) ? <span className="chip w">원문 확인</span> : <span className="chip">같음</span> },
        ]} />
    </>}
  </div>;
}

export default function CsImprovements(){
  const {requestId}=useParams();
  const access=useBusinessAccess('cs');
  if(!access.ready)return <BusinessGate access={access} title="개선요청"/>;
  return <ImprovementView key={requestId??'list'}/>;
}
function ImprovementView(){
  const client=useQueryClient();
  const navigate=useNavigate();
  const {requestId}=useParams();
  const mode=getApiMode();
  const [writing,setWriting]=useState(false);
  const [creating,setCreating]=useState(false);
  const [primary,setPrimary]=useState<PrimaryAction>(null);
  const reportPrimary=useCallback((action:PrimaryAction)=>setPrimary(action),[]);
  const [acting,setActing]=useState(false);
  // 처리 결과 불명·충돌 때의 상세 갱신 횟수. 그 뒤 서버 상세를 실제로 한 번 더 받아야(조회 성공 또는 저장 응답) 처리 동작을 다시 연다. 다시 읽기가 실패하면 횟수가 그대로라 막은 채로 둔다.
  const [uncertainAt,setUncertainAt]=useState<number|null>(null);
  const editing=writing||acting;
  const list=useQuery({queryKey:['cs-improvements',mode,'list'],queryFn:({signal})=>listImprovements(signal),enabled:!requestId&&!editing,retry:false,staleTime:0});
  const detail=useQuery({queryKey:['cs-improvements',mode,requestId],queryFn:({signal})=>getImprovement(requestId!,signal),enabled:!!requestId&&!editing,retry:false,staleTime:0});
  const active=requestId?detail:list;
  const refresh=()=>{void active.refetch();};
  useHotkey('F8',()=>{if(!active.isFetching&&!editing)refresh();});
  const detailUpdates=requestId?client.getQueryState(['cs-improvements',mode,requestId])?.dataUpdateCount??0:0;
  const actionsUncertain=uncertainAt!==null&&detailUpdates<=uncertainAt;
  const d=requestId&&!detail.isFetching&&detail.isSuccess?detail.data.data:null;
  const r=d?.request;
  // 쓰기 응답은 서버가 돌려준 최신 상세다. 판본 이력·목록·재요청 신호는 다시 읽게 둔다.
  const saved=(result:ImprovementDetail)=>{client.setQueryData(['cs-improvements',mode,requestId],result);for(const key of ['cs-improvement-versions','cs-improvement-signals'])void client.invalidateQueries({queryKey:[key,mode,result.data.request.id]});void client.invalidateQueries({queryKey:['cs-improvements',mode,'list']});};
  const listColumns:LogColumn<RequestSummary>[]=[
    {key:'title',header:'문제',className:'t',cell:item=><Link className="link" to={`/cs/improvements/${encodeURIComponent(item.id)}`}>{titleOf(item)}</Link>},
    {key:'type',header:'유형',width:64,className:'c',cell:item=>REQUEST_TYPE_LABEL[item.request_type]},
    {key:'product',header:'제품',width:150,cell:item=>item.product_code},
    {key:'status',header:'상태',width:110,className:'c',cell:item=><span className={item.status==='COMPLETED'?'chip x':'chip'}>{REQUEST_STATUS_LABEL[item.status]??item.status}</span>},
    {key:'version',header:'판본',width:56,className:'c',cell:item=>item.version},
    {key:'evidence',header:'근거',width:64,className:'c',cell:item=>`${item.evidence_count}건`},
    {key:'owner',header:'담당',width:230,cell:item=>`담당자 ID: ${item.cs_owner??'미배정'}`},
  ];
  const titleOf=useSymptoms([...(list.data?.data.items??[]).filter(systemTitle).map(item=>item.product_code),...(r&&systemTitle(r)?[r.product_code]:[])]);
  const at=r?STEP_OF[r.status]??-1:-1;
  const done=(index:number)=>r?.status==='COMPLETED'?true:index<at;
  return <main className="td3 ir3">
    <div className="title">
      <span className="path">C/S 관리 ›</span><h1>{`개선요청 ${requestId?'상세':'목록'}`}</h1><code className="kv-code">CS0301</code>
      <div className="btns">
        {requestId
          ? <Link className="kb3" to="/cs/improvements">개선요청 목록</Link>
          : <button className="kb3" disabled={mode!=='live'||creating} title={mode!=='live'?'모의 응답에서는 새 요청을 저장하지 않습니다.':undefined} onClick={()=>setCreating(true)}>새 개선요청 작성</button>}
        <span className="vsep"/>
        <button className="kb3 pri" disabled={active.isFetching||editing} onClick={refresh}>최신 요청 조회<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      {mode==='mock'&&<div className="tier-note" role="status">모의 응답 모드 — 계약 예시이며 실제 업무 기록이 아닙니다.</div>}
      <div className="chan-note">저장된 요청을 조회합니다. 문의 종결, 부서 조치, 고객 적용 및 재발 확인은 서로 다른 기록입니다.</div>
      {editing&&<div className="stale">작성·수정 중에는 입력 보존을 위해 자동 조회를 잠시 멈춥니다. 저장·취소 후 최신 내용을 확인하세요.</div>}
      {requestId&&actionsUncertain&&<div className="stale" role="status">처리 결과를 확인하지 못했습니다. 최신 상세를 받기 전에는 검토·전달·부서 처리를 다시 시작하지 않습니다.</div>}
      {active.isFetching&&<Loading/>}{!active.isFetching&&active.isError&&<ErrorState error={active.error} onRetry={refresh}/>}

      {!requestId&&!list.isFetching&&list.isSuccess&&<>
        <div className="pane">
          <div className="ph">최신 요청 (최대 200건)<div className="r"><span className="meta">반복 사례 후보는 해결 기록을 남길 때 자동으로 초안이 됩니다</span></div></div>
          <LogTable label="개선요청 목록" columns={listColumns} rows={list.data.data.items} rowKey={item=>item.id} fallback="조회 가능한 개선요청이 없습니다."/>
        </div>
        {creating&&<div className="pane irx"><ImprovementCreator opened onOpenedChange={setCreating} onReload={()=>{void list.refetch();}} onEditingChange={setWriting} onCreated={result=>{void client.invalidateQueries({queryKey:['cs-improvements',mode,'list']});setCreating(false);navigate(`/cs/improvements/${encodeURIComponent(result.data.request.id)}`);}}/></div>}
      </>}

      {r&&d&&<>
        <div className="info">
          <div className="k">유형</div><div className="v">{r.request_type==='FAULT'?'고장':'문서'}</div>
          <div className="k">제품</div><div className="v">{r.product_code}</div>
          <div className="k">고장 / 문서</div><div className="v">{r.fault_id??r.doc_signal??'미확인'}{r.doc_id&&<span className="meta"> · {r.doc_id}</span>}</div>
          <div className="k">상태</div><div className="v last"><strong className="state">{REQUEST_STATUS_LABEL[r.status]??r.status}</strong></div>
          <div className="k nb">판본 / 회차</div><div className="v nb">{r.version} / {r.current_round}</div>
          <div className="k nb">조건 확인</div><div className="v nb">{r.condition_status==='UNKNOWN'?<span className="chip w">미확인</span>:r.condition_status}</div>
          <div className="k nb">수정 FW</div><div className="v nb">{r.fw_fixed_version??'미확인'} / {r.fw_released_on??'미확인'}</div>
          <div className="k nb">출처</div><div className="v nb last">{r.data_origin} · {r.derivation_kind}</div>
        </div>

        <div className="steps">
          {STEPS.map((name,index)=>{const cls=done(index)?'done':index===at?'cur':'';return <span key={name} className="sw">{index>0&&<span className={done(index-1)?'ln':'ln g'}/>}<span className={`st ${cls}`}><span className="dot">{done(index)?'✓':index+1}</span>{name}</span></span>;})}
          <div className="now"><div><small>지금 할 일</small><b>{d.next_owners[0]?`${ACTOR_LABEL[d.next_owners[0].actor]??d.next_owners[0].actor} · ${taskName(d.next_owners[0].task)}`:'없음'}</b></div>
            {primary&&<button className="kb3 pri big" disabled={primary.blocked} onClick={primary.run}>{primary.label} →</button>}</div>
        </div>

        {r.request_type==='FAULT'&&<EvidenceCompare requestId={r.id}/>}

        <div className="cols">
          <div className="pane">
            <div className="ph">요청 내용</div>
            <div className="pb">
              <h2 className="problem">{titleOf(r)}</h2>
              <div className="tags">
                <span className="tag-box"><small>제품</small><b>{r.product_code}</b></span>
                {r.request_type==='FAULT'?<span className="tag-box"><small>고장</small><b>{r.fault_id??'미확인'}</b></span>:<span className="tag-box"><small>문서 신호</small><b>{r.doc_signal??'미확인'}</b></span>}
                <span className={r.origin==='SYSTEM_CANDIDATE'?'tag-box sys':'tag-box'}><small>작성</small><b>{r.origin==='SYSTEM_CANDIDATE'?'시스템 초안':'직접 작성'}</b></span>
              </div>
              {r.receiver_unconfirmed&&<div className="warn-note">수신 부서 미확인 — 전달 준비가 끝난 상태가 아닙니다.</div>}
              <div className="facts">
                {([['확인된 사실',r.confirmed_facts,'fact'],['불일치',r.inconsistencies,'conflict'],['부족한 정보',r.missing_info,'missing'],['검토 과제',r.review_task,'task'],['단정하지 않는 내용',r.not_claimed,'claim']] as const).map(([label,value,kind])=><div key={label} className="fact-row">
                  <span className="k">{label}</span>
                  <div className={value?`fact-box ${kind}`:'fact-box empty'}>{value??'기록 없음'}</div>
                </div>)}
                <div className="fact-row"><span className="k">사용 조건</span><div className={r.condition_summary?'fact-box':'fact-box empty'}>{r.condition_summary??'미확인'}</div></div>
                <div className="fact-row"><span className="k">제안 부서</span><div className="chips">{r.proposed_depts.length?r.proposed_depts.map(x=><span key={x} className="dept-chip">{deptName(x)}</span>):<span className="fact-box empty">미확인</span>}</div></div>
              </div>
            </div>
          </div>

          <div className="stack">
            <div className="pane">
              <div className="ph"><h2>근거 문의</h2><div className="r"><span className="meta">{d.evidence.filter(e=>!e.removed_at).length}건 유효</span></div></div>
              <div className="pb">{d.evidence.length?<div className="cand-list">{d.evidence.map(e=><div key={e.id} className={e.removed_at?'ev out':'ev'}>
                <Link className="link" to={`/cs/tickets/${encodeURIComponent(e.ticket_id)}`}>{e.ticket_external_id??e.ticket_id}</Link>
                <p>해결 사례: {e.case_external_id??e.case_id??'연결 없음'} · {e.removed_at?`제외됨: ${e.removal_reason??'사유 미확인'}`:'유효한 근거'}</p></div>)}</div>:<p className="none">연결된 근거 없음</p>}</div>
            </div>
            <div className="pane">
              <div className="ph"><h2>다음 담당과 할 일 (API 조회 결과)</h2></div>
              <div className="pb">{d.next_owners.length?d.next_owners.map((n,i)=><p key={i} className="todo"><b>{ACTOR_LABEL[n.actor]??n.actor}</b> · {n.dept_code?`${deptName(n.dept_code)} · `:n.user_id?'':'담당자 미배정 · '}{taskName(n.task)}</p>):<p className="none">API가 반환한 다음 작업 없음</p>}<p className="meta">표시된 다음 작업을 실행하거나 완료 처리하지 않습니다.</p></div>
            </div>
          </div>

          <div className="stack">
            <div className="pane">
              <div className="ph"><h2>현재 회차 수신 부서</h2></div>
              <div className="pb">
                {d.departments.length?<table className="log"><thead><tr><th>부서</th><th>상태</th><th>판본</th><th>기한</th></tr></thead><tbody>{d.departments.map(x=><tr key={`${x.round}-${x.dept_code}`}><td>{deptName(x.dept_code)}{x.carried_over&&<span className="chip x">이월</span>}</td><td><span className="chip">{DEPT_STATUS[x.status]??x.status}</span></td><td>{x.sent_version??'미확인'}</td><td>{x.reply_due_at?formatStamp(x.reply_due_at):'미확인'}</td></tr>)}</tbody></table>:<p className="none">수신 부서 기록 없음</p>}
                <h3>부서 회신</h3>
                {d.replies.length?d.replies.map(x=><div key={x.id} className="dept-ans"><span className="meta">{deptName(x.dept_code)} · 회차 {x.round} · 판본 {x.request_version} · {x.reply_type}</span><div className="dept-body">{x.reason??'사유 기록 없음'}</div></div>):<p className="none">회신 기록 없음</p>}
              </div>
            </div>
            <div className="pane">
              <div className="ph"><h2>부서 조치</h2></div>
              <div className="pb">
                {d.actions.length?d.actions.map(x=><div key={x.id} className="dept-ans"><span className="meta">{deptName(x.dept_code)} · {x.action_date}</span><div className="dept-body">{x.action}</div><p className="meta">적용 대상: {x.action_scope}</p><p className="meta">근거: {x.action_evidence}</p></div>):<p className="none">조치 기록 없음</p>}
                <h3>후속 확인 (고객 적용·재발 확인과 구분)</h3>
                <p>계획: {r.followup_plan??'미기록'}</p><p>결과: {r.followup_result??'미기록'}</p>
                <p className="meta">고객별 적용·재발 여부는 이 응답만으로 확인할 수 없습니다.</p>
              </div>
            </div>
          </div>
        </div>

        <div className="pane irx"><ImprovementActions key={r.id} onPrimary={reportPrimary} data={d} onEditingChange={setActing} onSaved={saved} onReload={refresh} uncertain={actionsUncertain} onUncertain={()=>setUncertainAt(detailUpdates)} locked={writing}/></div>
        <div className="cols2">
          <div className="pane irx"><ImprovementEditor onEditingChange={setWriting} locked={acting} key={`${r.id}:${r.version}`} record={r} onSaved={saved} onReload={()=>{void detail.refetch();}}/></div>
          <div className="pane irx"><ImprovementVersions key={r.id} requestId={r.id}/></div>
        </div>
      </>}
    </div>
  </main>;
}
