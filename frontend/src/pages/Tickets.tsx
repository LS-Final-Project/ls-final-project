import { useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ApiError, getApiMode } from '../api/client';
import type { TicketListResponse } from '../api/cs';
import { listImprovements } from '../api/cs-improvements';
import { ErrorState, Loading } from '../components/states';
import { useTicketPages } from '../ls-shell/use-tickets';
import { KvCond, KvCondField, KvCondRow, KvFoot, KvGrid, KvKey, KvTitle, downloadCsv, useHotkey, type KvColumn } from '../ls-shell/kv';
import { CHANNEL_LABEL, REQUEST_STATUS_LABEL, REQUEST_TYPE_LABEL, STATE_LABEL, dayOf, formatStamp, type RequestSummary, type TicketChannel } from '../ls-shell/cs-labels';

type Ticket = TicketListResponse['data']['items'][number];

const DAY = 86_400_000;
const STATUS_OPTIONS: [string, string][] = [
  ['ACTIVE', '진행 중 전체'], ['RECEIVED', '접수'], ['IN_REVIEW', '검토중'], ['WAITING_CUSTOMER', '고객 회신 대기'],
  ['ANSWERED', '답변 완료'], ['TRANSFERRED', '부서 검토 중'], ['CLOSED', '종결'], ['', '전체'],
];
const AGE_OPTIONS: [string, string][] = [['', '전체'], ['3', '3일 이상'], ['7', '7일 이상'], ['WAIT3', '고객 대기 3일 이상']];
const bar = <span className="sep">|</span>;

// 문의 조회 CS0101. 조회조건은 받아 온 목록 안에서 거른다(서버 필터가 아직 없다).
export default function Tickets() {
  const mode = getApiMode();
  const access = useBusinessAccess('cs');
  const [status, setStatus] = useState('ACTIVE');
  const [channel, setChannel] = useState('');
  const [product, setProduct] = useState('');
  const [age, setAge] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [search, setSearch] = useState('');
  const [newestFirst, setNewestFirst] = useState(true);
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());

  const tickets = useTicketPages(access.ready);
  const improvements = useQuery({ queryKey: ['cs-improvements-board', mode], queryFn: ({ signal }) => listImprovements(signal), enabled: access.ready && import.meta.env.DEV });
  // refetch()는 enabled를 무시하므로 개선요청은 개발 모드에서만 다시 부른다.
  // 조회 중에는 다시 부르지 않는다. 단추만 잠그면 다시 그려지기 전에 들어온 연타나 F8 키 반복이 요청을 겹친다.
  const refreshing = useRef(false);
  const refresh = () => {
    if (refreshing.current) return;
    refreshing.current = true;
    void Promise.allSettled([tickets.refetch(), import.meta.env.DEV ? improvements.refetch() : undefined]).finally(() => { refreshing.current = false; });
  };
  useHotkey('F8', refresh, access.ready);

  const all = useMemo(() => tickets.data?.pages.flatMap(page => page.data.items) ?? [], [tickets.data]);
  const now = Date.now();
  const ageOf = (item: Ticket) => Math.max(0, Math.floor((now - Date.parse(item.received_at)) / DAY));
  const needle = search.trim().toLocaleLowerCase();
  const rows = useMemo(() => {
    const kept = all.filter(item => {
      const days = Math.max(0, Math.floor((Date.now() - Date.parse(item.received_at)) / DAY));
      const day = dayOf(item.received_at);
      return (status === '' || (status === 'ACTIVE' ? item.workflow_state !== 'CLOSED' : item.workflow_state === status))
        && (!channel || item.channel === channel)
        && (!product || item.product_code === product)
        && (age === '' || (age === 'WAIT3' ? item.workflow_state === 'WAITING_CUSTOMER' && days >= 3 : days >= Number(age)))
        && (!from || day >= from) && (!to || day <= to)
        && (!needle || `${item.subject ?? ''} ${item.external_id ?? ''} ${item.product_code} ${item.id}`.toLocaleLowerCase().includes(needle));
    });
    return kept.sort((a, b) => (Date.parse(a.received_at) - Date.parse(b.received_at)) * (newestFirst ? -1 : 1));
  }, [all, status, channel, product, age, from, to, needle, newestFirst]);

  if (!access.ready) return <BusinessGate access={access} title="문의 조회" />;

  const products = Array.from(new Set([...all.map(item => item.product_code), ...(product ? [product] : [])])).sort();
  const received = all.filter(item => item.workflow_state === 'RECEIVED').length;
  const stale = all.filter(item => item.workflow_state !== 'CLOSED' && ageOf(item) >= 7).length;
  const today = dayOf(new Date().toISOString());
  const todayCount = all.filter(item => dayOf(item.received_at) === today).length;
  const number = (item: Ticket) => item.external_id ?? item.id.slice(0, 8);
  const pickedRows = rows.filter(item => picked.has(item.id));
  const allPicked = rows.length > 0 && rows.every(item => picked.has(item.id));
  const clear = () => { setStatus('ACTIVE'); setChannel(''); setProduct(''); setAge(''); setFrom(''); setTo(''); setSearch(''); };
  const togglePick = (id: string) => setPicked(prev => { const next = new Set(prev); if (!next.delete(id)) next.add(id); return next; });
  const exportCsv = () => {
    const target = pickedRows.length ? pickedRows : rows;
    downloadCsv('문의_조회', ['문의번호', '제목', '제품', '접수 경로', '상태', '경과일', '접수일시'],
      target.map(item => [number(item), item.subject ?? '', item.product_code, CHANNEL_LABEL[item.channel], STATE_LABEL[item.workflow_state], ageOf(item), formatStamp(item.received_at)]));
  };

  const columns: KvColumn<Ticket>[] = [
    { key: 'pick', header: <input type="checkbox" aria-label="모두 선택" checked={allPicked} onChange={() => setPicked(allPicked ? new Set() : new Set(rows.map(item => item.id)))} />, width: 30, className: 'c',
      cell: item => <input type="checkbox" aria-label={`${number(item)} 선택`} checked={picked.has(item.id)} onChange={() => togglePick(item.id)} /> },
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_item, index) => index + 1 },
    { key: 'id', header: '문의번호', width: 96, className: 'id', headClass: '', cell: item => number(item) },
    { key: 'subject', header: '제목', width: 220, className: 't', headClass: '', cell: item => <Link to={`/cs/tickets/${item.id}`} title={item.subject ?? undefined}>{item.subject || '(제목 없음)'}</Link> },
    { key: 'product', header: '제품', width: 150, cell: item => item.product_code },
    { key: 'state', header: '상태', width: 96, className: 'c', cell: item => STATE_LABEL[item.workflow_state] },
    { key: 'age', header: '경과일', width: 56, className: 'c', cell: item => ageOf(item) },
    { key: 'received', header: <button type="button" aria-label={newestFirst ? '접수일시 내림차순, 눌러서 오름차순' : '접수일시 오름차순, 눌러서 내림차순'} onClick={() => setNewestFirst(value => !value)} style={{ font: 'inherit', color: 'inherit', padding: 0, border: 0, background: 'none', cursor: 'pointer' }}>접수일시</button>,
      width: 130, className: 'd', headClass: 'sorted', sort: newestFirst ? 'descending' : 'ascending', cell: item => formatStamp(item.received_at) },
  ];

  const requests = (improvements.data?.data.items ?? []).filter(item => item.status !== 'COMPLETED' && item.status !== 'REJECTED').slice(0, 5);
  const requestColumns: KvColumn<RequestSummary>[] = [
    { key: 'pick', header: <input type="checkbox" aria-label="개선요청 모두 선택" disabled />, width: 30, className: 'c', cell: item => <input type="checkbox" aria-label={`요청 ${item.id.slice(0, 8)} 선택`} disabled /> },
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_item, index) => index + 1 },
    { key: 'id', header: '요청번호', width: 96, className: 'id', headClass: '', cell: item => <Link to={`/cs/improvements/${item.id}`}>{item.id.slice(0, 8)}</Link> },
    { key: 'type', header: '유형', width: 64, className: 'c', cell: item => REQUEST_TYPE_LABEL[item.request_type] },
    { key: 'title', header: '제목', className: 't', headClass: '', cell: item => item.problem_statement },
    { key: 'status', header: '상태', width: 110, className: 'c', cell: item => REQUEST_STATUS_LABEL[item.status] },
    { key: 'next', header: '다음 할 일', width: 160, cell: () => <span className="unc">API 없음</span> },
    { key: 'due', header: '회신 기한', width: 88, className: 'd', headClass: '', cell: () => <span className="unc">-</span> },
    { key: 'created', header: '작성일', width: 88, className: 'd', headClass: '', cell: item => dayOf(item.created_at) },
  ];

  return <main>
    <KvTitle path="C/S 관리" title="문의 조회" code="CS0101">
      <button className="kb primary" disabled={tickets.isFetching} onClick={refresh}>조회<KvKey>F8</KvKey></button>
      <button className="kb" disabled title="직접 등록 API가 아직 없습니다">신규 등록</button>
      <button className="kb" disabled={rows.length === 0} onClick={exportCsv} title={pickedRows.length ? `선택한 ${pickedRows.length}건만 내려받습니다` : '표시된 행 전체를 내려받습니다'}>엑셀{pickedRows.length > 0 && <small> {pickedRows.length}</small>}</button>
      <button className="kb" onClick={() => window.print()}>인쇄</button>
    </KvTitle>
    <div className="kv-next">
      <span className="k">다음 할 일</span>
      <span>다음 <b>접수 {received}건 검토 시작</b>, 7일 이상 미처리 <b>{stale}</b></span>
      {bar}
      <span><span className="m">받아 온 {all.length}건 기준, 오늘 접수 {todayCount}. 요약/분류는 아직 없음</span></span>
    </div>

    <KvCond onSearch={refresh}>
      <KvCondRow columns={3}>
        <KvCondField label="상태"><select aria-label="상태" value={status} onChange={event => setStatus(event.target.value)}>{STATUS_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></KvCondField>
        <KvCondField label="접수 경로"><select aria-label="접수 경로" value={channel} onChange={event => setChannel(event.target.value)}><option value="">전체</option>{(Object.keys(CHANNEL_LABEL) as TicketChannel[]).map(value => <option key={value} value={value}>{CHANNEL_LABEL[value]}</option>)}</select></KvCondField>
        <KvCondField label="제품"><select aria-label="제품" value={product} onChange={event => setProduct(event.target.value)}><option value="">전체</option>{products.map(value => <option key={value}>{value}</option>)}</select></KvCondField>
      </KvCondRow>
      <KvCondRow columns={3}>
        <KvCondField label="경과"><select aria-label="경과" value={age} onChange={event => setAge(event.target.value)}>{AGE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></KvCondField>
        <KvCondField label="접수일자"><input type="date" aria-label="접수일자 시작" value={from} onChange={event => setFrom(event.target.value)} /><em>~</em><input type="date" aria-label="접수일자 종료" value={to} onChange={event => setTo(event.target.value)} /></KvCondField>
        <KvCondField label="검색어"><input type="text" aria-label="검색어" placeholder="문의번호, 제목, 제품" autoComplete="off" value={search} onChange={event => setSearch(event.target.value)} /></KvCondField>
      </KvCondRow>
    </KvCond>
    <div className="kv-saved">
      <span>내 조건</span>
      <select aria-label="내 조건" disabled title="저장 조건 API가 아직 없습니다"><option>선택 안 함</option></select>
      <button type="button" className="kb" disabled title="저장 조건 API가 아직 없습니다">현재 조건 저장</button>
      <a href="#clear" onClick={event => { event.preventDefault(); clear(); }}>조건 지우기</a>
    </div>

    <div className="kv-gridhead"><span>조회 {all.length}건 중 {rows.length}건 표시. 조회조건은 받아 온 목록 안에서 거름, 경과일은 접수일에서 계산</span><span className="unc" style={{ marginLeft: 8 }}>서버 필터, 요약, 고객 열 아직 없음</span></div>
    {tickets.isPending && <Loading />}
    {tickets.isError && <ErrorState error={tickets.error} onRetry={() => void tickets.refetch()} />}
    {tickets.isSuccess && !tickets.isError && <>
      <KvGrid label="문의 목록" frozen sortable scroll columns={columns} rows={rows} rowKey={item => item.id}
        fallback={all.length ? '조건에 맞는 문의가 없습니다. 조건 지우기로 조건을 비울 수 있습니다.' : '문의가 없습니다.'} />
      <KvFoot count={`표시 ${rows.length}건 / 조회 ${all.length}건`}
        more={<button type="button" className="kb" disabled={!tickets.hasNextPage || tickets.isFetchingNextPage} onClick={() => void tickets.fetchNextPage()}>{tickets.hasNextPage ? '50건 더 보기' : '모두 불러옴'}</button>} />
    </>}

    <div className="kv-gridhead sub"><span>개선요청, 진행 중 {requests.length}건</span><Link className="right" to="/cs/improvements">개선요청 조회 (CS0301)</Link></div>
    <KvGrid label="개선요청 목록" columns={requestColumns} rows={requests} rowKey={item => item.id}
      fallback={!import.meta.env.DEV ? '운영 인증 중계와 권한 검증 뒤에 제공됩니다.' : improvements.isPending ? '불러오는 중' : improvements.isError ? (improvements.error instanceof ApiError && (improvements.error.status === 401 || improvements.error.status === 403) ? '운영 인증 중계와 권한 검증 뒤에 제공됩니다.' : '개선요청을 불러오지 못했습니다.') : '진행 중인 개선요청이 없습니다.'} />
  </main>;
}
