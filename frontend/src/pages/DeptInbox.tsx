import { useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listDeptRequests, type DeptCode, type TicketItem, type TransferStatus } from '../api/cs';
import { me } from '../api/platform';
import { ErrorState, Loading } from '../components/states';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { downloadCsv, useHotkey } from '../ls-shell/kv';
import LogTable, { type LogColumn, type LogSort } from './LogTable';
import './ticket-detail-v3.css';
import './list-cond.css';
import './tickets-v3.css';
import { DEPT_CODES, DEPT_LABEL, dayOf, formatStamp } from '../ls-shell/cs-labels';

const STATUS_LABEL: Record<TransferStatus, string> = { OPEN: '진행', RESULT_SUBMITTED: '답변 보냄', RETURNED_BY_DEPT: '담당 아님 반송', RECALLED: '요청 취소' };
const DAY = 24 * 60 * 60 * 1000;
// 상태 칩 색: 답할 차례는 노랑, 끝난 것은 초록, 반송·취소는 회색.
const STATUS_TONE: Record<TransferStatus, string> = { OPEN: 'st-open', RESULT_SUBMITTED: 'st-closed', RETURNED_BY_DEPT: 'st-muted', RECALLED: 'st-muted' };
// 한 쪽에 보이는 요청 수(문의 조회와 같다).
const PAGE = 10;
type Transfer = NonNullable<TicketItem['transfer']>;
type Row = TicketItem & { transfer: Transfer };
const isDept = (value: string | null | undefined): value is DeptCode => !!value && DEPT_CODES.includes(value);

// 문의 검토 CS0202(부서 창). 우리 부서로 이관된 문의를 문의 목록 API의 부서 필터(KAN-202)로 읽어 보인다.
// 부서 필터를 주면 항목의 transfer에 고객 ID와 요청 사유가 함께 온다. 개선요청 회신 표는 부서별 조회가 없어 넣지 않는다.
export default function DeptInbox() {
  const access = useBusinessAccess('cs');
  if (!access.ready) return <BusinessGate access={access} title="문의 검토" />;
  return <InboxView />;
}

function InboxView() {
  const mode = getApiMode();
  const identity = useQuery({ queryKey: ['dept-inbox-identity', mode], queryFn: me, retry: false });
  const own = identity.data?.data.dept;
  const [picked, setPicked] = useState<string>('');
  const dept = isDept(own) ? own : isDept(picked) ? picked : undefined;
  const [status, setStatus] = useState<TransferStatus | ''>('OPEN');
  const [round, setRound] = useState<'' | '1' | '2+'>('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<LogSort>({ key: 'at', dir: 'desc' });
  const [page, setPage] = useState(0);

  const list = useInfiniteQuery({
    queryKey: ['dept-requests', mode, dept, status],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => listDeptRequests(dept!, status, pageParam, signal),
    getNextPageParam: last => last.data.next_cursor ?? undefined,
    enabled: !!dept, retry: false, staleTime: 0,
  });
  const fetchAgain = () => { if (!list.isFetching) void list.refetch(); };
  useHotkey('F8', fetchAgain, !!dept);

  const all = useMemo(() => (list.data?.pages.flatMap(page => page.data.items) ?? []).filter((item): item is Row => item.transfer !== null), [list.data]);
  const now = Date.now();
  const elapsed = (row: Row) => Math.max(0, Math.floor((now - Date.parse(row.transfer.transferred_at)) / DAY));
  const needle = search.trim().toLocaleLowerCase();
  const rows = all
    .filter(row => (round === '' || (round === '1' ? row.transfer.round === 1 : row.transfer.round >= 2))
      && (!from || dayOf(row.transfer.transferred_at) >= from) && (!to || dayOf(row.transfer.transferred_at) <= to)
      && (!needle || `${row.subject_ko ?? ''} ${row.subject ?? ''} ${row.external_id ?? ''} ${row.transfer.customer_external_id} ${row.transfer.reason}`.toLocaleLowerCase().includes(needle)))
    .sort((a, b) => {
      const sign = sort.dir === 'asc' ? 1 : -1;
      // 경과일이 길수록 요청일시가 이르다. 두 머리 모두 요청일시로 줄 세운다.
      const older = sort.key === 'age' ? -sign : sign;
      return a.transfer.transferred_at.localeCompare(b.transfer.transferred_at) * older;
    });
  const invalid = !!from && !!to && from > to;
  const shown = invalid ? [] : rows;
  const open = shown.filter(row => row.transfer.status === 'OPEN');
  const old = open.filter(row => elapsed(row) >= 7).length;
  useEffect(() => { setPage(0); }, [dept, status, round, from, to, search, sort]);

  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const at = Math.min(page, pages - 1);
  const pageRows = shown.slice(at * PAGE, at * PAGE + PAGE);
  const last = at === pages - 1;
  const next = () => { if (!last) setPage(at + 1); else if (list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage().then(() => setPage(at + 1)); };
  const clear = () => { setStatus('OPEN'); setRound(''); setFrom(''); setTo(''); setSearch(''); };
  const columns: LogColumn<Row>[] = [
    { key: 'id', header: '문의번호', width: 110, cell: row => row.external_id ?? row.id.slice(0, 8) },
    { key: 'subject', header: '제목', className: 't', cell: row => <Link className="link" to={`/dept/result/${row.id}`} title={row.subject ? `원문: ${row.subject}` : undefined}>{row.subject_ko || row.subject || '(제목 없음)'}</Link> },
    { key: 'customer', header: '고객', width: 100, cell: row => row.transfer.customer_external_id },
    { key: 'reason', header: '요청 사유', width: 240, className: 'r', cell: row => <span title={row.transfer.reason}>{row.transfer.reason}</span> },
    { key: 'round', header: '차수', width: 56, className: 'c', cell: row => `${row.transfer.round}차` },
    { key: 'status', header: '상태', width: 110, className: 'c', cell: row => <span className={`chip ${STATUS_TONE[row.transfer.status]}`}>{STATUS_LABEL[row.transfer.status]}</span> },
    { key: 'age', header: '경과일', width: 76, className: 'c', sortValue: row => elapsed(row), cell: row => row.transfer.status === 'OPEN' ? <span className={elapsed(row) >= 7 ? 'late' : undefined}>{elapsed(row)}일</span> : '-' },
    { key: 'at', header: '요청일시', width: 140, className: 'c', sortValue: row => Date.parse(row.transfer.transferred_at), cell: row => formatStamp(row.transfer.transferred_at) },
  ];
  const exportCsv = () => downloadCsv('문의_검토', ['문의번호', '제목(한국어)', '원문 제목', '고객', '차수', '요청일시', '요청 사유', '상태', '경과일'],
    shown.map(row => [row.external_id ?? row.id, row.subject_ko ?? '', row.subject ?? '', row.transfer.customer_external_id, row.transfer.round, formatStamp(row.transfer.transferred_at), row.transfer.reason, STATUS_LABEL[row.transfer.status], row.transfer.status === 'OPEN' ? elapsed(row) : '']));

  return <main className="td3 tk3 di3">
    <div className="title">
      <span className="path">부서 검토 ›</span><h1>문의 검토</h1><code className="kv-code">CS0202</code>
      <div className="btns">
        <button className="kb3" disabled={shown.length === 0} onClick={exportCsv}>엑셀</button>
        <button className="kb3" onClick={() => window.print()}>인쇄</button>
        <span className="vsep" />
        <button className="kb3 pri" disabled={!dept || list.isFetching} onClick={fetchAgain}>조회<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      {mode === 'mock' && <div className="tier-note" role="status">모의 응답 모드 — 부서 목록 예시가 없어 비어 있습니다. 실제 API 모드에서 확인하세요.</div>}
      {identity.isPending && <Loading label="소속 부서 확인 중" />}
      {identity.isError && <ErrorState error={identity.error} onRetry={() => { void identity.refetch(); }} />}
      {identity.isSuccess && identity.data === null && <div className="stale">로그인이 필요합니다. <Link to="/account">계정 확인</Link></div>}
      {identity.isSuccess && identity.data && <>
        {dept && <div className="di-sum" role="status">
          <span className="al"><i aria-hidden="true" />답변할 요청 <b>{open.length}</b>건, 7일 이상 <b>{old}</b></span>
          <small>{DEPT_LABEL[dept]} · {formatStamp(new Date().toISOString())} 기준</small>
        </div>}
        <form className="cond lc" onSubmit={event => { event.preventDefault(); fetchAgain(); }}>
          <label className="search">
            <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
            <input type="text" aria-label="검색어" placeholder="문의번호, 제목, 고객, 요청 사유로 검색" autoComplete="off" value={search} onChange={event => setSearch(event.target.value)} />
          </label>
          <label><span className="k">부서</span><select aria-label="부서" value={dept ?? ''} disabled={isDept(own)} onChange={event => setPicked(event.target.value)}>
            {!dept && <option value="">부서 선택</option>}
            {DEPT_CODES.map(code => <option key={code} value={code}>{DEPT_LABEL[code]}</option>)}
          </select></label>
          <label><span className="k">상태</span><select aria-label="상태" value={status} onChange={event => setStatus(event.target.value as TransferStatus | '')}>
            {(Object.keys(STATUS_LABEL) as TransferStatus[]).map(key => <option key={key} value={key}>{STATUS_LABEL[key]}</option>)}
            <option value="">전체</option>
          </select></label>
          <label><span className="k">차수</span><select aria-label="차수" value={round} onChange={event => setRound(event.target.value as '' | '1' | '2+')}>
            <option value="">전체</option><option value="1">1차</option><option value="2+">2차 이상</option>
          </select></label>
          <span className="field"><span className="k">요청일자</span><span className="range"><input type="date" aria-label="시작일" value={from} onChange={event => setFrom(event.target.value)} /><em>~</em><input type="date" aria-label="종료일" value={to} onChange={event => setTo(event.target.value)} /></span></span>
          <button type="button" className="kb3 sm clear" onClick={clear}><span aria-hidden="true">↺</span> 조건 지우기</button>
          <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
        </form>
        {!isDept(own) && !dept && <div className="stale">소속 부서가 없는 계정입니다. 볼 부서를 고르세요. 볼 수 있는 범위는 서버가 정합니다.</div>}
        {invalid && <div className="stale" role="alert">종료일은 시작일보다 빠를 수 없습니다.</div>}
        {dept && list.isPending && <Loading />}
        {dept && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
        {dept && list.isSuccess && <div className="pane">
          <div className="ph">문의 검토 목록<div className="r"><span className="meta" title="문의별 최신 검토 요청입니다">총 {all.length}{list.hasNextPage ? '+' : ''}건 중 {shown.length}건</span></div></div>
          <LogTable label="문의 검토" columns={columns} rows={pageRows} rowKey={row => row.id}
            sort={sort} onSort={key => setSort(current => current.key === key ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' })}
            fallback="조건에 맞는 요청이 없습니다." />
          <div className="foot">
            <nav className="pager" aria-label="문의 검토 쪽">
              <button type="button" className="kb3 sm" disabled={at === 0} onClick={() => setPage(at - 1)}>이전</button>
              {Array.from({ length: pages }, (_, i) => i).filter(i => i === 0 || i === pages - 1 || Math.abs(i - at) <= 2).map((i, k, list2) => <span key={i} className="pg">
                {k > 0 && i - list2[k - 1] > 1 && <span className="gap">…</span>}
                <button type="button" className={i === at ? 'kb3 sm pri' : 'kb3 sm'} aria-current={i === at ? 'page' : undefined} aria-label={`${i + 1}쪽`} onClick={() => setPage(i)}>{i + 1}</button></span>)}
              <button type="button" className="kb3 sm" disabled={last && (!list.hasNextPage || list.isFetchingNextPage)} title={last && list.hasNextPage ? '다음 요청을 더 받아 옵니다' : undefined} onClick={next}>{list.isFetchingNextPage ? '받는 중' : '다음'}</button>
            </nav>
          </div>
        </div>}
      </>}
    </div>
  </main>;
}
