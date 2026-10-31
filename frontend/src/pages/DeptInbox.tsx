import { useMemo, useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listDeptRequests, type DeptCode, type TicketItem, type TransferStatus } from '../api/cs';
import { me } from '../api/platform';
import { ErrorState, Loading, Preparing } from '../components/states';
import { KvCond, KvCondField, KvCondRow, KvFoot, KvGrid, KvKey, KvNext, KvTitle, downloadCsv, useHotkey, type KvColumn } from '../ls-shell/kv';
import { DEPT_CODES, DEPT_LABEL, dayOf, formatStamp } from '../ls-shell/cs-labels';

const STATUS_LABEL: Record<TransferStatus, string> = { OPEN: '진행', RESULT_SUBMITTED: '답변 보냄', RETURNED_BY_DEPT: '담당 아님 반송', RECALLED: '요청 취소' };
const DAY = 24 * 60 * 60 * 1000;
type Transfer = NonNullable<TicketItem['transfer']>;
type Row = TicketItem & { transfer: Transfer };
const isDept = (value: string | null | undefined): value is DeptCode => !!value && DEPT_CODES.includes(value);

// 받은 요청 CS0202(부서 창). 우리 부서로 이관된 문의를 문의 목록 API의 부서 필터(KAN-202)로 읽어 보인다.
// 목록 항목에는 고객 ID와 요청 사유가 없어 그 열은 넣지 않는다. 개선요청 회신 표도 부서별 조회가 없어 넣지 않는다.
export default function DeptInbox() {
  if (!import.meta.env.DEV) return <Preparing title="받은 요청" message="운영 인증 중계와 권한 검증 후 제공됩니다." />;
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
  const rows = all
    .filter(row => (round === '' || (round === '1' ? row.transfer.round === 1 : row.transfer.round >= 2))
      && (!from || dayOf(row.transfer.transferred_at) >= from) && (!to || dayOf(row.transfer.transferred_at) <= to))
    .sort((a, b) => b.transfer.transferred_at.localeCompare(a.transfer.transferred_at));
  const invalid = !!from && !!to && from > to;
  const shown = invalid ? [] : rows;
  const open = shown.filter(row => row.transfer.status === 'OPEN');
  const old = open.filter(row => elapsed(row) >= 7).length;

  const columns: KvColumn<Row>[] = [
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_row, index) => index + 1 },
    { key: 'id', header: '문의번호', width: 110, className: 'id', cell: row => <Link to={`/dept/result/${row.id}`}>{row.external_id ?? row.id.slice(0, 8)}</Link> },
    { key: 'subject', header: '제목', className: 't', headClass: '', cell: row => row.subject ?? '' },
    { key: 'round', header: '차수', width: 52, className: 'c', cell: row => row.transfer.round },
    { key: 'at', header: '요청일시', width: 130, className: 'd', sort: 'descending', headClass: '', cell: row => formatStamp(row.transfer.transferred_at) },
    { key: 'status', header: '상태', width: 110, className: 'c', cell: row => STATUS_LABEL[row.transfer.status] },
    { key: 'age', header: '경과일', width: 64, className: 'c', cell: row => row.transfer.status === 'OPEN' ? elapsed(row) : '' },
  ];
  const exportCsv = () => downloadCsv('받은_요청', ['문의번호', '제목', '차수', '요청일시', '상태', '경과일'],
    shown.map(row => [row.external_id ?? row.id, row.subject ?? '', row.transfer.round, formatStamp(row.transfer.transferred_at), STATUS_LABEL[row.transfer.status], row.transfer.status === 'OPEN' ? elapsed(row) : '']));

  return <main>
    <KvTitle path="부서 검토" title="받은 요청" code="CS0202">
      <button className="kb primary" disabled={!dept || list.isFetching} onClick={fetchAgain}>조회<KvKey>F8</KvKey></button>
      <button className="kb" disabled={shown.length === 0} onClick={exportCsv}>엑셀</button>
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 부서 목록 예시가 없어 비어 있습니다. 실제 API 모드에서 확인하세요.</div>}
    {identity.isPending && <Loading label="소속 부서 확인 중" />}
    {identity.isError && <ErrorState error={identity.error} onRetry={() => { void identity.refetch(); }} />}
    {identity.isSuccess && identity.data === null && <div className="kv-sub" style={{ margin: '6px 14px 0' }}>로그인이 필요합니다. <Link to="/account">계정 확인</Link></div>}
    {identity.isSuccess && identity.data && <>
      {dept && <KvNext>답변할 요청 <b>{open.length}</b>건, 7일 이상 <b>{old}</b>. {DEPT_LABEL[dept]}, {formatStamp(new Date().toISOString())} 기준</KvNext>}
      <KvCond onSearch={fetchAgain}>
        <KvCondRow columns={4}>
          <KvCondField label="부서"><select aria-label="부서" value={dept ?? ''} disabled={isDept(own)} onChange={event => setPicked(event.target.value)}>
            {!dept && <option value="">부서 선택</option>}
            {DEPT_CODES.map(code => <option key={code} value={code}>{DEPT_LABEL[code]}</option>)}
          </select></KvCondField>
          <KvCondField label="상태"><select aria-label="상태" value={status} onChange={event => setStatus(event.target.value as TransferStatus | '')}>
            {(Object.keys(STATUS_LABEL) as TransferStatus[]).map(key => <option key={key} value={key}>{STATUS_LABEL[key]}</option>)}
            <option value="">전체</option>
          </select></KvCondField>
          <KvCondField label="차수"><select aria-label="차수" value={round} onChange={event => setRound(event.target.value as '' | '1' | '2+')}>
            <option value="">전체</option><option value="1">1차</option><option value="2+">2차 이상</option>
          </select></KvCondField>
          <KvCondField label="요청일자"><div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="date" aria-label="시작일" value={from} onChange={event => setFrom(event.target.value)} />~<input type="date" aria-label="종료일" value={to} onChange={event => setTo(event.target.value)} />
          </div></KvCondField>
        </KvCondRow>
      </KvCond>
      {!isDept(own) && !dept && <div className="kv-sub" style={{ margin: '6px 14px 0' }}>소속 부서가 없는 계정입니다. 볼 부서를 고르세요. 볼 수 있는 범위는 서버가 정합니다.</div>}
      {invalid && <div className="kv-sub" role="alert" style={{ margin: '6px 14px 0' }}>종료일은 시작일보다 빠를 수 없습니다.</div>}
      {dept && list.isPending && <Loading />}
      {dept && list.isError && <ErrorState error={list.error} onRetry={fetchAgain} />}
      {dept && list.isSuccess && <>
        <div className="kv-gridhead"><span>문의 검토 요청 {shown.length}건. 문의별 최신 검토 요청을 표시, 고객·요청 사유는 목록 API가 주지 않아 상세에서 확인</span></div>
        <KvGrid label="받은 요청" columns={columns} rows={shown} rowKey={row => row.id} fallback="조건에 맞는 요청이 없습니다." />
        <KvFoot count={`표시 ${shown.length}건 / 받아 온 ${all.length}건`} more={list.hasNextPage ? <button className="kb" disabled={list.isFetchingNextPage} onClick={() => { void list.fetchNextPage(); }}>{list.isFetchingNextPage ? '불러오는 중' : '더 보기'}</button> : undefined} />
      </>}
    </>}
  </main>;
}
