import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getCustomerHistory, type CustomerHistory, type StatsFilters } from '../api/cs-stats';
import { ErrorState, Loading, Preparing } from '../components/states';
import { KvCond, KvCondField, KvCondRow, KvFormCell, KvFormRow, KvGrid, KvKey, KvTitle, downloadCsv, useHotkey, type KvColumn } from '../ls-shell/kv';
import { CHANNEL_LABEL, REQUEST_STATUS_LABEL, REQUEST_TYPE_LABEL, STATE_LABEL, UNCONFIRMED_LABEL, derivationName, formatStamp } from '../ls-shell/cs-labels';
import { emptyFilters, rangeError, toApiFilters } from '../ls-shell/stats-common';

type Data = CustomerHistory['data'];
type Unit = Data['units'][number];
type Ticket = Data['tickets'][number];
type Request = Data['improvement_requests'][number];
const join = (values: Record<string, number>, label: (key: string) => string) => Object.keys(values).length ? Object.entries(values).map(([key, count]) => `${label(key)} ${count}`).join(', ') : '기록 없음';

// 고객 이력 CS0103. 고객 한 곳의 기간 내 문의, 설치 장비, 연결된 개선요청을 저장된 기록에서 읽어 보인다.
export default function CsCustomers() {
  if (!import.meta.env.DEV) return <Preparing title="고객 이력" message="운영 인증 중계와 권한 검증 후 제공됩니다." />;
  return <CustomersView />;
}

function CustomersView() {
  const mode = getApiMode();
  const [draft, setDraft] = useState(emptyFilters);
  const [filters, setFilters] = useState<StatsFilters>(emptyFilters);
  const [error, setError] = useState('');
  const history = useQuery({ queryKey: ['cs-stats', mode, 'customer', filters], queryFn: ({ signal }) => getCustomerHistory(filters, signal), enabled: filters.customer !== '', retry: false, staleTime: 0 });
  const apply = () => {
    const invalid = rangeError(draft) || (draft.customer.trim() ? '' : '고객 ID를 입력하세요.');
    setError(invalid);
    if (invalid) return;
    const next = toApiFilters(draft);
    if (JSON.stringify(next) === JSON.stringify(filters)) void history.refetch(); else setFilters(next);
  };
  useHotkey('F8', apply);

  // 다시 읽는 동안에는 이전 값을 보이지 않는다.
  const data: Data | undefined = history.isSuccess && !history.isFetching ? history.data.data : undefined;
  const unapplied = draft.customer.trim() !== filters.customer;
  const closed = data?.by_state.CLOSED ?? 0;
  const open = data ? Object.entries(data.by_state).filter(([key]) => key !== 'CLOSED').reduce((sum, [, count]) => sum + count, 0) : 0;

  const unitColumns: KvColumn<Unit>[] = [
    { key: 'serial', header: '시리얼', headClass: '', cell: item => item.serial_no },
    { key: 'product', header: '제품', width: 150, headClass: '', cell: item => item.product_code },
    { key: 'status', header: '상태', width: 80, className: 'c', cell: item => item.unit_status },
    { key: 'tickets', header: '연결된 문의', width: 80, className: 'c', cell: item => item.confirmed_tickets },
    { key: 'basis', header: '확인 근거', width: 90, className: 'c', cell: item => derivationName(item.derivation_kind) },
  ];
  const ticketColumns: KvColumn<Ticket>[] = [
    { key: 'id', header: '문의번호', width: 96, className: 'id', headClass: '', cell: item => <Link to={`/cs/tickets/${encodeURIComponent(item.id)}`}>{item.external_id ?? item.id.slice(0, 8)}</Link> },
    { key: 'at', header: '접수일시', width: 130, className: 'd', headClass: '', cell: item => formatStamp(item.received_at) },
    { key: 'subject', header: '제목', className: 't', headClass: '', cell: item => item.subject || '(제목 없음)' },
    { key: 'channel', header: '접수 경로', width: 80, className: 'c', cell: item => CHANNEL_LABEL[item.channel as keyof typeof CHANNEL_LABEL] ?? item.channel },
    { key: 'product', header: '제품', width: 150, headClass: '', cell: item => item.product_code },
    { key: 'state', header: '상태', width: 100, className: 'c', cell: item => STATE_LABEL[item.workflow_state] },
  ];
  const requestColumns: KvColumn<Request>[] = [
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_item, index) => index + 1 },
    { key: 'id', header: '요청번호', width: 96, className: 'id', headClass: '', cell: item => <Link to={`/cs/improvements/${item.id}`}>{item.id.slice(0, 8)}</Link> },
    { key: 'type', header: '유형', width: 64, className: 'c', cell: item => REQUEST_TYPE_LABEL[item.request_type as keyof typeof REQUEST_TYPE_LABEL] ?? item.request_type },
    { key: 'product', header: '제품', width: 150, headClass: '', cell: item => item.product_code },
    { key: 'status', header: '상태', width: 110, className: 'c', cell: item => REQUEST_STATUS_LABEL[item.status as keyof typeof REQUEST_STATUS_LABEL] ?? item.status },
    { key: 'evidence', header: '근거 문의', headClass: '', cell: item => `${item.fault_id ?? '고장 ID 없음'}, 근거 문의 ${item.evidence_tickets}건` },
  ];
  const exportCsv = () => data && downloadCsv(`고객_이력_${data.customer_external_id}`, ['문의번호', '접수일시', '제목', '접수 경로', '제품', '상태'],
    data.tickets.map(item => [item.external_id ?? item.id, formatStamp(item.received_at), item.subject ?? '', item.channel, item.product_code, STATE_LABEL[item.workflow_state]]));

  return <main>
    <KvTitle path="C/S 관리" title="고객 이력" code="CS0103">
      <button className="kb primary" disabled={history.isFetching} onClick={apply}>조회<KvKey>F8</KvKey></button>
      <button className="kb" disabled={!data || data.tickets.length === 0} onClick={exportCsv}>엑셀</button>
      <button className="kb" onClick={() => window.print()}>인쇄</button>
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 계약 예시이며 실제 업무 집계가 아닙니다. 고객별 예시가 없는 요청은 실패할 수 있습니다.</div>}
    <KvCond onSearch={apply}>
      <KvCondRow columns={2}>
        <KvCondField label="기간"><input type="date" aria-label="시작일" value={draft.start} onChange={event => setDraft({ ...draft, start: event.target.value })} /><em>~</em><input type="date" aria-label="종료일 (포함)" value={draft.end} onChange={event => setDraft({ ...draft, end: event.target.value })} /></KvCondField>
        <KvCondField label="고객코드"><input type="text" aria-label="고객 ID" maxLength={64} placeholder="예: CUST-0565" autoComplete="off" value={draft.customer} onChange={event => setDraft({ ...draft, customer: event.target.value })} /></KvCondField>
      </KvCondRow>
    </KvCond>
    <div className="kv-saved"><a href="#clear" onClick={event => { event.preventDefault(); setDraft(emptyFilters); setFilters(emptyFilters); setError(''); }}>초기화</a></div>
    {unapplied && draft.customer.trim() !== '' && <div className="kv-sub" style={{ margin: '4px 14px 0' }}>입력한 고객 ID는 아직 적용되지 않았습니다. 조회를 누르면 해당 고객을 조회합니다.</div>}
    {error && <div className="kv-err" role="alert" style={{ margin: '6px 14px 0', padding: '6px 10px' }}>{error}</div>}
    {filters.customer === '' && <div className="kv-sub" style={{ margin: '8px 14px 0' }}>고객 ID를 입력하고 조회하세요.</div>}
    {filters.customer !== '' && history.isFetching && <Loading />}
    {filters.customer !== '' && !history.isFetching && history.isError && <ErrorState error={history.error instanceof ApiError ? history.error : history.error} onRetry={() => void history.refetch()} />}

    {data && <>
      <div className="kv-form">
        <KvFormRow>
          <KvFormCell label="고객"><span className="ro">{data.customer_external_id}</span></KvFormCell>
          <KvFormCell label="기간 내 접수"><span className="ro">{data.received}건</span></KvFormCell>
          <KvFormCell label="진행 중"><span className="ro">{open}건</span></KvFormCell>
          <KvFormCell label="종결"><span className="ro">{closed}건</span></KvFormCell>
        </KvFormRow>
        <KvFormRow>
          <KvFormCell label="제품별"><span className="ro" title={join(data.by_product, key => key)}>{join(data.by_product, key => key)}</span></KvFormCell>
          <KvFormCell label="상태별"><span className="ro" title={join(data.by_state, key => STATE_LABEL[key as keyof typeof STATE_LABEL] ?? key)}>{join(data.by_state, key => STATE_LABEL[key as keyof typeof STATE_LABEL] ?? key)}</span></KvFormCell>
          <KvFormCell label="개선요청"><span className="ro">연결 {data.improvement_requests.length}건</span></KvFormCell>
          <KvFormCell label="미확인 지표"><span className="ro">{data.unconfirmed.length ? data.unconfirmed.map(key => UNCONFIRMED_LABEL[key] ?? `알 수 없는 지표 (${key})`).join(', ') : '없음'}</span></KvFormCell>
        </KvFormRow>
      </div>
      <div className="kv-gridhead"><span>보유 장비 {data.units.length}건</span><span className="unc" style={{ marginLeft: 8 }}>전체 이력, 기간 필터 미적용</span></div>
      <KvGrid label="설치 장비" columns={unitColumns} rows={data.units} rowKey={item => item.id} fallback="설치 장비 기록 없음" />
      <div className="kv-gridhead"><span>기간 내 문의 {data.received}건</span><span className="unc" style={{ marginLeft: 8 }}>최근 문의 최대 200건</span></div>
      <KvGrid label="기간 내 문의" columns={ticketColumns} rows={data.tickets} rowKey={item => item.id} fallback="문의 기록 없음" />
      <div className="kv-gridhead sub"><span>연결 개선요청 {data.improvement_requests.length}건</span><span className="unc" style={{ marginLeft: 8 }}>전체 이력, 기간 필터 미적용</span></div>
      <KvGrid label="연결된 개선요청" columns={requestColumns} rows={data.improvement_requests} rowKey={item => item.id} fallback="연결된 개선요청 없음" />
    </>}
  </main>;
}
