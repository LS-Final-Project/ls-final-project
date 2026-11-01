import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getImprovement, type ImprovementDetail } from '../api/cs-improvements';
import { addEvidence, getCaseComparison, listImprovementCandidates, listRepeatCandidates, removeEvidence, type CaseComparison } from '../api/cs-repeat-cases';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvBox, KvCond, KvCondField, KvCondRow, KvGrid, KvKey, KvNext, KvSplit, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { CAUSE_LABEL, DEPT_LABEL, REQUEST_STATUS_LABEL, REQUEST_TYPE_LABEL, dayOf, formatStamp, type RequestSummary } from '../ls-shell/cs-labels';

type CaseRow = CaseComparison['data']['items'][number];
type Kind = 'IN' | 'CAND' | 'OUT';
// 표 한 행. 번호·제목·접수일은 현재 API가 준 표시값만 쓴다.
type Row = { key: string; kind: Kind; ticketId: string; ticketNo: string | null; caseId: string | null; caseNo: string | null; customer: string | null; subject: string | null; receivedAt: string | null; evidenceId?: string };
type LogEntry = { key: string; at: string; kind: '추가' | '뺌'; caseNo: string | null; ticketNo: string | null; by: string | null; reason: string | null };

const DOC_SIGNAL_LABEL: Record<NonNullable<RequestSummary['doc_signal']>, string> = {
  NOT_FOUND: '자료에 없음',
  VALUE_CONFLICT: '자료 값이 서로 다름',
  BROKEN_LINK: '자료 링크 끊김',
  REPEATED_SPEC: '같은 사양 반복 문의',
};
const KIND_LABEL: Record<Kind, string> = { IN: '포함', CAND: '후보', OUT: '뺌' };
const FIELDS: { key: string; label: string; value: (row: CaseRow) => string | null }[] = [
  { key: 'product', label: '제품', value: row => row.product_code },
  { key: 'symptom', label: '증상', value: row => row.symptom },
  // 원시 코드는 보이지 않는다. 표시 이름이 없는 값은 이름 없는 유형으로 적는다.
  { key: 'cause_type', label: '원인 유형', value: row => row.root_cause_type && (CAUSE_LABEL[row.root_cause_type] ?? '이름 없는 유형') },
  { key: 'cause', label: '원인', value: row => row.root_cause },
  { key: 'action', label: '조치', value: row => row.action_taken },
  { key: 'dept', label: '해결 부서', value: row => row.resolved_by_dept && (DEPT_LABEL[row.resolved_by_dept] ?? row.resolved_by_dept) },
];
const dash = (value: string | null | undefined) => value ?? '-';
const unrecorded = (value: string | null | undefined) => value || '미기록';
const requestLabel = (item: RequestSummary) => `${item.problem_statement} · ${item.product_code} ${item.fault_id ?? (item.doc_signal ? DOC_SIGNAL_LABEL[item.doc_signal] : '')}`.trim();

// 반복 사례 모음 CS0201. 개선요청의 근거가 되는 같은 문제 문의를 비교해 근거에 넣거나 뺀다.
// 근거 상태와 변경 기록은 요청 상세, 비교 칸은 사례 비교표, 후보 행은 후보 API가 준 값만 쓴다.
export default function CsRepeatCases() {
  const { requestId } = useParams();
  const navigate = useNavigate();
  const mode = getApiMode();
  const access = useBusinessAccess('cs');
  const list = useQuery({ queryKey: ['cs-repeat-cases', mode, 'list'], queryFn: ({ signal }) => listRepeatCandidates(signal), enabled: access.ready, retry: false, staleTime: 0 });
  const reloadBoard = useRef<() => void>(() => {});
  const refresh = () => {
    if (list.isFetching) return;
    void list.refetch();
    reloadBoard.current();
  };
  useHotkey('F8', refresh, access.ready);
  if (!access.ready) return <BusinessGate access={access} title="반복 사례 모음" />;

  const candidates = list.isSuccess ? list.data.data.items : [];
  const title = <>
    <KvTitle path="C/S 관리" title="반복 사례 모음" code="CS0201">
      <button className="kb primary" disabled={list.isFetching} onClick={refresh}>조회<KvKey>F8</KvKey></button>
      {requestId
        ? <Link className="kb" to={`/cs/improvements/${encodeURIComponent(requestId)}`}>요청으로 이동</Link>
        : <button className="kb" disabled>요청으로 이동</button>}
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 계약 예시이며 실제 업무 기록이 아닙니다. 근거는 바꾸지 않습니다.</div>}
  </>;
  const cond = <KvCond onSearch={refresh}>
    <KvCondRow columns={2}>
      <KvCondField label="요청번호">
        <select aria-label="요청번호" value={requestId ?? ''} disabled={!list.isSuccess} onChange={event => navigate(event.target.value ? `/cs/repeat-cases/${encodeURIComponent(event.target.value)}` : '/cs/repeat-cases')}>
          <option value="">{list.isSuccess && !candidates.length ? '저장된 반복 사례 후보가 없습니다' : '요청 선택'}</option>
          {requestId && list.isSuccess && !candidates.some(item => item.id === requestId) && <option value={requestId}>목록에 없는 요청</option>}
          {candidates.map(item => <option key={item.id} value={item.id}>{requestLabel(item)}</option>)}
        </select>
      </KvCondField>
    </KvCondRow>
  </KvCond>;
  return <main>
    {title}
    {!requestId && <><KvNext label="다음">요청번호를 골라 근거 문의를 비교하세요.</KvNext>{cond}</>}
    {list.isFetching && <Loading label="요청 목록을 불러오는 중" />}
    {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
    {!list.isFetching && list.isSuccess && !candidates.length && !requestId && <p className="state">저장된 반복 사례 후보가 없습니다.</p>}
    {requestId && <EvidenceBoard key={requestId} requestId={requestId} summary={candidates.find(item => item.id === requestId)} cond={cond} reloadRef={reloadBoard} />}
  </main>;
}

// cond는 조건 줄이다. 다음 할 일 줄이 고른 행을 따라가므로 조건 줄도 이 안에서 그 아래에 그린다.
function EvidenceBoard({ requestId, summary, cond, reloadRef }: { requestId: string; summary?: RequestSummary; cond: ReactNode; reloadRef: { current: () => void } }) {
  const client = useQueryClient();
  const mode = getApiMode();
  const detailKey = ['cs-repeat-cases', mode, requestId, 'detail'];
  const detail = useQuery({ queryKey: detailKey, queryFn: ({ signal }) => getImprovement(requestId, signal), retry: false, staleTime: 0 });
  const comparison = useQuery({ queryKey: ['cs-repeat-cases', mode, requestId, 'comparison'], queryFn: ({ signal }) => getCaseComparison(requestId, signal), retry: false, staleTime: 0 });
  const pool = useQuery({ queryKey: ['cs-repeat-cases', mode, 'candidates'], queryFn: ({ signal }) => listImprovementCandidates(signal), retry: false, staleTime: 0 });
  const [picked, setPicked] = useState<string | null>(null);
  const [removal, setRemoval] = useState<{ rowKey: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => { controller.current?.abort(); }, []);
  const reload = () => {
    void Promise.allSettled([detail.refetch(), comparison.refetch(), pool.refetch()]).then(([result]) => {
      if (result.status === 'fulfilled' && result.value.isSuccess) setUncertain(false);
    });
  };
  reloadRef.current = reload;

  if (detail.isFetching && !detail.isSuccess) return <>{cond}<Loading label="요청 근거를 불러오는 중" /></>;
  if (detail.isError && !detail.isFetching) return <>{cond}<ErrorState error={detail.error} onRetry={() => detail.refetch()} /></>;
  if (!detail.isSuccess) return cond;

  const d = detail.data.data;
  const request = d.request;
  const completed = request.status === 'COMPLETED';
  const compared = comparison.isSuccess ? comparison.data.data : null;
  // 같은 문의의 다른 해결 기록이나 다른 row_kind를 대신 비교하지 않는다.
  const caseOf = new Map((compared?.items ?? []).map(item => [`${item.row_kind ?? 'EVIDENCE'}:${item.ticket_id}:${item.case_id ?? ''}`, item]));
  const comparisonOf = (row: Row) => row.kind === 'OUT' ? undefined
    : caseOf.get(`${row.kind === 'IN' ? 'EVIDENCE' : 'CANDIDATE'}:${row.ticketId}:${row.caseId ?? ''}`);
  const active = d.evidence.filter(e => !e.removed_at);
  const activeTickets = new Set(active.map(e => e.ticket_id));
  const poolRefs = pool.isSuccess ? pool.data.data.items.filter(item => item.request_id === request.id).flatMap(item => item.evidence) : [];
  const seen = new Set<string>();
  const rows: Row[] = [
    ...active.map(e => ({ key: `in:${e.id}`, kind: 'IN' as const, ticketId: e.ticket_id, ticketNo: e.ticket_external_id, caseId: e.case_id, caseNo: e.case_external_id, customer: e.customer_external_id ?? null, subject: e.ticket_subject ?? null, receivedAt: e.ticket_received_at ?? null, evidenceId: e.id })),
    ...poolRefs.filter(ref => !activeTickets.has(ref.ticket_id) && !seen.has(ref.ticket_id) && seen.add(ref.ticket_id))
      .map(ref => ({ key: `cand:${ref.ticket_id}`, kind: 'CAND' as const, ticketId: ref.ticket_id, ticketNo: ref.ticket_external_id ?? null, caseId: ref.case_id, caseNo: ref.case_external_id ?? null, customer: ref.customer_id, subject: ref.ticket_subject ?? null, receivedAt: ref.ticket_received_at ?? null })),
    ...d.evidence.filter(e => e.removed_at).map(e => ({ key: `out:${e.id}`, kind: 'OUT' as const, ticketId: e.ticket_id, ticketNo: e.ticket_external_id, caseId: e.case_id, caseNo: e.case_external_id, customer: e.customer_external_id ?? null, subject: e.ticket_subject ?? null, receivedAt: e.ticket_received_at ?? null, evidenceId: e.id })),
  ];
  // 기준은 비교표가 있는 첫 포함 행으로 고정한다.
  const baseRow = rows.find(row => row.kind === 'IN' && comparisonOf(row));
  const candidateCount = rows.filter(row => row.kind === 'CAND').length;
  const selected = rows.find(row => row.key === picked) ?? rows.find(row => row !== baseRow) ?? rows[0];
  const reason = removal && removal.rowKey === selected?.key ? removal.text : '';
  const selectRow = (row: Row) => {
    if (row.key !== selected?.key) setRemoval(null);
    setPicked(row.key);
  };
  const position = selected ? rows.indexOf(selected) + 1 : 0;
  // 고장 요청의 근거는 해결 사례가 있어야 한다(계약 근거 검사). 사례가 없는 후보는 넣을 수 없다.
  const needsCase = request.request_type === 'FAULT';
  const evidenceLabel = (row: Row) => row.kind === 'CAND' && needsCase && !row.caseId ? '해결 기록 없음' : KIND_LABEL[row.kind];
  const caseCell = (row: Row) => row.caseNo ?? (row.caseId ? '-' : '해결 기록 없음');
  const rowLabel = (row: Row | undefined) => row ? row.caseNo ?? row.ticketNo ?? '-' : '-';
  // 비교표 머리와 다음 할 일 줄에 쓰는 번호. 서버가 번호를 주지 않은 후보 행은 행 순번으로 적는다.
  const rowNo = (row: Row) => row.ticketNo ?? row.caseNo ?? `${rows.indexOf(row) + 1}행`;
  const writable = mode === 'live' && !completed;
  const canAdd = writable && !busy && !uncertain && selected?.kind === 'CAND' && (!needsCase || !!selected.caseId);
  const canRemove = writable && !busy && !uncertain && selected?.kind === 'IN' && !!reason.trim();

  const write = async (send: (signal: AbortSignal) => Promise<ImprovementDetail>, done: string) => {
    if (controller.current) return;
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    setNotice(null);
    try {
      const result = await send(current.signal);
      if (current.signal.aborted) return;
      client.setQueryData(detailKey, result);
      setRemoval(null);
      setPicked(null);
      setNotice({ text: done, error: false });
      void comparison.refetch();
      void pool.refetch();
    } catch (error) {
      if (current.signal.aborted) return;
      if (error instanceof ApiError && error.status === 409) {
        setNotice({ text: error.code === 'CS_REQUEST_VERSION_CONFLICT' ? '다른 곳에서 요청이 먼저 바뀌었습니다. 최신 내용을 다시 읽었으니 확인한 뒤 다시 시도하세요.' : `${error.message} 최신 내용을 다시 읽었습니다.`, error: true });
        reload();
      } else if (error instanceof ApiError && error.status !== 408 && error.status < 500) {
        setNotice({ text: error.message, error: true });
      } else {
        setUncertain(true);
        setNotice({ text: '저장 결과를 확인할 수 없습니다. 서버에 반영됐을 수 있으니 다시 보내지 말고 최신 내용을 조회하세요.', error: true });
      }
    } finally {
      if (controller.current === current) controller.current = null;
      if (!current.signal.aborted) setBusy(false);
    }
  };
  const add = () => {
    if (!canAdd || !selected) return;
    void write(signal => addEvidence(request.id, { ticket_id: selected.ticketId, case_id: selected.caseId, version: request.version }, signal), '근거에 추가했습니다.');
  };
  const remove = () => {
    if (!canRemove || !selected?.evidenceId) return;
    void write(signal => removeEvidence(request.id, selected.evidenceId!, { reason: reason.trim(), version: request.version }, signal), '근거에서 뺐습니다.');
  };

  // 기록에는 사용자 ID만 있고 이름이 없다. ID는 보이지 않는다.
  const actor = (by: string | null) => by === null ? '시스템' : '담당자';
  const log: LogEntry[] = d.evidence.flatMap(e => [
    { key: `${e.id}:add`, at: e.added_at, kind: '추가' as const, caseNo: e.case_external_id, ticketNo: e.ticket_external_id, by: e.added_by, reason: null },
    ...(e.removed_at ? [{ key: `${e.id}:out`, at: e.removed_at, kind: '뺌' as const, caseNo: e.case_external_id, ticketNo: e.ticket_external_id, by: e.removed_by, reason: e.removal_reason }] : []),
  ]).sort((a, b) => Date.parse(a.at) - Date.parse(b.at));

  const rowColumns: KvColumn<Row>[] = [
    { key: 'pick', header: '선택', width: 32, className: 'c', cell: (row, index) => <input type="checkbox" aria-label={`${index + 1}행 선택`} checked={row === selected} onChange={() => selectRow(row)} /> },
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_row, index) => index + 1 },
    { key: 'ticket', header: '문의번호', width: 96, headClass: '', cell: row => row.ticketNo ? <Link to={`/cs/tickets/${encodeURIComponent(row.ticketId)}`}>{row.ticketNo}</Link> : '-' },
    { key: 'case', header: '해결 기록', width: 80, headClass: '', cell: caseCell },
    { key: 'customer', header: '고객', width: 90, headClass: '', cell: row => dash(row.customer) },
    { key: 'received', header: '접수일', width: 88, className: 'c', cell: row => row.receivedAt ? dayOf(row.receivedAt) : '-' },
    { key: 'title', header: '제목', className: 't', headClass: '', cell: row => unrecorded(row.subject) },
    { key: 'evidence', header: '근거', width: 78, className: 'c', cell: evidenceLabel },
  ];
  const logColumns: KvColumn<LogEntry>[] = [
    { key: 'at', header: '일시', width: 130, className: 'c', cell: entry => formatStamp(entry.at) },
    { key: 'kind', header: '구분', width: 56, className: 'c', cell: entry => entry.kind },
    { key: 'case', header: '해결 기록', width: 80, headClass: '', cell: entry => dash(entry.caseNo) },
    { key: 'ticket', header: '문의', width: 120, headClass: '', cell: entry => dash(entry.ticketNo) },
    { key: 'by', header: '처리자', width: 90, className: 'c', cell: entry => actor(entry.by) },
    { key: 'reason', header: '사유', className: 't', headClass: '', cell: entry => dash(entry.reason) },
  ];
  const baseCase = baseRow && comparisonOf(baseRow);
  const selectedCase = selected && comparisonOf(selected);
  const compareColumns: KvColumn<(typeof FIELDS)[number]>[] = [
    { key: 'label', header: '항목', width: 80, headClass: '', cell: field => field.label },
    { key: 'base', header: baseRow ? `기준 ${rowNo(baseRow)}` : '기준', className: 't', headClass: '', cell: field => baseCase ? unrecorded(field.value(baseCase)) : '미기록' },
    { key: 'picked', header: selected ? rowLabel(selected) : '선택 행', className: 't', headClass: '', cell: field => selectedCase ? unrecorded(field.value(selectedCase)) : '미기록' },
  ];
  const noComparison = !selected ? null
    : selected.kind === 'CAND' && !selectedCase ? '이 후보의 비교표가 없습니다. 원문 보기로 문의를 확인하세요.'
      : selected.kind === 'OUT' ? '근거에서 뺀 문의는 비교표가 없습니다. 원문 보기로 문의를 확인하세요.'
        : compared && !selectedCase ? '볼 수 있는 범위 밖의 문의라 비교할 수 없습니다.' : null;
  const comparisonState = comparison.isFetching ? '비교표 불러오는 중' : comparison.isError ? '비교표를 불러오지 못함'
    : !selected || noComparison ? '비교표 없음' : '비교표 준비됨';
  const decision = !selected ? '근거와 후보 없음'
    : completed ? `${rowNo(selected)} 비교만 가능, 완료된 요청이라 근거를 바꿀 수 없음`
      : mode !== 'live' ? `${rowNo(selected)} 비교만 가능, 모의 응답에서는 근거를 바꾸지 않음`
        : selected.kind === 'CAND' ? `${rowNo(selected)} ${needsCase && !selected.caseId ? '해결 기록이 없어 넣을 수 없음' : '넣을지 판단'}`
          : selected.kind === 'IN' ? `${rowNo(selected)} 뺄지 판단` : `${rowNo(selected)} 뺀 근거, 기록 확인`;
  const sep = <span className="sep">/</span>;

  return <>
    <KvNext label="다음">
      {decision}, {comparisonState} {sep} 요청 {REQUEST_STATUS_LABEL[request.status] ?? request.status}{summary && `, 근거 사례 ${summary.evidence_count}건`}
      {pool.isSuccess && <> {sep} 후보 {candidateCount}건</>}
    </KvNext>
    {cond}
    {notice && <div className="kv-sub" role={notice.error ? 'alert' : 'status'} style={{ margin: '6px 14px 0' }}>{notice.text}</div>}
    {uncertain && <div className="kv-actions" style={{ margin: '6px 14px 0' }}><button className="kb primary" onClick={reload}>최신 내용 조회</button></div>}
    <KvSplit columns="725fr 452fr">
      <div>
        <div className="kv-gridhead"><span>근거 문의 · {REQUEST_TYPE_LABEL[request.request_type]} · 판본 {request.version}</span><span>{pool.isSuccess ? `후보 문의 ${candidateCount}건` : pool.isFetching ? '후보 문의 불러오는 중' : '후보 문의 확인 불가'}</span></div>
        <KvGrid label="근거 문의" columns={rowColumns} rows={rows} rowKey={row => row.key} selectedKey={selected?.key} onRow={selectRow} fallback="근거 문의와 후보가 없습니다." />
        {pool.isError && (pool.error instanceof ApiError && pool.error.status === 403
          ? <div className="kv-sub" role="note" style={{ margin: '6px 0 0' }}>후보 문의는 C/S 담당자와 C/S 부서 관리자만 볼 수 있어 후보 행은 빼고 보여 줍니다.</div>
          : <div className="kv-sub" role="note" style={{ margin: '6px 0 0' }}>후보 문의를 불러오지 못했습니다. <button className="kb" onClick={() => pool.refetch()}>후보 다시 조회</button></div>)}
        {compared && compared.hidden_case_count > 0 && <div className="kv-sub" role="note" style={{ margin: '6px 0 0' }}>볼 수 있는 범위 밖의 근거 문의 {compared.hidden_case_count}건은 비교표에서 뺐습니다.</div>}
        <div className="kv-gridhead" style={{ marginTop: 12 }}><span>근거 변경 기록</span></div>
        <KvGrid label="근거 변경 기록" columns={logColumns} rows={log} rowKey={entry => entry.key} fallback="근거 변경 기록이 없습니다." />
      </div>
      <KvBox title={`비교, ${position}/${rows.length}행 ${rowLabel(selected)} (기준 ${baseRow ? baseRow.ticketNo ?? '-' : '없음'})`}>
        {comparison.isFetching && <Loading label="비교표를 불러오는 중" />}
        {!comparison.isFetching && comparison.isError && <ErrorState error={comparison.error} onRetry={() => comparison.refetch()} />}
        {!comparison.isFetching && compared && (!baseRow ? <p className="state">기준으로 삼을 포함 행이 없습니다.</p>
          : noComparison ? <p className="state">{noComparison}</p>
            : <KvGrid label="비교" columns={compareColumns} rows={FIELDS} rowKey={field => field.key} fallback="-" />)}
        <div className="kv-actions">
          {selected ? <Link className="kb" to={`/cs/tickets/${encodeURIComponent(selected.ticketId)}`}>원문 보기</Link> : <button className="kb" disabled>원문 보기</button>}
          <button className="kb primary" disabled={!canAdd} onClick={add}>{busy ? '처리 중' : '근거 추가'}</button>
        </div>
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--erp-border-soft)' }}>
          <h3 style={{ margin: '0 0 6px', fontSize: 11.5, fontWeight: 400, color: 'var(--erp-muted)' }}>근거에서 빼기 (포함된 건만)</h3>
          <label style={{ display: 'grid', gridTemplateColumns: '96px minmax(0, 1fr)', alignItems: 'center', gap: 8, margin: 0 }}>빼는 사유 (필수)
            <textarea aria-label="빼는 사유" required maxLength={2000} rows={2} style={{ width: '100%', boxSizing: 'border-box', margin: 0 }} disabled={!writable || busy || selected?.kind !== 'IN'} value={reason} onChange={event => selected && setRemoval({ rowKey: selected.key, text: event.target.value })} />
          </label>
          <div className="kv-actions" style={{ justifyContent: 'flex-end' }}>
            <button className="kb" disabled={!canRemove} onClick={remove}>근거에서 빼기</button>
          </div>
        </div>
      </KvBox>
    </KvSplit>
  </>;
}
