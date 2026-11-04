import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getTicket, listTicketsBy, type TicketListResponse } from '../api/cs';
import { getAssignmentAgents } from '../api/cs-assignments';
import { listImprovements } from '../api/cs-improvements';
import { listCsAssignees, me } from '../api/platform';
import LogTable, { type LogColumn } from '../pages/LogTable';
import { CHANNEL_LABEL, REQUEST_STATUS_LABEL, STATE_LABEL, dayOf, languageName } from '../ls-shell/cs-labels';
import BatchOwnerDialog from './BatchOwnerDialog';
import Pager, { paged } from './Pager';

type Ticket = TicketListResponse['data']['items'][number];
type Request = Awaited<ReturnType<typeof listImprovements>>['data']['items'][number];
const PAGE = 10;
// 종결 전 문의. 담당 변경은 종결 문의에 하지 않는다(문의 처리 화면과 같다).
const OPEN_STATES = 'RECEIVED,IN_REVIEW,ANSWERED,TRANSFERRED,WAITING_CUSTOMER';

/**
 * 업무 배정 화면(CS0902)의 담당 배정(관리자). 관리자 메뉴는 통계/보고서뿐이라 배정 기능을 여기에 모았다.
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
  const [reqOwner, setReqOwner] = useState('');
  const [reqPage, setReqPage] = useState(0);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [batch, setBatch] = useState(false);
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'requests' ? 'requests' : 'tickets';
  const setTab = (next: 'tickets' | 'requests') => setParams(next === 'requests' ? { tab: 'requests' } : {}, { replace: true });
  const [assignOne, setAssignOne] = useState<Ticket | null>(null);
  const [reqPicked, setReqPicked] = useState<Set<string>>(new Set());
  const [reqBatch, setReqBatch] = useState<{ id: string; label: string }[] | null>(null);
  // 맞는 담당자 없는 문의: 접수 때 자동 배정 규칙(cs-assign-v1)이 담당자를 못 찾아 미배정으로 남은 문의.
  // 서버에 '맞는 담당자 없음' 기록이 없어서, 새로 접수된(과거 적재 synthetic 아닌) 미배정 접수 문의로 본다(미리보기용 기준).
  const recent = useQuery({ queryKey: ['cs-admin-assign', mode, 'unmatched'], queryFn: ({ signal }) => listTicketsBy({ state: 'RECEIVED', sort: 'received_at_desc', limit: '100' }, signal), enabled: isManager, retry: false, staleTime: 0 });
  const unmatched = (recent.data?.data.items ?? []).filter(item => !item.owner_user_id && item.data_origin !== 'synthetic');
  const unmatchedIds = unmatched.slice(0, 20).map(item => item.id);
  // 처음 열 때 다른 목록과 겹쳐 한 건이 실패할 수 있다. 빠진 건이 있으면 실패로 보고 두 번까지 다시 읽는다.
  const details = useQuery({ queryKey: ['cs-admin-assign', mode, 'unmatched-detail', unmatchedIds.join(',')], enabled: isManager && unmatchedIds.length > 0, retry: 2, retryDelay: 800, staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const out: Record<string, string> = {};
      // 다른 목록과 겹쳐 한 번 실패할 수 있어 한 번 더 읽는다. 그래도 못 읽으면 비워 둔다.
      const read = (id: string) => getTicket(id, signal).catch(() => getTicket(id, signal));
      const results = await Promise.allSettled(unmatchedIds.map(id => read(id)));
      results.forEach((result, index) => { if (result.status === 'fulfilled') out[unmatchedIds[index]] = result.value.data.body_language; });
      if (results.some(result => result.status === 'rejected')) throw new Error('UNMATCHED_DETAIL_PARTIAL');
      return out;
    } });
  const agents = useQuery({ queryKey: ['cs-assignment-agents', mode], queryFn: ({ signal }) => getAssignmentAgents(signal), enabled: isManager, retry: false, staleTime: 0 });
  // 조건이 바뀌거나 목록을 다시 읽으면 선택을 비운다. 화면에 안 보이는 문의가 담당 변경 대상에 남지 않게.
  useEffect(() => { setPicked(new Set()); }, [owner, tickets.dataUpdatedAt]);
  useEffect(() => { setReqPicked(new Set()); }, [reqOwner, requests.dataUpdatedAt]);

  if (!isManager) return null;
  const people = assignees.data?.data.items ?? [];
  const nameOf = (id: string | null | undefined) => !id ? '미배정' : people.find(item => item.user_id === id)?.display_name ?? `사용자 ${id.slice(0, 8)}`;
  const agentRows = Array.isArray(agents.data?.data?.items) ? agents.data.data.items.filter(item => item.active) : [];
  const langOf = (id: string) => details.data?.[id];
  const reasonOf = (item: Ticket) => {
    const lang = langOf(item.id);
    const base = lang ? lang.split('-')[0].toLowerCase() : '';
    const speaks = !!base && agentRows.some(agent => agent.languages.some(code => code.split('-')[0].toLowerCase() === base));
    const parts = [lang ? (speaks ? `${languageName(lang)} 가능자 있음` : `${languageName(lang)} 가능자 없음`) : '언어 미확인', `미등록 제품 ${item.product_code}`];
    return parts.join(' · ');
  };
  const all = tickets.data?.data.items ?? [];
  const more = !!tickets.data?.data.next_cursor;
  const rows = all.filter(item => owner === '' || (owner === 'none' ? !item.owner_user_id : item.owner_user_id === owner));
  const t = paged(rows, page, PAGE);
  const number = (item: Ticket) => item.external_id ?? item.id.slice(0, 8);
  const toggle = (id: string) => setPicked(prev => { const next = new Set(prev); if (!next.delete(id)) next.add(id); return next; });
  // 담당 변경 대상은 지금 조건에 보이는 문의 중 고른 것만.
  const pickedRows = rows.filter(item => picked.has(item.id));
  const pageIds = t.shown.map(item => item.id);
  const pageAll = pageIds.length > 0 && pageIds.every(id => picked.has(id));
  const togglePage = () => setPicked(prev => { const next = new Set(prev); pageIds.forEach(id => pageAll ? next.delete(id) : next.add(id)); return next; });
  const unassigned = all.filter(item => !item.owner_user_id).length;
  const unmatchedColumns: LogColumn<Ticket>[] = [
    { key: 'id', header: '문의번호', width: 110, cell: item => <Link className="link" to={`/cs/tickets/${item.id}`}>{number(item)}</Link> },
    { key: 'subject', header: '제목', className: 't', cell: item => <span title={item.subject ?? undefined}>{item.subject_ko || item.subject || '(제목 없음)'}</span> },
    { key: 'lang', header: '언어 · 경로', width: 170, cell: item => <span className="nowrap">{langOf(item.id) ? languageName(langOf(item.id)!) : details.isPending ? '…' : '미확인'}<span className="unc"> · {CHANNEL_LABEL[item.channel as keyof typeof CHANNEL_LABEL] ?? item.channel}</span></span> },
    { key: 'why', header: '배정 못 한 이유', width: 330, cell: item => <span className="why" title={reasonOf(item)}>{reasonOf(item)}</span> },
    { key: 'day', header: '접수일', width: 96, className: 'c', cell: item => dayOf(item.received_at) },
    { key: 'go', header: '', width: 104, className: 'c', cell: item => <button type="button" className="kb3 sm pri" onClick={() => setAssignOne(item)}>담당 지정</button> },
  ];
  const ticketColumns: LogColumn<Ticket>[] = [
    { key: 'pick', header: <input type="checkbox" aria-label="이 쪽 모두 선택" checked={pageAll} onChange={togglePage} />, width: 34, className: 'c', cell: item => <input type="checkbox" aria-label={`${number(item)} 선택`} checked={picked.has(item.id)} onChange={() => toggle(item.id)} /> },
    { key: 'id', header: '문의번호', width: 110, cell: item => <Link className="link" to={`/cs/tickets/${item.id}`}>{number(item)}</Link> },
    { key: 'subject', header: '제목', className: 't', cell: item => <span title={item.subject ? `원문: ${item.subject}` : undefined}>{item.subject_ko || item.subject || '(제목 없음)'}</span> },
    { key: 'state', header: '상태', width: 90, className: 'c', cell: item => <span className={item.workflow_state === 'RECEIVED' ? 'chip st-new' : 'chip st-open'}>{STATE_LABEL[item.workflow_state]}</span> },
    { key: 'owner', header: '담당자', width: 230, cell: item => item.owner_user_id ? nameOf(item.owner_user_id) : <span className="unc">미배정</span> },
    { key: 'day', header: '접수일', width: 96, className: 'c', cell: item => dayOf(item.received_at) },
  ];
  const openRequests = (requests.data?.data.items ?? []).filter(item => item.request_type === 'FAULT' && item.status !== 'COMPLETED');
  // 개선요청도 시스템이 초안을 만들 때 제품군 규칙으로 배정한다. 담당이 비어 있으면 맞는 담당자가 없었던 요청이다.
  const reqUnmatched = openRequests.filter(item => !item.cs_owner);
  const reqAssigned = openRequests.filter(item => !!item.cs_owner);
  const reqRows = reqAssigned.filter(item => reqOwner === '' || item.cs_owner === reqOwner);
  const r = paged(reqRows, reqPage, PAGE);
  const reqLabel = (item: Request) => `${item.product_code}${item.fault_id ? ` · ${item.fault_id}` : ''}`;
  const reqToggle = (id: string) => setReqPicked(prev => { const next = new Set(prev); if (!next.delete(id)) next.add(id); return next; });
  const reqPickedRows = reqRows.filter(item => reqPicked.has(item.id));
  const reqPageIds = r.shown.map(item => item.id);
  const reqPageAll = reqPageIds.length > 0 && reqPageIds.every(id => reqPicked.has(id));
  const reqTogglePage = () => setReqPicked(prev => { const next = new Set(prev); reqPageIds.forEach(id => reqPageAll ? next.delete(id) : next.add(id)); return next; });
  const reqUnmatchedColumns: LogColumn<Request>[] = [
    { key: 'what', header: '개선요청', className: 't', cell: item => <Link className="link" to={`/cs/improvements/${encodeURIComponent(item.id)}`}>{reqLabel(item)}</Link> },
    { key: 'status', header: '상태', width: 110, className: 'c', cell: item => <span className={`chip st-${item.status.toLowerCase()}`}>{REQUEST_STATUS_LABEL[item.status] ?? item.status}</span> },
    { key: 'why', header: '배정 못 한 이유', width: 330, cell: item => <span className="why">{item.product_code} 제품군을 맡은 활성 담당자 없음</span> },
    { key: 'day', header: '만든 날', width: 96, className: 'c', cell: item => dayOf(item.created_at) },
    { key: 'go', header: '', width: 104, className: 'c', cell: item => <button type="button" className="kb3 sm pri" onClick={() => setReqBatch([{ id: item.id, label: reqLabel(item) }])}>담당 지정</button> },
  ];
  const requestColumns: LogColumn<Request>[] = [
    { key: 'pick', header: <input type="checkbox" aria-label="이 쪽 개선요청 모두 선택" checked={reqPageAll} onChange={reqTogglePage} />, width: 34, className: 'c', cell: item => <input type="checkbox" aria-label={`${reqLabel(item)} 선택`} checked={reqPicked.has(item.id)} onChange={() => reqToggle(item.id)} /> },
    { key: 'what', header: '개선요청', className: 't', cell: item => <Link className="link" to={`/cs/improvements/${encodeURIComponent(item.id)}`}>{reqLabel(item)}</Link> },
    { key: 'status', header: '상태', width: 110, className: 'c', cell: item => <span className={`chip st-${item.status.toLowerCase()}`}>{REQUEST_STATUS_LABEL[item.status] ?? item.status}</span> },
    { key: 'owner', header: 'C/S 담당', width: 300, cell: item => nameOf(item.cs_owner) },
    { key: 'day', header: '만든 날', width: 96, className: 'c', cell: item => dayOf(item.created_at) },
  ];
  const ownerOptions = <>
    <option value="">담당자 전체</option><option value="none">미배정</option>
    {people.map(item => <option key={item.user_id} value={item.user_id}>{item.display_name}</option>)}
  </>;

  return <>
    <div className="kpis">
      <div className="kpi"><small>진행 문의</small><b>{tickets.isPending ? '…' : `${all.length}${more ? '+' : ''}`}</b><span>종결 전 · 오래된 순 100건까지</span></div>
      <div className={unmatched.length ? 'kpi hot' : 'kpi calm'}><small>맞는 담당자 없는 문의</small><b>{recent.isPending ? '…' : unmatched.length}</b><span>관리자가 담당을 지정해야 함</span></div>
      <div className="kpi ok"><small>진행 개선요청</small><b>{requests.isPending ? '…' : openRequests.length}</b><span>고장 개선요청 · 완료 전</span></div>
      <div className={reqUnmatched.length ? 'kpi hot' : 'kpi calm'}><small>맞는 담당자 없는 개선요청</small><b>{requests.isPending ? '…' : reqUnmatched.length}</b><span>관리자가 담당을 지정해야 함</span></div>
    </div>

    <nav className="as-tabs" role="tablist" aria-label="배정 대상">
      <button type="button" role="tab" aria-selected={tab === 'tickets'} onClick={() => setTab('tickets')}>문의<b className={unmatched.length ? 'hot' : undefined}>{unmatched.length}</b></button>
      <button type="button" role="tab" aria-selected={tab === 'requests'} onClick={() => setTab('requests')}>개선요청<b className={reqUnmatched.length ? 'hot' : undefined}>{reqUnmatched.length}</b></button>
      <span className="hint">숫자는 맞는 담당자가 없어 직접 지정할 건수</span>
    </nav>

    {tab === 'tickets' && <>
    <section className="pane unmatched" aria-label="맞는 담당자 없는 문의">
      <div className="ph">맞는 담당자 없는 문의
        <div className="r"><span className="meta">새 문의는 접수 때 규칙으로 자동 배정됩니다. 여기 남은 건만 직접 지정하세요</span></div>
      </div>
      {recent.isError ? <div className="none">문의를 불러오지 못했습니다.</div>
        : <LogTable label="맞는 담당자 없는 문의" columns={unmatchedColumns} rows={unmatched.slice(0, 20)} rowKey={item => item.id} fallback={recent.isPending ? '불러오는 중' : '맞는 담당자가 없는 문의가 없습니다. 모든 새 문의가 자동 배정됐습니다.'} />}
      <p className="fnote">자동 배정 규칙(cs-assign-v1): 언어·제품군 일치 → 언어만 일치 → 제품군 담당 + 영어 순으로 찾고, 모두 없으면 여기 남습니다. 과거 적재 자료는 자동 배정 대상이 아니라 제외합니다.</p>
    </section>

    <section className="pane" aria-label="담당 배정">
      <div className="ph">진행 문의 담당 변경
        <div className="r">
          <select aria-label="담당자 조건" value={owner} onChange={event => { setOwner(event.target.value); setPage(0); }}>{ownerOptions}</select>
          <button className="kb3 pri" disabled={pickedRows.length === 0} onClick={() => setBatch(true)}>선택 담당 변경{pickedRows.length > 0 && ` (${pickedRows.length})`}</button>
        </div>
      </div>
      {tickets.isError ? <div className="none">진행 문의를 불러오지 못했습니다.</div>
        : <LogTable label="담당 배정 문의" columns={ticketColumns} rows={t.shown} rowKey={item => item.id} fallback={tickets.isPending ? '불러오는 중' : '조건에 맞는 진행 문의가 없습니다.'} />}
      <Pager label="진행 문의 쪽" page={t.at} pages={t.pages} onPage={setPage} />
      <p className="fnote">{rows.length}건 중 {t.shown.length ? `${t.at * PAGE + 1}~${t.at * PAGE + t.shown.length}` : 0}번째 · 체크한 문의를 한 담당자에게 한꺼번에 바꿉니다. 조건을 바꾸면 선택이 비워집니다.</p>
    </section>
    </>}

    {tab === 'requests' && <>
    <section className="pane unmatched" aria-label="맞는 담당자 없는 개선요청">
      <div className="ph">맞는 담당자 없는 개선요청
        <div className="r"><span className="meta">시스템 초안은 만들어질 때 제품군 담당자에게 자동 배정됩니다. 여기 남은 건만 직접 지정하세요</span></div>
      </div>
      {requests.isError ? <div className="none">개선요청을 불러오지 못했습니다.</div>
        : <LogTable label="맞는 담당자 없는 개선요청" columns={reqUnmatchedColumns} rows={reqUnmatched.slice(0, 20)} rowKey={item => item.id} fallback={requests.isPending ? '불러오는 중' : '맞는 담당자가 없는 개선요청이 없습니다. 모든 초안이 자동 배정됐습니다.'} />}
    </section>

    <section className="pane" aria-label="개선요청 담당">
      <div className="ph">진행 개선요청 담당 변경
        <div className="r">
          <select aria-label="개선요청 담당자 조건" value={reqOwner} onChange={event => { setReqOwner(event.target.value); setReqPage(0); }}>
            <option value="">담당자 전체</option>{people.map(item => <option key={item.user_id} value={item.user_id}>{item.display_name}</option>)}
          </select>
          <button className="kb3 pri" disabled={reqPickedRows.length === 0} onClick={() => setReqBatch(reqPickedRows.map(item => ({ id: item.id, label: reqLabel(item) })))}>선택 담당 변경{reqPickedRows.length > 0 && ` (${reqPickedRows.length})`}</button>
        </div>
      </div>
      {requests.isError ? <div className="none">개선요청을 불러오지 못했습니다.</div>
        : <LogTable label="개선요청 담당" columns={requestColumns} rows={r.shown} rowKey={item => item.id} fallback={requests.isPending ? '불러오는 중' : '조건에 맞는 진행 개선요청이 없습니다.'} />}
      <Pager label="개선요청 쪽" page={r.at} pages={r.pages} onPage={setReqPage} />
      <p className="fnote">{reqRows.length}건 중 {r.shown.length ? `${r.at * PAGE + 1}~${r.at * PAGE + r.shown.length}` : 0}번째 · 체크한 개선요청의 C/S 담당을 한 사람으로 한꺼번에 바꿉니다.</p>
    </section>
    </>}

    {reqBatch && <BatchOwnerDialog kind="request" targets={reqBatch} onClose={() => setReqBatch(null)} onDone={() => { setReqPicked(new Set()); void requests.refetch(); }} />}
    {assignOne && <BatchOwnerDialog targets={[{ id: assignOne.id, label: number(assignOne) }]} onClose={() => setAssignOne(null)} onDone={() => { void recent.refetch(); void tickets.refetch(); }} />}
    {batch && <BatchOwnerDialog targets={pickedRows.map(item => ({ id: item.id, label: number(item) }))} onClose={() => setBatch(false)} onDone={() => { setPicked(new Set()); void tickets.refetch(); }} />}
  </>;
}
