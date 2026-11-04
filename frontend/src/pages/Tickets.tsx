import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { getApiMode } from '../api/client';
import type { TicketListResponse } from '../api/cs';
import { ErrorState, Loading } from '../components/states';
import TicketCreateDialog from '../components/TicketCreateDialog';
import BatchOwnerDialog from '../components/BatchOwnerDialog';
import { me } from '../api/platform';
import { useTicketPages } from '../ls-shell/use-tickets';
import { KvCond, KvCondField, KvCondRow, KvFoot, KvGrid, KvKey, KvTitle, downloadCsv, useHotkey, type KvColumn } from '../ls-shell/kv';
import { CHANNEL_LABEL, STATE_LABEL, dayOf, formatStamp, type TicketChannel } from '../ls-shell/cs-labels';

type Ticket = TicketListResponse['data']['items'][number];

const DAY = 86_400_000;
const STATUS_OPTIONS: [string, string][] = [
  ['ACTIVE', '진행 중 전체'], ['RECEIVED', '접수'], ['IN_REVIEW', '검토중'], ['WAITING_CUSTOMER', '고객 회신 대기'],
  ['ANSWERED', '답변 완료'], ['TRANSFERRED', '부서 검토 중'], ['CLOSED', '종결'], ['', '전체'],
];
const AGE_OPTIONS: [string, string][] = [['', '전체'], ['3', '3일 이상'], ['7', '7일 이상'], ['WAIT3', '고객 대기 3일 이상']];
// 한 쪽에 보이는 문의 수. 마지막 쪽에서 '다음'을 누르면 서버에서 다음 묶음을 받아 이어 간다.
const PAGE = 20;

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
  const [sort, setSort] = useState<{ key: 'age' | 'received'; desc: boolean }>({ key: 'age', desc: true });
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [batchOwner, setBatchOwner] = useState(false);
  // 담당 변경은 C/S 관리자만 한다(서버도 확인). 업무 접근 확인(['me'])과 캐시를 섞지 않으려고 키를 따로 둔다.
  const identity = useQuery({ queryKey: ['tickets-identity', mode], queryFn: me, retry: false, enabled: access.ready && mode === 'live' && picked.size > 0 });
  // 서버는 manager 역할과 C/S 부서(GCS_L1) 관리를 함께 본다. 다른 부서 관리자에게는 열지 않는다.
  const isManager = identity.data?.data?.roles?.includes('manager') === true && (identity.data?.data?.managed_departments ?? []).includes('GCS_L1');

  const tickets = useTicketPages(access.ready);
  // 조회 중에는 다시 부르지 않는다. 단추만 잠그면 다시 그려지기 전에 들어온 연타나 F8 키 반복이 요청을 겹친다.
  const refreshing = useRef(false);
  const refresh = () => {
    if (refreshing.current) return;
    refreshing.current = true;
    void tickets.refetch().finally(() => { refreshing.current = false; });
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
    // 경과일이 길수록 접수일시가 이르다. 경과일 내림차순 = 접수일시 오름차순.
    const oldestFirst = sort.key === 'age' ? sort.desc : !sort.desc;
    return kept.sort((a, b) => (Date.parse(a.received_at) - Date.parse(b.received_at)) * (oldestFirst ? 1 : -1));
  }, [all, status, channel, product, age, from, to, needle, sort]);
  // 조건·정렬이 바뀌면 첫 쪽으로 간다.
  useEffect(() => { setPage(0); }, [status, channel, product, age, from, to, needle, sort]);

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

  const sortHead = (key: 'age' | 'received', label: string) => {
    const on = sort.key === key;
    return <button type="button" aria-label={`${label} ${on ? (sort.desc ? '내림차순, 눌러서 오름차순' : '오름차순, 눌러서 내림차순') : '정렬'}`}
      onClick={() => setSort(current => current.key === key ? { key, desc: !current.desc } : { key, desc: true })}
      style={{ font: 'inherit', color: 'inherit', padding: 0, border: 0, background: 'none', cursor: 'pointer' }}>{label}</button>;
  };
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const at = Math.min(page, pages - 1);
  const shown = rows.slice(at * PAGE, at * PAGE + PAGE);
  const last = at === pages - 1;
  const next = () => { if (!last) setPage(at + 1); else if (tickets.hasNextPage && !tickets.isFetchingNextPage) void tickets.fetchNextPage().then(() => setPage(at + 1)); };
  const columns: KvColumn<Ticket>[] = [
    { key: 'pick', header: <input type="checkbox" aria-label="모두 선택" checked={allPicked} onChange={() => setPicked(allPicked ? new Set() : new Set(rows.map(item => item.id)))} />, width: 30, className: 'c',
      cell: item => <input type="checkbox" aria-label={`${number(item)} 선택`} checked={picked.has(item.id)} onChange={() => togglePick(item.id)} /> },
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_item, index) => at * PAGE + index + 1 },
    { key: 'id', header: '문의번호', width: 96, className: 'id', headClass: '', cell: item => number(item) },
    { key: 'subject', header: '제목', width: 220, className: 't', headClass: '', cell: item => <Link to={`/cs/tickets/${item.id}`} title={item.subject ?? undefined}>{item.subject || '(제목 없음)'}</Link> },
    { key: 'product', header: '제품', width: 150, cell: item => item.product_code },
    { key: 'state', header: '상태', width: 96, className: 'c', cell: item => STATE_LABEL[item.workflow_state] },
    { key: 'age', header: sortHead('age', '경과일'), width: 64, className: 'c', headClass: sort.key === 'age' ? 'sorted' : '', sort: sort.key === 'age' ? (sort.desc ? 'descending' : 'ascending') : undefined, cell: item => ageOf(item) },
    { key: 'received', header: sortHead('received', '접수일시'), width: 130, className: 'd', headClass: sort.key === 'received' ? 'sorted' : '', sort: sort.key === 'received' ? (sort.desc ? 'descending' : 'ascending') : undefined, cell: item => formatStamp(item.received_at) },
  ];


  return <main>
    <KvTitle path="C/S 관리" title="문의 조회" code="CS0101">
      <button className="kb primary" disabled={tickets.isFetching} onClick={refresh}>조회<KvKey>F8</KvKey></button>
      <button className="kb" disabled={mode !== 'live'} title={mode !== 'live' ? '실제 API 모드에서만 등록할 수 있습니다' : undefined} onClick={() => setCreating(true)}>신규 등록</button>
      <button className="kb" disabled={rows.length === 0} onClick={exportCsv} title={pickedRows.length ? `선택한 ${pickedRows.length}건만 내려받습니다` : '표시된 행 전체를 내려받습니다'}>엑셀{pickedRows.length > 0 && <small> {pickedRows.length}</small>}</button>
      <button className="kb" onClick={() => window.print()}>인쇄</button>
    </KvTitle>

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

    {tickets.isPending && <Loading />}
    {tickets.isError && <ErrorState error={tickets.error} onRetry={() => void tickets.refetch()} />}
    {pickedRows.length > 0 && <div className="kv-gridhead sub" role="toolbar" aria-label="선택한 문의">
      <span><b>{pickedRows.length}</b>건 선택</span>
      <button type="button" className="kb" disabled={!isManager} title={isManager ? undefined : mode !== 'live' ? '실제 API 모드에서만 할 수 있습니다' : identity.isPending ? '세션을 확인하는 중입니다' : identity.isError ? '세션을 확인하지 못했습니다. 새로고침하세요' : 'C/S 부서 관리자만 담당을 바꿀 수 있습니다'} onClick={() => setBatchOwner(true)}>담당 변경</button>
      <button type="button" className="kb" aria-label="선택한 문의 엑셀" onClick={exportCsv}>엑셀</button>
      <button type="button" className="kb" onClick={() => setPicked(new Set())}>선택 해제</button>
    </div>}
    {tickets.isSuccess && !tickets.isError && <>
      <KvGrid label="문의 목록" frozen sortable scroll columns={columns} rows={shown} rowKey={item => item.id}
        fallback={all.length ? '조건에 맞는 문의가 없습니다. 조건 지우기로 조건을 비울 수 있습니다.' : '문의가 없습니다.'} />
      <KvFoot count={`표시 ${rows.length}건 / 조회 ${all.length}건`}
        more={<nav className="kv-pager" aria-label="문의 쪽">
          <button type="button" className="kb" disabled={at === 0} onClick={() => setPage(at - 1)}>이전</button>
          {Array.from({ length: pages }, (_, i) => i).filter(i => i === 0 || i === pages - 1 || Math.abs(i - at) <= 2).map((i, k, list) => <span key={i}>
            {k > 0 && i - list[k - 1] > 1 && <span className="gap">…</span>}
            <button type="button" className={i === at ? 'kb primary' : 'kb'} aria-current={i === at ? 'page' : undefined} aria-label={`${i + 1}쪽`} onClick={() => setPage(i)}>{i + 1}</button></span>)}
          <button type="button" className="kb" disabled={last && (!tickets.hasNextPage || tickets.isFetchingNextPage)} title={last && tickets.hasNextPage ? '다음 문의를 더 받아 옵니다' : undefined} onClick={next}>{tickets.isFetchingNextPage ? '받는 중' : '다음'}</button>
        </nav>} />
    </>}

    {batchOwner && <BatchOwnerDialog targets={pickedRows.map(item => ({ id: item.id, label: number(item) }))} onClose={() => setBatchOwner(false)} onDone={() => { void tickets.refetch(); }} />}
    {creating && <TicketCreateDialog products={products} onClose={() => setCreating(false)} onCreated={() => { void tickets.refetch(); }} />}
  </main>;
}
