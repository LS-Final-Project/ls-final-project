import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listTicketsBy, type TicketListResponse } from '../api/cs';
import { listImprovements } from '../api/cs-improvements';
import { listCsAssignees, me } from '../api/platform';
import { KvGrid, type KvColumn } from '../ls-shell/kv';
import { REQUEST_STATUS_LABEL, STATE_LABEL, dayOf } from '../ls-shell/cs-labels';
import AutoAssignDialog from './AutoAssignDialog';
import BatchOwnerDialog from './BatchOwnerDialog';

type Ticket = TicketListResponse['data']['items'][number];
type Request = Awaited<ReturnType<typeof listImprovements>>['data']['items'][number];
const PAGE = 10;
// 종결 전 문의. 담당 변경은 종결 문의에 하지 않는다(문의 처리 화면과 같다).
const OPEN_STATES = 'RECEIVED,IN_REVIEW,ANSWERED,TRANSFERRED,WAITING_CUSTOMER';

/**
 * 운영 현황의 담당 배정(관리자). 관리자 메뉴는 통계/보고서뿐이라 배정 기능을 여기에 모았다.
 * 미배정 문의 자동 배정(규칙, 미리보기 뒤 적용), 진행 문의 담당 변경(선택 후 한 사람으로),
 * 진행 개선요청 담당은 요청 상세의 재배정으로 보낸다. 권한은 서버가 다시 확인한다(C/S 부서 관리자).
 */
export default function AdminAssign() {
  const mode = getApiMode();
  const identity = useQuery({ queryKey: ['cs-admin-assign', mode, 'me'], queryFn: me, retry: false, staleTime: 60_000 });
  const isManager = identity.data?.data?.roles?.includes('manager') === true && (identity.data?.data?.managed_departments ?? []).includes('GCS_L1');
  const tickets = useQuery({ queryKey: ['cs-admin-assign', mode, 'tickets'], queryFn: ({ signal }) => listTicketsBy({ state: OPEN_STATES, sort: 'received_at_asc', limit: '100' }, signal), enabled: isManager, retry: false, staleTime: 0 });
  const requests = useQuery({ queryKey: ['cs-admin-assign', mode, 'requests'], queryFn: ({ signal }) => listImprovements(signal), enabled: isManager, retry: false, staleTime: 0 });
  const assignees = useQuery({ queryKey: ['cs-assignees', mode], queryFn: ({ signal }) => listCsAssignees(signal), enabled: isManager, retry: false, staleTime: 0 });
  const [owner, setOwner] = useState('');
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [autoAssign, setAutoAssign] = useState(false);
  const [batch, setBatch] = useState(false);

  if (!isManager) return null;
  const people = assignees.data?.data.items ?? [];
  const nameOf = (id: string | null | undefined) => !id ? '미배정' : people.find(item => item.user_id === id)?.display_name ?? `사용자 ${id.slice(0, 8)}`;
  const all = tickets.data?.data.items ?? [];
  const rows = all.filter(item => owner === '' || (owner === 'none' ? !item.owner_user_id : item.owner_user_id === owner));
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const at = Math.min(page, pages - 1);
  const shown = rows.slice(at * PAGE, at * PAGE + PAGE);
  const number = (item: Ticket) => item.external_id ?? item.id.slice(0, 8);
  const toggle = (id: string) => setPicked(prev => { const next = new Set(prev); if (!next.delete(id)) next.add(id); return next; });
  const pickedRows = all.filter(item => picked.has(item.id));
  const unassigned = all.filter(item => !item.owner_user_id).length;
  const ticketColumns: KvColumn<Ticket>[] = [
    { key: 'pick', header: '', width: 34, className: 'c', cell: item => <input type="checkbox" aria-label={`${number(item)} 선택`} checked={picked.has(item.id)} onChange={() => toggle(item.id)} /> },
    { key: 'id', header: '문의번호', width: 110, headClass: '', cell: item => <Link to={`/cs/tickets/${item.id}`}>{number(item)}</Link> },
    { key: 'subject', header: '제목', headClass: '', cell: item => <span title={item.subject ? `원문: ${item.subject}` : undefined}>{item.subject_ko || item.subject || '(제목 없음)'}</span> },
    { key: 'state', header: '상태', width: 90, className: 'c', cell: item => STATE_LABEL[item.workflow_state] },
    { key: 'owner', header: '담당자', width: 200, headClass: '', cell: item => nameOf(item.owner_user_id) },
    { key: 'day', header: '접수일', width: 90, className: 'c', cell: item => dayOf(item.received_at) },
  ];
  const openRequests = (requests.data?.data.items ?? []).filter(item => item.request_type === 'FAULT' && item.status !== 'COMPLETED');
  const requestColumns: KvColumn<Request>[] = [
    { key: 'what', header: '요청', headClass: '', cell: item => `${item.product_code}${item.fault_id ? ` · ${item.fault_id}` : ''}` },
    { key: 'status', header: '상태', width: 100, className: 'c', cell: item => REQUEST_STATUS_LABEL[item.status] ?? item.status },
    { key: 'owner', header: 'C/S 담당', width: 200, headClass: '', cell: item => nameOf(item.cs_owner) },
    { key: 'go', header: '', width: 80, className: 'c', cell: item => <Link to={`/cs/improvements/${encodeURIComponent(item.id)}`}>재배정</Link> },
  ];

  return <section className="kv-box" style={{ margin: '12px 14px 0' }} aria-label="담당 배정">
    <h2>담당 배정<small>C/S 부서 관리자 · 진행 문의 {all.length}{tickets.data?.data.next_cursor ? '+' : ''}건, 미배정 {unassigned}건</small></h2>
    <div className="body">
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
        <button className="kb" onClick={() => setAutoAssign(true)} title="담당자 없는 접수 문의를 언어·제품군 규칙으로 배정합니다(미리보기 뒤 적용)">미배정 문의 자동 배정</button>
        <button className="kb" disabled={pickedRows.length === 0} onClick={() => setBatch(true)}>선택 담당 변경{pickedRows.length > 0 && ` (${pickedRows.length})`}</button>
        <label style={{ marginLeft: 'auto' }}>담당자 <select aria-label="담당자 조건" value={owner} onChange={event => { setOwner(event.target.value); setPage(0); }}>
          <option value="">전체</option><option value="none">미배정</option>
          {people.map(item => <option key={item.user_id} value={item.user_id}>{item.display_name}</option>)}
        </select></label>
      </div>
      {tickets.isError ? <div className="kv-sub">진행 문의를 불러오지 못했습니다.</div>
        : <KvGrid label="담당 배정 문의" columns={ticketColumns} rows={shown} rowKey={item => item.id} fallback={tickets.isPending ? '불러오는 중' : '조건에 맞는 진행 문의가 없습니다.'} />}
      {pages > 1 && <div style={{ display: 'flex', gap: 6, justifyContent: 'center', marginTop: 6 }}>
        <button className="kb" disabled={at === 0} onClick={() => setPage(at - 1)}>이전</button>
        <span className="kv-sub" style={{ margin: 0, alignSelf: 'center' }}>{at + 1} / {pages}</span>
        <button className="kb" disabled={at === pages - 1} onClick={() => setPage(at + 1)}>다음</button>
      </div>}
      <div className="kv-sub" style={{ margin: '12px 0 4px' }}>진행 개선요청 C/S 담당 · 재배정은 요청 상세에서 합니다</div>
      {requests.isError ? <div className="kv-sub">개선요청을 불러오지 못했습니다.</div>
        : <KvGrid label="개선요청 담당" columns={requestColumns} rows={openRequests} rowKey={item => item.id} fallback={requests.isPending ? '불러오는 중' : '진행 개선요청이 없습니다.'} />}
    </div>
    {autoAssign && <AutoAssignDialog onClose={() => setAutoAssign(false)} onDone={() => { void tickets.refetch(); }} />}
    {batch && <BatchOwnerDialog targets={pickedRows.map(item => ({ id: item.id, label: number(item) }))} onClose={() => setBatch(false)} onDone={() => { setPicked(new Set()); void tickets.refetch(); }} />}
  </section>;
}
