import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getCaseComparison, listRepeatCandidates, type CaseComparison } from '../api/cs-repeat-cases';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvGrid, KvKey, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { DEPT_LABEL, REQUEST_STATUS_LABEL, REQUEST_TYPE_LABEL, formatStamp, type RequestSummary } from '../ls-shell/cs-labels';

type CaseRow = CaseComparison['data']['items'][number];

const DOC_SIGNAL_LABEL: Record<NonNullable<RequestSummary['doc_signal']>, string> = {
  NOT_FOUND: '자료에 없음',
  VALUE_CONFLICT: '자료 값이 서로 다름',
  BROKEN_LINK: '자료 링크 끊김',
  REPEATED_SPEC: '같은 사양 반복 문의',
};
const dash = (value: string | null | undefined) => value ?? '-';

// 반복 사례 모음 CS0201. 저장된 시스템 후보와 그 근거 문의의 해결 기록을 나란히 본다. 건수는 서버가 준 값만 쓴다.
export default function CsRepeatCases() {
  const { requestId } = useParams();
  const mode = getApiMode();
  const access = useBusinessAccess('cs');
  const list = useQuery({ queryKey: ['cs-repeat-cases', mode, 'list'], queryFn: ({ signal }) => listRepeatCandidates(signal), enabled: access.ready, retry: false, staleTime: 0 });
  const comparison = useQuery({ queryKey: ['cs-repeat-cases', mode, requestId], queryFn: ({ signal }) => getCaseComparison(requestId!, signal), enabled: access.ready && !!requestId, retry: false, staleTime: 0 });
  const busy = list.isFetching || comparison.isFetching;
  const refresh = () => {
    if (busy) return;
    void list.refetch();
    if (requestId) void comparison.refetch();
  };
  useHotkey('F8', refresh, access.ready);
  if (!access.ready) return <BusinessGate access={access} title="반복 사례 모음" />;

  const candidates = list.isSuccess && !list.isFetching ? list.data.data.items : null;
  const picked = candidates?.find(item => item.id === requestId);
  const table = comparison.isSuccess && !comparison.isFetching ? comparison.data.data : null;
  const listColumns: KvColumn<RequestSummary>[] = [
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_item, index) => index + 1 },
    { key: 'title', header: '반복 문제', className: 't', headClass: '', cell: item => <Link to={`/cs/repeat-cases/${encodeURIComponent(item.id)}`} aria-current={item.id === requestId ? 'true' : undefined}>{item.problem_statement}</Link> },
    { key: 'type', header: '유형', width: 80, className: 'c', cell: item => REQUEST_TYPE_LABEL[item.request_type] },
    { key: 'product', header: '제품', width: 140, headClass: '', cell: item => item.product_code },
    { key: 'signal', header: '고장 / 자료 신호', width: 150, headClass: '', cell: item => item.fault_id ?? (item.doc_signal ? DOC_SIGNAL_LABEL[item.doc_signal] : '-') },
    { key: 'status', header: '상태', width: 110, className: 'c', cell: item => REQUEST_STATUS_LABEL[item.status] ?? item.status },
    { key: 'evidence', header: '근거', width: 64, className: 'c', cell: item => `${item.evidence_count}건` },
    { key: 'owner', header: '담당', width: 72, className: 'c', cell: item => item.cs_owner ? '배정됨' : '미배정' },
    { key: 'saved', header: '저장 시각', width: 130, className: 'c', cell: item => formatStamp(item.created_at) },
    { key: 'go', header: '이동', width: 110, className: 'c', cell: item => <Link to={`/cs/improvements/${encodeURIComponent(item.id)}`}>개선요청 상세</Link> },
  ];
  const caseColumns: KvColumn<CaseRow>[] = [
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_row, index) => index + 1 },
    { key: 'ticket', header: '문의', width: 130, headClass: '', cell: row => <Link to={`/cs/tickets/${encodeURIComponent(row.ticket_id)}`}>{row.ticket_external_id ?? '번호 없는 문의'}</Link> },
    { key: 'customer', header: '고객', width: 110, headClass: '', cell: row => row.customer_external_id },
    { key: 'product', header: '제품', width: 120, headClass: '', cell: row => row.product_code },
    { key: 'case', header: '해결 사례', width: 110, headClass: '', cell: row => row.case_external_id ?? (row.case_id ? '번호 없음' : '해결 기록 없음') },
    { key: 'symptom', header: '증상', className: 't', headClass: '', cell: row => dash(row.symptom) },
    { key: 'cause_type', header: '원인 유형', width: 100, headClass: '', cell: row => dash(row.root_cause_type) },
    { key: 'cause', header: '원인', className: 't', headClass: '', cell: row => dash(row.root_cause) },
    { key: 'action', header: '조치', className: 't', headClass: '', cell: row => dash(row.action_taken) },
    { key: 'dept', header: '해결 부서', width: 130, headClass: '', cell: row => row.resolved_by_dept ? DEPT_LABEL[row.resolved_by_dept] ?? row.resolved_by_dept : '-' },
    { key: 'known', header: '기록 시각', width: 130, className: 'c', cell: row => row.known_at ? formatStamp(row.known_at) : '-' },
  ];
  return <main>
    <KvTitle path="C/S 관리" title="반복 사례 모음" code="CS0201">
      <button className="kb primary" disabled={busy} onClick={refresh}>최신 후보 조회<KvKey>F8</KvKey></button>
      <Link className="kb" to="/cs/improvements">개선요청 목록</Link>
      {requestId && <Link className="kb" to="/cs/repeat-cases">비교표 닫기</Link>}
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 계약 예시이며 실제 업무 기록이 아닙니다.</div>}
    <div className="kv-sub" style={{ margin: '6px 14px 0' }}>같은 문제가 여러 고객에게 반복되어 저장된 후보입니다. 후보를 고르면 근거 문의와 해결 기록을 비교합니다. 이 화면에서는 요청을 고치거나 보내지 않습니다.</div>
    {list.isFetching && <Loading label="후보 목록을 불러오는 중" />}
    {!list.isFetching && list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
    {candidates && <>
      <div className="kv-gridhead"><span>저장된 반복 사례 후보 (최신순, 최대 200건)</span></div>
      <KvGrid label="반복 사례 후보 목록" columns={listColumns} rows={candidates} rowKey={item => item.id} selectedKey={requestId} fallback="저장된 반복 사례 후보가 없습니다." />
    </>}
    {requestId && <>
      <div className="kv-gridhead"><span>사례 비교표{picked ? ` — ${picked.problem_statement}` : ''}</span><Link to={`/cs/improvements/${encodeURIComponent(requestId)}`}>개선요청 상세</Link></div>
      {comparison.isFetching && <Loading label="비교표를 불러오는 중" />}
      {!comparison.isFetching && comparison.isError && <ErrorState error={comparison.error} onRetry={() => comparison.refetch()} />}
      {table && <>
        <KvGrid label="사례 비교표" columns={caseColumns} rows={table.items} rowKey={row => row.ticket_id} fallback="비교할 근거 문의가 없습니다." />
        {table.hidden_case_count > 0 && <div className="kv-sub" role="note" style={{ margin: '6px 14px 0' }}>볼 수 있는 범위 밖의 근거 문의 {table.hidden_case_count}건은 표에서 뺐습니다.</div>}
      </>}
    </>}
  </main>;
}
