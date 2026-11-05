import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { getApiMode } from '../api/client';
import { listTicketsBy, type TicketListResponse } from '../api/cs';
import { ErrorState, Loading } from '../components/states';
import TicketCreateDialog from '../components/TicketCreateDialog';
import { useTicketPages } from '../ls-shell/use-tickets';
import { useHotkey } from '../ls-shell/kv';
import LogTable, { type LogColumn, type LogSort } from './LogTable';
import './ticket-detail-v3.css';
import './list-cond.css';
import './tickets-v3.css';
import { CHANNEL_LABEL, STATE_LABEL, dayOf, formatStamp, type TicketChannel } from '../ls-shell/cs-labels';

type Ticket = TicketListResponse['data']['items'][number];

const DAY = 86_400_000;
const STATUS_OPTIONS: [string, string][] = [
  ['ACTIVE', '진행 중 전체'], ['RECEIVED', '접수'], ['IN_REVIEW', '검토중'], ['WAITING_CUSTOMER', '고객 회신 대기'],
  ['ANSWERED', '답변 완료'], ['TRANSFERRED', '부서 검토 중'], ['CLOSED', '종결'], ['', '전체'],
];
const AGE_OPTIONS: [string, string][] = [['', '전체'], ['3', '3일 이상'], ['7', '7일 이상'], ['WAIT3', '고객 대기 3일 이상']];
// 한 쪽에 보이는 문의 수. 마지막 쪽에서 '다음'을 누르면 서버에서 다음 묶음을 받아 이어 간다.
const PAGE = 10;

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
  // 정렬: 경과일(기본, 오래된 문의 먼저) 또는 접수일시.
  const [sort, setSort] = useState<LogSort>({ key: 'age', dir: 'desc' });
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);

  const tickets = useTicketPages(access.ready);
  // 조회 중에는 다시 부르지 않는다. 단추만 잠그면 다시 그려지기 전에 들어온 연타나 F8 키 반복이 요청을 겹친다.
  const refreshing = useRef(false);
  const refresh = () => {
    if (refreshing.current) return;
    refreshing.current = true;
    void tickets.refetch().finally(() => { refreshing.current = false; });
  };
  useHotkey('F8', refresh, access.ready);

  // 검색어는 서버에서도 찾는다(문의번호·제목·고객). 아직 받아 오지 않은 쪽의 문의도 나오게.
  const [term, setTerm] = useState('');
  useEffect(() => { const timer = window.setTimeout(() => setTerm(search.trim()), 300); return () => window.clearTimeout(timer); }, [search]);
  const hits = useQuery({ queryKey: ['tickets-search', mode, term], queryFn: ({ signal }) => listTicketsBy({ q: term, limit: '100' }, signal), enabled: access.ready && term.length > 0, retry: false, staleTime: 30_000 });
  const hitIds = useMemo(() => new Set((hits.data?.data.items ?? []).map(item => item.id)), [hits.data]);
  const all = useMemo(() => {
    const loaded = tickets.data?.pages.flatMap(page => page.data.items) ?? [];
    const extra = (hits.data?.data.items ?? []).filter(item => !loaded.some(row => row.id === item.id));
    return [...loaded, ...extra];
  }, [tickets.data, hits.data]);
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
        && (!needle || hitIds.has(item.id) || `${item.subject_ko ?? ''} ${item.subject ?? ''} ${item.external_id ?? ''} ${item.product_code} ${item.id}`.toLocaleLowerCase().includes(needle));
    });
    // 경과일이 길수록 접수일시가 이르다. 경과일 내림차순 = 접수일시 오름차순.
    const oldestFirst = sort.key === 'age' ? sort.dir === 'desc' : sort.dir === 'asc';
    return kept.sort((a, b) => (Date.parse(a.received_at) - Date.parse(b.received_at)) * (oldestFirst ? 1 : -1));
  }, [all, hitIds, status, channel, product, age, from, to, needle, sort]);
  // 조건·정렬이 바뀌면 첫 쪽으로 간다.
  useEffect(() => { setPage(0); }, [status, channel, product, age, from, to, needle, sort]);

  if (!access.ready) return <BusinessGate access={access} title="문의 조회" />;

  const products = Array.from(new Set([...all.map(item => item.product_code), ...(product ? [product] : [])])).sort();
  const number = (item: Ticket) => item.external_id ?? item.id.slice(0, 8);
  const clear = () => { setStatus('ACTIVE'); setChannel(''); setProduct(''); setAge(''); setFrom(''); setTo(''); setSearch(''); };


  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const at = Math.min(page, pages - 1);
  const shown = rows.slice(at * PAGE, at * PAGE + PAGE);
  const last = at === pages - 1;
  const next = () => { if (!last) setPage(at + 1); else if (tickets.hasNextPage && !tickets.isFetchingNextPage) void tickets.fetchNextPage().then(() => setPage(at + 1)); };
  const columns: LogColumn<Ticket>[] = [
    // No: 지금 조건·정렬 기준의 순번(쪽을 넘겨도 이어진다).
    { key: 'no', header: 'No', width: 44, className: 'c no', cell: item => at * PAGE + shown.indexOf(item) + 1 },
    { key: 'id', header: '문의번호', width: 110, cell: item => number(item) },
    { key: 'subject', header: '제목', className: 't', cell: item => <Link className="link" to={`/cs/tickets/${item.id}`} title={item.subject ? `원문: ${item.subject}` : undefined}>{item.subject_ko || item.subject || '(제목 없음)'}</Link> },
    { key: 'state', header: '상태', width: 110, className: 'c', cell: item => <span className={item.workflow_state === 'CLOSED' ? 'chip st-closed' : 'chip st-open'}>{STATE_LABEL[item.workflow_state]}</span> },
    { key: 'product', header: '제품', width: 160, cell: item => item.product_code },
    { key: 'channel', header: '접수 경로', width: 100, className: 'c', cell: item => <span className={`chip ch-${item.channel.toLowerCase()}`}>{CHANNEL_LABEL[item.channel] ?? item.channel}</span> },
    // 정렬은 받은 목록 전체에 이미 적용했다(rows). sortValue는 머리 정렬 단추를 그리는 데만 쓴다.
    { key: 'age', header: '경과일', width: 76, className: 'c', sortValue: item => ageOf(item), cell: item => `${ageOf(item)}일` },
    { key: 'received', header: '접수일시', width: 140, className: 'c', sortValue: item => Date.parse(item.received_at), cell: item => formatStamp(item.received_at) },
  ];


  return <main className="td3 tk3">
    <div className="title">
      <span className="path">C/S 관리 ›</span><h1>문의 조회</h1><code className="kv-code">CS0101</code>
      <div className="btns">
        <button className="kb3" disabled={mode !== 'live'} title={mode !== 'live' ? '실제 API 모드에서만 등록할 수 있습니다' : undefined} onClick={() => setCreating(true)}>신규 등록</button>
        {/* KAN-214: 담당 없는 접수 문의를 규칙으로 배정한다. 미리보기 뒤 적용. */}
        <button className="kb3" onClick={() => window.print()}>인쇄</button>
        <span className="vsep" />
        <button className="kb3 pri" disabled={tickets.isFetching} onClick={refresh}>조회<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      <form className="cond lc" onSubmit={event => { event.preventDefault(); refresh(); }}>
        <label className="search">
          <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input type="text" aria-label="검색어" placeholder="문의번호, 제목, 고객, 제품으로 검색" autoComplete="off" value={search} onChange={event => setSearch(event.target.value)} />
        </label>
        <label><span className="k">상태</span><select aria-label="상태" value={status} onChange={event => setStatus(event.target.value)}>{STATUS_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><span className="k">접수 경로</span><select aria-label="접수 경로" value={channel} onChange={event => setChannel(event.target.value)}><option value="">전체</option>{(Object.keys(CHANNEL_LABEL) as TicketChannel[]).map(value => <option key={value} value={value}>{CHANNEL_LABEL[value]}</option>)}</select></label>
        <label><span className="k">제품</span><select aria-label="제품" value={product} onChange={event => setProduct(event.target.value)}><option value="">전체</option>{products.map(value => <option key={value}>{value}</option>)}</select></label>
        <label><span className="k">경과</span><select aria-label="경과" value={age} onChange={event => setAge(event.target.value)}>{AGE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <span className="field"><span className="k">접수일자</span><span className="range"><input type="date" aria-label="접수일자 시작" value={from} onChange={event => setFrom(event.target.value)} /><em>~</em><input type="date" aria-label="접수일자 종료" value={to} onChange={event => setTo(event.target.value)} /></span></span>
        <button type="button" className="kb3 sm clear" onClick={clear}><span aria-hidden="true">↺</span> 조건 지우기</button>
        {/* 입력 칸이 여럿이면 제출 단추가 있어야 Enter로 조회된다. 보이는 조회 단추는 제목 줄에 있다. */}
        <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
      </form>

      {tickets.isPending && <Loading />}
      {tickets.isError && <ErrorState error={tickets.error} onRetry={() => void tickets.refetch()} />}
      {tickets.isSuccess && !tickets.isError && <div className="pane">
        <div className="ph">문의 목록
          <div className="r">
            <span className="meta" title={tickets.hasNextPage ? '받아 온 문의 기준입니다. 마지막 쪽의 다음을 누르면 더 받아 옵니다.' : undefined}>총 {all.length}건 중 {rows.length}건</span>
          </div>
        </div>
        <LogTable label="문의 목록" columns={columns} rows={shown} rowKey={item => item.id}
          sort={sort} onSort={key => setSort(current => current.key === key ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' })}
          fallback={all.length ? '조건에 맞는 문의가 없습니다. 조건 지우기로 조건을 비울 수 있습니다.' : '문의가 없습니다.'} />
        <div className="foot">
          <nav className="pager" aria-label="문의 쪽">
            <button type="button" className="kb3 sm" disabled={at === 0} onClick={() => setPage(at - 1)}>이전</button>
            {Array.from({ length: pages }, (_, i) => i).map(i => <span key={i} className="pg">
              <button type="button" className={i === at ? 'kb3 sm pri' : 'kb3 sm'} aria-current={i === at ? 'page' : undefined} aria-label={`${i + 1}쪽`} onClick={() => setPage(i)}>{i + 1}</button></span>)}
            <button type="button" className="kb3 sm" disabled={last && (!tickets.hasNextPage || tickets.isFetchingNextPage)} title={last && tickets.hasNextPage ? '다음 문의를 더 받아 옵니다' : undefined} onClick={next}>{tickets.isFetchingNextPage ? '받는 중' : '다음'}</button>
          </nav>
        </div>
      </div>}
    </div>

    {creating && <TicketCreateDialog products={products} onClose={() => setCreating(false)} onCreated={() => { void tickets.refetch(); }} />}
  </main>;
}
