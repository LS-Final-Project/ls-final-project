import { useCallback, useEffect, useRef, useState } from 'react';
import ImprovementContent from '../components/ImprovementContent';
import ImprovementVersions from '../components/ImprovementVersions';
import ImprovementActions, { FollowupSignals, type ActionControl, type ActionRow, type PrimaryAction } from '../components/ImprovementActions';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { listFaults } from '../components/ResolutionCaseForm';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getImprovement, listImprovements, type ImprovementDetail } from '../api/cs-improvements';
import { ErrorState, Loading } from '../components/states';
import { useHotkey } from '../ls-shell/kv';
import LogTable, { sortRows, type LogColumn, type LogSort } from './LogTable';
import { CAUSE_LABEL, REQUEST_STATUS_LABEL, dayOf, deptName, formatStamp } from '../ls-shell/cs-labels';
import { addEvidence, getCaseComparison, removeEvidence, type CaseComparison } from '../api/cs-repeat-cases';
import { me } from '../api/platform';
import type { RequestSummary } from '../ls-shell/cs-labels';
import './ticket-detail-v3.css';
import './list-cond.css';
import './improvements-v3.css';

// 단계 바: 상태는 서버가 정한다. 화면은 지금 어느 단계인지만 보인다.
const PAGE = 10;
// 시스템 후보가 만들어진 기준(기획서 4.4): 90일 안 서로 다른 고객 3곳, 또는 수정 펌웨어가 이미 배포된 고장은 고객 1곳부터.
const REASON_LABEL: Record<string, string> = { REPEAT_90D_3_CUSTOMERS: '90일 안 고객 3곳', FIRMWARE_FIX_RELEASED: '수정 펌웨어 배포 (1곳부터)' };
const STEPS = ['작성', '검토', '부서 전달', '부서 처리', '후속 확인', '완료'];
// 상태별로 지금 해야 할 단계(깜빡이는 단계). 그 앞은 끝난 단계다. 작성 중 요청은 내용이 갖춰지면 '검토'로 넘어간다(아래 at 계산).
const STEP_OF: Record<string, number> = { DRAFT: 0, REVIEWED: 2, SENT: 3, ACKNOWLEDGED: 3, ADOPTED: 3, INFO_REQUESTED: 3, REJECTED: 3, ACTION_TAKEN: 4, FOLLOW_UP: 4, COMPLETED: 6 };
// 서버가 정한 다음 할 일(next_owners.task)의 화면 이름.
const TASK_LABEL: Record<string, string> = {
  REVIEW: '검토 완료 기록', CONFIRM_RECEIVER: '받는 부서 확인', SEND: '부서 전달', SUPPLEMENT: '자료 보완 후 다시 요청',
  REVIEW_REJECTION: '반려 확인', PLAN_FOLLOWUP: '후속 확인 계획 기록', RECORD_FOLLOWUP: '후속 확인 결과와 완료 기록',
  ACKNOWLEDGE: '수신 확인', REPLY: '회신(채택·추가 정보 요청·반려)', RECORD_ACTION: '개선 조치 기록',
};
const ACTOR_LABEL: Record<string, string> = { CS_OWNER: 'C/S 담당자', DEPARTMENT: '부서' };
const taskName = (task: string) => TASK_LABEL[task] ?? task;
type Titled = { origin: string; request_type: string; product_code: string; fault_id: string | null; problem_statement: string };
// 시스템 후보의 문제 문장은 "제품 고장ID: 판정 사유"다(candidates.draft). 화면 제목에는 판정 사유를 빼고 고장 증상을 보인다.
// 사람이 쓴 문장(수동 요청이나 내용 수정)은 그대로 보인다.
const systemTitle = (r: Titled) => r.origin === 'SYSTEM_CANDIDATE' && r.request_type === 'FAULT' && !!r.fault_id && r.problem_statement.startsWith(`${r.product_code} ${r.fault_id}: `);
// 시스템 초안의 내용이 만들 때 규칙으로 채운 그대로인지(candidates.draft의 문제 문장·확인된 사실 형식). 사람이 고치거나 AI 초안을 반영하면 아니다.
const RULE_FACTS = /^해결 기록 \d+건, 고객 \d+곳, 고객 식별자 없는 기록 \d+건\.$/;
const ruleOnly = (r: Titled & { confirmed_facts: string | null }) => systemTitle(r) && (r.confirmed_facts === null || RULE_FACTS.test(r.confirmed_facts));
// 고장 목록(KAN-209)에서 제품별 증상을 읽는다. 못 읽으면 고장 ID만 보인다.
function useSymptoms(products: string[]) {
  const mode = getApiMode();
  const unique = [...new Set(products)];
  const results = useQueries({ queries: unique.map(product => ({ queryKey: ['cs-faults', mode, product], queryFn: ({ signal }: { signal: AbortSignal }) => listFaults(product, signal), retry: false, staleTime: 300000 })) });
  const map = new Map<string, string>();
  results.forEach((result, index) => result.data?.data?.items?.forEach(item => map.set(`${unique[index]}:${item.fault_id}`, item.symptom)));
  return (r: Titled) => systemTitle(r) ? `${r.fault_id} · ${map.get(`${r.product_code}:${r.fault_id}`) ?? '고장 증상 미확인'}` : r.problem_statement;
}
const DEPT_STATUS: Record<string, string> = { SENT: '받음(확인 전)', ACKNOWLEDGED: '수신 확인', ADOPTED: '채택', INFO_REQUESTED: '추가 정보 요청', REJECTED: '반려', ACTION_TAKEN: '조치 기록' };

type CaseRow = CaseComparison['data']['items'][number];
type Evidence = ImprovementDetail['data']['evidence'][number];
// 규칙 비교(기획서 16쪽: 사용 조건이 구조화된 칸에 있으면 규칙으로 비교). 해결 기록 칸 값을 항목별로 모아
// 같은지·다른지를 먼저 보이고, 표에서 근거별 값을 확인한다. 묶음이 맞는지는 사람이 정한다.
// split: 다르면 같은 고장 ID라도 나눌 수 있는 조건(기획서 15쪽: 펌웨어·운전 조건, 원인). ref: 누가·어떻게 고쳤는지라 묶음과 무관한 참고.
// palette: 값마다 다른 색(원인 유형·해결 부서). 나머지는 다수와 다른 값만 표시한다. 어느 근거에도 값이 없는 항목은 칸을 그리지 않는다.
const COMPARE: { key: string; label: string; weight: 'split' | 'ref'; palette?: boolean; value: (row: CaseRow) => string | null }[] = [
  { key: 'cause', label: '원인 유형', weight: 'split', palette: true, value: row => row.root_cause_type ? CAUSE_LABEL[row.root_cause_type] ?? row.root_cause_type : null },
  { key: 'fw', label: '고장 당시 FW', weight: 'split', value: row => row.fw_version_at_issue ?? null },
  // 해결 부서에는 이관 부서 목록 밖의 C/S 1차(GCS_L1)도 온다(해결 기록 입력 화면과 같은 이름).
  { key: 'dept', label: '해결 부서', weight: 'ref', palette: true, value: row => row.resolved_by_dept ? (row.resolved_by_dept === 'GCS_L1' ? 'C/S 1차 (GCS_L1)' : deptName(row.resolved_by_dept)) : null },
  { key: 'onsite', label: '현장 방문', weight: 'ref', value: row => row.onsite_visit == null ? null : row.onsite_visit ? '있음' : '없음' },
];
type Verdict = 'same' | 'check' | 'ref' | 'none';
// 기준은 포함된 근거만으로 정한다. 후보 행은 그 기준과 비교해 보인다.
function summarize(evidence: CaseRow[]) {
  return COMPARE.map(field => {
    const counts = new Map<string, number>();
    evidence.forEach(row => { const value = field.value(row); if (value !== null) counts.set(value, (counts.get(value) ?? 0) + 1); });
    const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    // 가장 많은 값이 하나로 정해질 때만 기준으로 삼는다. 동률이면 기준 없이 모두 다른 값으로 본다.
    const base = sorted.length && (sorted.length === 1 || sorted[0][1] > sorted[1][1]) ? sorted[0][0] : null;
    const verdict: Verdict = !sorted.length ? 'none' : sorted.length === 1 ? 'same' : field.weight === 'split' ? 'check' : 'ref';
    const differs = (row: CaseRow) => { const value = field.value(row); return value !== null && (base === null ? sorted.length > 1 : value !== base); };
    // 많은 값부터 0, 1, 2… 색 번호. 기준에 없는 후보 값은 마지막 색. 색은 6가지를 돌려 쓴다.
    const tone = (value: string) => { const at = sorted.findIndex(([key]) => key === value); return (at < 0 ? sorted.length : at) % 6; };
    return { ...field, base, verdict, differs, tone };
  });
}
const rowOf = (row: CaseRow) => `${row.row_kind}:${row.ticket_id}:${row.case_id ?? ''}`;

// 근거 비교·관리(반복 사례 모음 CS0201을 합침). 포함된 근거와 같은 묶음의 후보 문의를 한 표에서 비교하고, 넣고 뺀다.
// openSignal: 값이 바뀔 때마다(개선 요청 작성의 근거 칸을 누름) 펼치고 이 상자로 스크롤한다.
function EvidenceCompare({ data, locked, onSaved, onEditingChange, openSignal = 0 }: { data: ImprovementDetail['data']; locked: boolean; onSaved: (result: ImprovementDetail) => void; onEditingChange: (editing: boolean) => void; openSignal?: number }) {
  const mode = getApiMode();
  const r = data.request;
  const comparison = useQuery({ queryKey: ['cs-repeat-cases', mode, r.id, 'comparison', r.version], queryFn: ({ signal }) => getCaseComparison(r.id, signal), retry: false, staleTime: 0 });
  const all = comparison.data?.data.items ?? [];
  const evidence = all.filter(row => row.row_kind !== 'CANDIDATE');
  const candidates = all.filter(row => row.row_kind === 'CANDIDATE');
  const rows = [...evidence, ...candidates];
  const stats = summarize(evidence).filter(stat => rows.some(row => stat.value(row) !== null));
  const checks = stats.filter(stat => stat.verdict === 'check').length;
  const refs = stats.filter(stat => stat.verdict === 'ref').length;
  const needsLook = (row: CaseRow) => stats.some(stat => stat.weight === 'split' && stat.differs(row));
  const active = data.evidence.filter(e => !e.removed_at);
  const evidenceOf = (row: CaseRow): Evidence | undefined => active.find(e => e.ticket_id === row.ticket_id && (e.case_id ?? null) === (row.case_id ?? null));
  // 판단할 것(묶음을 가르는 조건의 차이나 넣을지 볼 후보)이 있을 때만 펼쳐 둔다. 담당자가 누르면 그 상태를 따른다.
  const [opened, setOpened] = useState<boolean | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => { if (!openSignal) return; setOpened(true); box.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }); }, [openSignal]);
  const open = opened ?? (checks > 0 || candidates.length > 0);
  const [removing, setRemoving] = useState<{ key: string; evidence: Evidence; label: string; reason: string } | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { onEditingChange(removing !== null); return () => onEditingChange(false); }, [removing, onEditingChange]);
  // 근거를 넣고 빼는 것은 C/S 담당자만(서버도 막는다). 다른 역할에는 버튼을 끈다.
  const identity = useQuery({ queryKey: ['cs-improvement-actor', mode], queryFn: me, enabled: mode === 'live', retry: false, staleTime: 0 });
  const agent = Array.isArray(identity.data?.data?.roles) && (identity.data!.data.roles as string[]).includes('cs_agent');
  const writable = mode === 'live' && agent && r.status !== 'COMPLETED' && !locked && !busy && !uncertain;
  const needsCase = r.request_type === 'FAULT';
  const write = async (send: (signal: AbortSignal) => Promise<ImprovementDetail>, done: string): Promise<boolean> => {
    if (controller.current) return false;
    const current = new AbortController(); controller.current = current;
    setBusy(true); setNotice(null);
    try {
      const result = await send(current.signal);
      if (current.signal.aborted) return false;
      setRemoving(null); setNotice({ text: done, error: false }); onSaved(result);
      return true;
    } catch (error) {
      if (current.signal.aborted) return false;
      if (error instanceof ApiError && error.status !== 408 && error.status < 500) {
        setNotice({ text: error.status === 409 ? '다른 곳에서 요청이 먼저 바뀌었습니다. "최신 요청 조회"로 다시 읽은 뒤 확인하세요.' : error.message, error: true });
      } else {
        setUncertain(true);
        setNotice({ text: '저장 결과를 확인할 수 없습니다. 서버에 반영됐을 수 있으니 다시 보내지 말고 "최신 요청 조회"로 확인하세요.', error: true });
      }
    } finally {
      if (controller.current === current) controller.current = null;
      if (!current.signal.aborted) setBusy(false);
    }
    return false;
  };
  const [direct, setDirect] = useState({ ticket: '', case: '' });
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const directOk = UUID.test(direct.ticket.trim()) && (!needsCase || UUID.test(direct.case.trim()));
  const addDirect = () => { if (!directOk) return; void write(signal => addEvidence(r.id, { ticket_id: direct.ticket.trim(), ...(needsCase ? { case_id: direct.case.trim() } : {}), version: r.version }, signal), '문의를 근거에 넣었습니다.').then(ok => { if (ok) setDirect({ ticket: '', case: '' }); }); };
  const add = (row: CaseRow) => void write(signal => addEvidence(r.id, { ticket_id: row.ticket_id, case_id: row.case_id, version: r.version }, signal), `${row.ticket_external_id ?? '문의'}를 근거에 넣었습니다.`);
  const remove = () => { if (removing && removing.reason.trim()) void write(signal => removeEvidence(r.id, removing.evidence.id, { reason: removing.reason.trim(), version: r.version }, signal), `${removing.label}를 근거에서 뺐습니다.`); };
  const actor = (by: string | null) => by === null ? '시스템' : '담당자';
  const log = data.evidence.flatMap(e => [
    { key: `${e.id}:add`, at: e.added_at, kind: '넣음', ticket: e.ticket_external_id, by: e.added_by, reason: null as string | null },
    ...(e.removed_at ? [{ key: `${e.id}:out`, at: e.removed_at, kind: '뺌', ticket: e.ticket_external_id, by: e.removed_by, reason: e.removal_reason }] : []),
  ]).sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const removed = data.evidence.length - active.length;
  return <div className="pane cmp-pane" ref={box}>
    <div className="ph"><button type="button" className="fold" aria-expanded={open} onClick={() => setOpened(!open)}><span className="caret" aria-hidden="true">{open ? '▾' : '▸'}</span><h2>근거 비교</h2></button>
      <div className="r">{comparison.isSuccess && <>
        {checks > 0 && <span className="chip w">판단 필요 {checks}</span>}{refs > 0 && <span className="chip x">참고 {refs}</span>}{!checks && !refs && <span className="chip">모두 같음</span>}
        <span className="meta">근거 {evidence.length}건{candidates.length > 0 && ` · 후보 ${candidates.length}건`}{removed > 0 && ` · 뺀 근거 ${removed}건`}</span></>}</div></div>
    {comparison.isFetching && !comparison.isSuccess && <Loading label="근거 비교를 불러오는 중" />}
    {comparison.isError && <div className="pb"><ErrorState error={comparison.error} onRetry={() => comparison.refetch()} /></div>}
    {notice && <div className="pb"><div className={notice.error ? 'ai-note err' : 'ai-note'} role={notice.error ? 'alert' : 'status'}>{notice.text}</div></div>}
    {open && comparison.isSuccess && <>
      <LogTable label="근거 비교" rows={rows} rowKey={rowOf} fallback="비교할 근거가 없습니다."
        columns={[
          { key: 'kind', header: '구분', width: 56, className: 'c', cell: row => row.row_kind === 'CANDIDATE' ? <span className="chip w">후보</span> : <span className="chip">포함</span> },
          { key: 'ticket', header: '문의', width: 120, cell: row => <Link className="link" to={`/cs/tickets/${encodeURIComponent(row.ticket_id)}`}>{row.ticket_external_id ?? row.ticket_id.slice(0, 8)}</Link> },
          { key: 'customer', header: '고객', width: 96, cell: row => row.customer_external_id },
          { key: 'received', header: '접수일', width: 92, className: 'c', cell: row => dayOf(row.ticket_received_at) },
          ...stats.map(stat => ({ key: stat.key, header: stat.label, cell: (row: CaseRow) => {
            const value = stat.value(row);
            if (value === null) return <span className="none">미기록</span>;
            const cls = stat.palette ? `cmp tone tone-${stat.tone(value)}` : !stat.differs(row) ? 'cmp' : stat.weight === 'split' ? 'cmp diff' : 'cmp ref';
            return <span className={cls}>{value}</span>;
          } })),
          { key: 'check', header: '확인', width: 88, className: 'c', cell: row => needsLook(row) ? <span className="chip w">원문 확인</span> : <span className="chip">같음</span> },
          { key: 'manage', header: '관리', width: 76, className: 'c', cell: row => {
            if (row.row_kind === 'CANDIDATE') {
              const noCase = needsCase && !row.case_id;
              return <button className="kb3 sm" disabled={!writable || noCase || removing !== null} title={noCase ? '해결 기록이 없어 근거로 넣을 수 없습니다' : '이 문의를 근거에 넣습니다'} onClick={() => add(row)}>넣기</button>;
            }
            const e = evidenceOf(row);
            return <button className="kb3 sm" disabled={!writable || !e || removing !== null} onClick={() => e && setRemoving({ key: rowOf(row), evidence: e, label: row.ticket_external_id ?? '문의', reason: '' })}>빼기</button>;
          } },
        ]} />
      {comparison.data.data.hidden_case_count > 0 && <div className="pb"><p className="meta">볼 수 있는 범위 밖의 근거 {comparison.data.data.hidden_case_count}건은 비교표에서 뺐습니다.</p></div>}
      {/* 후보 목록에 없는 문의를 직접 넣는다(반려 뒤 새 근거 재요청 포함). */}
      <form className="pb cmp-add" onSubmit={event => { event.preventDefault(); addDirect(); }}>
        <span className="k">문의 직접 추가</span>
        <input aria-label="추가할 문의 ID" placeholder="문의 시스템 ID" value={direct.ticket} disabled={!writable || removing !== null} onChange={event => setDirect({ ...direct, ticket: event.target.value })} />
        {needsCase && <input aria-label="추가할 해결 사례 ID" placeholder="해결 사례 시스템 ID" value={direct.case} disabled={!writable || removing !== null} onChange={event => setDirect({ ...direct, case: event.target.value })} />}
        <button className="kb3 sm" type="submit" disabled={!writable || removing !== null || !directOk}>넣기</button>
      </form>
      {removing && <form className="pb sendbox cmp-remove" onSubmit={event => { event.preventDefault(); remove(); }}>
        <b>{removing.label} 근거에서 빼기</b>
        <label className="fr top"><span className="k">빼는 사유</span>
          <textarea aria-label="빼는 사유" autoFocus required maxLength={2000} rows={2} disabled={busy} value={removing.reason} onChange={event => setRemoving({ ...removing, reason: event.target.value })} /></label>
        <div className="row2"><button className="kb3 sm" type="button" disabled={busy} onClick={() => setRemoving(null)}>취소</button><button className="kb3 sm pri" type="submit" disabled={busy || !removing.reason.trim()}>{busy ? '처리 중' : '근거에서 빼기'}</button></div>
      </form>}
      <div className="pb"><button type="button" className="fold sub" aria-expanded={showLog} onClick={() => setShowLog(!showLog)}><span className="caret" aria-hidden="true">{showLog ? '▾' : '▸'}</span>근거 변경 기록 {log.length}건</button></div>
      {showLog && <LogTable label="근거 변경 기록" rows={log} rowKey={entry => entry.key} fallback="근거 변경 기록이 없습니다."
        columns={[
          { key: 'at', header: '일시', width: 130, className: 'c', cell: entry => formatStamp(entry.at) },
          { key: 'kind', header: '구분', width: 56, className: 'c', cell: entry => entry.kind },
          { key: 'ticket', header: '문의', width: 120, cell: entry => entry.ticket ?? '-' },
          { key: 'by', header: '처리자', width: 72, className: 'c', cell: entry => actor(entry.by) },
          { key: 'reason', header: '사유', className: 't', cell: entry => entry.reason ?? '-' },
        ]} />}
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
  // 목록: 문의 조회처럼 머리를 눌러 정렬하고, 10건씩 쪽으로 나눠 본다. 기본은 최근 작성순.
  const [sort,setSort]=useState<LogSort>({key:'created',dir:'desc'});
  const [page,setPage]=useState(0);
  // 목록 조건(문의 조회와 같은 조건 줄). 받아 온 목록(최근 200건) 안에서 거른다.
  const [query,setQuery]=useState('');const [fStatus,setFStatus]=useState('');const [fProduct,setFProduct]=useState('');const [fOrigin,setFOrigin]=useState('');
  const clearFilters=()=>{setQuery('');setFStatus('');setFProduct('');setFOrigin('');setPage(0);};
  const [primary,setPrimary]=useState<PrimaryAction>(null);
  const reportPrimary=useCallback((action:PrimaryAction)=>setPrimary(action),[]);
  // 처리 동작 목록(검토·전달·부서 처리). 표 대신 부서 줄·제목 줄 버튼으로 보인다.
  const [control,setControl]=useState<ActionControl|null>(null);
  const reportControl=useCallback((value:ActionControl|null)=>setControl(value),[]);
  const actButton=(row:ActionRow,label=row.label)=><button key={`${row.kind}:${row.target??''}`} type="button" className={!row.reason&&!control?.blocked?'kb3 sm pri':'kb3 sm'} disabled={!!row.reason||!!control?.blocked} title={row.reason??row.label} onClick={()=>control?.start(row)}>{label}</button>;
  const SHORT:Record<string,string>={reply:'회신',action:'조치 기록'};
  const [acting,setActing]=useState(false);
  const [sorting,setSorting]=useState(false);
  // 검토 완료를 막을 이유(요청 내용이 비었거나 반영하지 않은 AI 초안이 있음). 요청 내용 상자가 알려 준다.
  const [reviewBlock,setReviewBlock]=useState<string|null>(null);
  const [showEvidence,setShowEvidence]=useState(0);
  // 처리 결과 불명·충돌 때의 상세 갱신 횟수. 그 뒤 서버 상세를 실제로 한 번 더 받아야(조회 성공 또는 저장 응답) 처리 동작을 다시 연다. 다시 읽기가 실패하면 횟수가 그대로라 막은 채로 둔다.
  const [uncertainAt,setUncertainAt]=useState<number|null>(null);
  const editing=writing||acting||sorting;
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
    {key:'title',header:'문제',className:'t',sortValue:item=>titleOf(item),cell:item=><Link className="link" to={`/cs/improvements/${encodeURIComponent(item.id)}`}>{titleOf(item)}</Link>},
    {key:'product',header:'제품',width:150,sortValue:item=>item.product_code,cell:item=>item.product_code},
    {key:'status',header:'상태',width:110,className:'c',sortValue:item=>STEP_OF[item.status]??-1,cell:item=><span className={`chip st-${item.status.toLowerCase()}`}>{REQUEST_STATUS_LABEL[item.status]??item.status}</span>},
    {key:'created',header:'작성일',width:96,className:'c',sortValue:item=>Date.parse(item.created_at),cell:item=>dayOf(item.created_at)},
  ];
  const titleOf=useSymptoms([...(list.data?.data.items??[]).filter(systemTitle).map(item=>item.product_code),...(r&&systemTitle(r)?[r.product_code]:[])]);
  const at=!r?-1:r.status==='DRAFT'&&!reviewBlock?1:STEP_OF[r.status]??-1;
  const done=(index:number)=>r?.status==='COMPLETED'?true:index<at;
  return <main className="td3 ir3">
    <div className="title">
      <span className="path">C/S 관리 ›</span><h1>{`개선요청 ${requestId?'상세':'목록'}`}</h1><code className="kv-code">CS0301</code>
      <div className="btns">
        {requestId
          ? <>{control?.rows.filter(row=>row.kind==='owner').map(row=>actButton(row))}<Link className="kb3" to="/cs/improvements">개선요청 목록</Link></>
          : null}
        {requestId&&<span className="vsep"/>}
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
        {(()=>{const faults=list.data.data.items.filter(item=>item.request_type==='FAULT');const products=[...new Set(faults.map(item=>item.product_code))].sort();
          return <form className="cond lc" onSubmit={event=>{event.preventDefault();refresh();}}>
            <label className="search">
              <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
              <input type="text" aria-label="검색어" placeholder="문제, 제품, 고장 ID로 검색" autoComplete="off" value={query} onChange={event=>{setQuery(event.target.value);setPage(0);}}/>
            </label>
            <label><span className="k">상태</span><select aria-label="상태" value={fStatus} onChange={event=>{setFStatus(event.target.value);setPage(0);}}><option value="">전체</option><option value="OPEN">진행 중 전체</option>{Object.keys(STEP_OF).map(key=><option key={key} value={key}>{REQUEST_STATUS_LABEL[key as RequestSummary['status']]??key}</option>)}</select></label>
            <label><span className="k">제품</span><select aria-label="제품" value={fProduct} onChange={event=>{setFProduct(event.target.value);setPage(0);}}><option value="">전체</option>{products.map(value=><option key={value}>{value}</option>)}</select></label>
            <label><span className="k">작성</span><select aria-label="작성" value={fOrigin} onChange={event=>{setFOrigin(event.target.value);setPage(0);}}><option value="">전체</option><option value="SYSTEM_CANDIDATE">시스템 후보</option><option value="MANUAL">직접 작성</option></select></label>
            <button type="button" className="kb3 sm clear" onClick={clearFilters}><span aria-hidden="true">↺</span> 조건 지우기</button>
            <button type="submit" hidden tabIndex={-1} aria-hidden="true"/>
          </form>;})()}
        <div className="pane">
          {(()=>{// 화면은 고장 반복 요청만 다룬다(문서 요청은 범위 제외). API로 만들어진 문서 요청이 있어도 목록에 올리지 않는다.
            const faults=list.data.data.items.filter(item=>item.request_type==='FAULT');
            const needle=query.trim().toLocaleLowerCase();
            const items=faults.filter(item=>(!fStatus||(fStatus==='OPEN'?item.status!=='COMPLETED':item.status===fStatus))&&(!fProduct||item.product_code===fProduct)&&(!fOrigin||item.origin===fOrigin)
              &&(!needle||`${titleOf(item)} ${item.problem_statement} ${item.product_code} ${item.fault_id??''}`.toLocaleLowerCase().includes(needle)));const pages=Math.max(1,Math.ceil(items.length/PAGE));const at=Math.min(page,pages-1);
            const shown=sortRows(items,listColumns,sort).slice(at*PAGE,at*PAGE+PAGE);
            return <>
              <div className="ph">개선요청<div className="r"><span className="meta" title="최근 최대 200건을 받아 온 기준입니다">총 {faults.length}건 중 {items.length}건</span><span className="meta" title="C/S 담당이 없는 진행 중 요청입니다(완료 제외). C/S 관리자가 미배정 자동 배정으로 채웁니다">미배정 {faults.filter(item=>!item.cs_owner&&item.status!=='COMPLETED').length}건</span></div></div>
              <LogTable label="개선요청 목록" columns={listColumns} rows={shown} rowKey={item=>item.id} fallback={faults.length?'조건에 맞는 개선요청이 없습니다. 조건 지우기로 조건을 비울 수 있습니다.':'조회 가능한 개선요청이 없습니다.'}
                sort={sort} onSort={key=>{setPage(0);setSort(current=>current.key===key?{key,dir:current.dir==='asc'?'desc':'asc'}:{key,dir:key==='created'?'desc':'asc'});}}/>
              {pages>1&&<nav className="pager" aria-label="개선요청 쪽">
                <button type="button" className="kb3 sm" disabled={at===0} onClick={()=>setPage(at-1)}>이전</button>
                {Array.from({length:pages},(_,i)=>i).filter(i=>i===0||i===pages-1||Math.abs(i-at)<=2).map((i,k,arr)=><span key={i} className="pg">
                  {k>0&&i-arr[k-1]>1&&<span className="gap">…</span>}
                  <button type="button" className={i===at?'kb3 sm pri':'kb3 sm'} aria-current={i===at?'page':undefined} aria-label={`${i+1}쪽`} onClick={()=>setPage(i)}>{i+1}</button></span>)}
                <button type="button" className="kb3 sm" disabled={at===pages-1} onClick={()=>setPage(at+1)}>다음</button>
              </nav>}
            </>;})()}
        </div>
      </>}

      {r&&d&&<>
        <div className="info">
          <div className="k">제품</div><div className="v">{r.product_code}</div>
          <div className="k">고장</div><div className="v">{r.fault_id??'미확인'}</div>
          <div className="k">상태</div><div className="v"><strong className="ir-state">{REQUEST_STATUS_LABEL[r.status]??r.status}</strong></div>
          <div className="k">생성 사유</div><div className="v last">{r.origin==='SYSTEM_CANDIDATE'?REASON_LABEL[r.candidate_reason_code??'']??'시스템 후보':'직접 작성'}</div>
          <div className="k nb">판본 / 회차</div><div className="v nb">{r.version} / {r.current_round}</div>
          <div className="k nb">조건 확인</div><div className="v nb">{r.condition_status==='UNKNOWN'?<span className="chip w">미확인</span>:r.condition_status}</div>
          <div className="k nb">수정 FW</div><div className="v nb">{r.fw_fixed_version??'미확인'} / {r.fw_released_on??'미확인'}</div>
          <div className="k nb">출처</div><div className="v nb last">{r.data_origin} · {r.derivation_kind}</div>
        </div>

        <div className="steps">
          {STEPS.map((name,index)=>{const cls=done(index)?'done':index===at?'cur':'';return <span key={name} className="sw">{index>0&&<span className={done(index-1)?'ln':'ln g'}/>}<span className={`st ${cls}`}><span className="dot">{done(index)?'✓':index+1}</span>{name}</span></span>;})}
          <div className="now"><div><small>지금 할 일</small><b>{d.next_owners[0]?(d.next_owners[0].actor==='CS_OWNER'?taskName(d.next_owners[0].task):`${d.next_owners[0].dept_code?deptName(d.next_owners[0].dept_code):ACTOR_LABEL[d.next_owners[0].actor]??d.next_owners[0].actor} · ${taskName(d.next_owners[0].task)}`):'없음'}</b></div>
            {primary&&primary.kind!=='send'&&<button className="kb3 pri big" disabled={primary.blocked} title={primary.why} onClick={primary.run}>{primary.label} →</button>}</div>
        </div>

        <div className="irx ia-host"><ImprovementActions key={r.id} inline={false} reviewBlocked={reviewBlock} onControl={reportControl} onPrimary={reportPrimary} data={d} onEditingChange={setActing} onSaved={saved} onReload={refresh} uncertain={actionsUncertain} onUncertain={()=>setUncertainAt(detailUpdates)} locked={writing||sorting}/></div>

        {<EvidenceCompare data={d} locked={writing||acting} onSaved={saved} onEditingChange={setSorting} openSignal={showEvidence}/>}

        <div className="cols">
          <ImprovementContent key={r.id} request={r} title={titleOf(r)} ruleOnly={ruleOnly(r)} locked={acting||sorting} onSaved={saved} onReload={()=>{void detail.refetch();}} onEditingChange={setWriting} onPending={setReviewBlock} next={r.status==='REVIEWED'?control?.rows.filter(row=>row.kind==='send'&&!row.done).map(row=>actButton(row))[0]:undefined} tags={<div className="tags">
            <span className="tag-box"><small>제품</small><b>{r.product_code}</b></span>
            <span className="tag-box"><small>고장</small><b>{r.fault_id??'미확인'}</b></span>
            <button type="button" className="tag-box link-box" title="근거 비교 열기" onClick={()=>setShowEvidence(n=>n+1)}><small>근거</small><b>{d.evidence.filter(e=>!e.removed_at).length}건</b></button>
          </div>}/>

          <div className="stack">
            <div className="pane">
              <div className="ph"><h2>수신 부서와 할 일</h2>{r.current_round>0&&<div className="r"><span className="meta">{r.current_round}회차</span></div>}</div>
              <div className="pb">
                {d.departments.length?<table className="log"><thead><tr><th>부서</th><th>상태</th><th>할 일</th><th>처리</th></tr></thead><tbody>{d.departments.map(x=>{const task=d.next_owners.find(n=>n.actor==='DEPARTMENT'&&n.dept_code===x.dept_code);return <tr key={`${x.round}-${x.dept_code}`}><td>{deptName(x.dept_code)}{x.carried_over&&<span className="chip x">이월</span>}</td><td><span className="chip">{DEPT_STATUS[x.status]??x.status}</span></td><td>{task?taskName(task.task):<span className="none">-</span>}</td><td className="acts">{(control?.rows??[]).filter(row=>row.target===x.dept_code&&!row.done).map(row=>actButton(row,SHORT[row.kind]))}</td></tr>;})}</tbody></table>
                  :<p className="none">아직 부서에 전달하지 않았습니다.{r.proposed_depts.length>0&&` 전달하면 제안 부서(${r.proposed_depts.map(deptName).join(', ')})가 받습니다.`}</p>}
                <div id="ia-slot-dept" className="irx ia-slot"/>
                {d.replies.length>0&&<><h3>부서 회신</h3>{d.replies.map(x=><div key={x.id} className="dept-ans"><span className="meta">{deptName(x.dept_code)} · 회차 {x.round} · 판본 {x.request_version} · {x.reply_type}</span><div className="dept-body">{x.reason??'사유 기록 없음'}</div></div>)}</>}
              </div>
            </div>
            <div className="pane">
              <div className="ph"><h2>부서 조치와 후속 확인</h2></div>
              <div className="pb">
                {d.actions.length?d.actions.map(x=><div key={x.id} className="dept-ans"><span className="meta">{deptName(x.dept_code)} · {x.action_date}</span><div className="dept-body">{x.action}</div><p className="meta">적용 대상: {x.action_scope}</p><p className="meta">근거: {x.action_evidence}</p></div>):<p className="none">조치 기록 없음</p>}
                <h3>후속 확인</h3>
                <p>계획: {r.followup_plan??'미기록'}</p><p>결과: {r.followup_result??'미기록'}</p>
                {r.request_type==='FAULT'&&!['DRAFT','REVIEWED'].includes(r.status)&&<div className="irx"><FollowupSignals data={d}/></div>}
              </div>
            </div>
          </div>
        </div>

        <div className="pane irx"><ImprovementVersions key={r.id} requestId={r.id}/></div>
      </>}
    </div>
  </main>;
}
