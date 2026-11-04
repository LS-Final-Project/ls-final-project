import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getCustomerHistory, type CustomerHistory, type StatsFilters } from '../api/cs-stats';
import { ErrorState, Loading } from '../components/states';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { downloadCsv, useHotkey } from '../ls-shell/kv';
import { CHANNEL_LABEL, REQUEST_STATUS_LABEL, REQUEST_TYPE_LABEL, STATE_LABEL, derivationName, formatStamp } from '../ls-shell/cs-labels';
import { emptyFilters, rangeError, toApiFilters } from '../ls-shell/stats-common';
import './ticket-detail-v3.css';
import './customers-v3.css';

type Data = CustomerHistory['data'];
const stateName = (key: string) => STATE_LABEL[key as keyof typeof STATE_LABEL] ?? key;
// 고객 코드는 늘 CUST-로 시작한다. 입력 칸에 미리 적어 두고 번호만 쓰게 한다. 앞부분만 있으면 입력 안 한 것으로 본다.
const PREFIX = 'CUST-';
const typed = (value: string) => { const v = value.trim(); return v === PREFIX ? '' : v; };
// 상태 카드 순서: 처리 흐름 순.
const STATE_ORDER = ['RECEIVED', 'IN_REVIEW', 'TRANSFERRED', 'WAITING_CUSTOMER', 'ANSWERED', 'CLOSED'];
const UNIT_STATUS: Record<string, string> = { ACTIVE: '사용 중', REPLACED: '교체됨', DECOMMISSIONED: '철거' };

// 고객 이력 CS0103. 고객 한 곳의 기간 내 문의, 설치 장비, 연결된 개선요청을 저장된 기록에서 읽어 보인다.
export default function CsCustomers() {
  const access = useBusinessAccess('cs');
  if (!access.ready) return <BusinessGate access={access} title="고객 이력" />;
  return <CustomersView />;
}

function CustomersView() {
  const mode = getApiMode();
  // 문의 처리 화면의 고객 링크(?customer=)로 들어오면 그 고객을 바로 조회한다.
  const [params] = useSearchParams();
  const initial: StatsFilters = { ...emptyFilters, customer: params.get('customer')?.trim() || PREFIX, start: params.get('start') ?? '', end: params.get('end') ?? '' };
  const [draft, setDraft] = useState(initial);
  const [filters, setFilters] = useState<StatsFilters>(() => typed(initial.customer) && !rangeError(initial) ? toApiFilters(initial) : emptyFilters);
  const [error, setError] = useState(() => typed(initial.customer) ? rangeError(initial) : '');
  const history = useQuery({ queryKey: ['cs-stats', mode, 'customer', filters], queryFn: ({ signal }) => getCustomerHistory(filters, signal), enabled: filters.customer !== '', retry: false, staleTime: 0 });
  const apply = () => {
    const invalid = rangeError(draft) || (typed(draft.customer) ? '' : '고객 ID를 입력하세요.');
    setError(invalid);
    if (invalid) return;
    const next = toApiFilters(draft);
    if (JSON.stringify(next) === JSON.stringify(filters)) void history.refetch(); else setFilters(next);
  };
  useHotkey('F8', apply);

  // 다시 읽는 동안에는 이전 값을 보이지 않는다.
  const data: Data | undefined = history.isSuccess && !history.isFetching ? history.data.data : undefined;
  const unapplied = typed(draft.customer) !== filters.customer;
  const exportCsv = () => data && downloadCsv(`고객_이력_${data.customer_external_id}`, ['문의번호', '접수일시', '제목', '접수 경로', '제품', '상태'],
    data.tickets.map(item => [item.external_id ?? item.id, formatStamp(item.received_at), item.subject ?? '', item.channel, item.product_code, stateName(item.workflow_state)]));
  const reset = () => { setDraft({ ...emptyFilters, customer: PREFIX }); setFilters(emptyFilters); setError(''); };

  return <main className="td3 cu3">
    <div className="title">
      <span className="path">C/S 관리 ›</span><h1>고객 이력</h1><code className="kv-code">CS0103</code>
      <div className="btns">
        <button className="kb3" disabled={!data || data.tickets.length === 0} onClick={exportCsv}>엑셀</button>
        <button className="kb3" onClick={() => window.print()}>인쇄</button>
        <span className="vsep" />
        <button className="kb3 pri" disabled={history.isFetching} onClick={apply}>조회<kbd aria-hidden="true">F8</kbd></button>
        <Link className="kb3" to="/cs/tickets">문의 조회</Link>
      </div>
    </div>

    <div className="body">
      {mode === 'mock' && <div className="tier-note" role="status">모의 응답 모드 — 계약 예시이며 실제 업무 집계가 아닙니다. 고객별 예시가 없는 요청은 실패할 수 있습니다.</div>}
      <form className="cond" onSubmit={event => { event.preventDefault(); apply(); }}>
        <span className="k">고객코드</span>
        <input type="text" aria-label="고객 ID" maxLength={64} placeholder="CUST-0565" autoComplete="off" value={draft.customer} onChange={event => setDraft({ ...draft, customer: event.target.value })} />
        <span className="k">기간</span>
        <span className="range"><input type="date" aria-label="시작일" value={draft.start} onChange={event => setDraft({ ...draft, start: event.target.value })} /><em>~</em><input type="date" aria-label="종료일 (포함)" value={draft.end} onChange={event => setDraft({ ...draft, end: event.target.value })} /></span>
        <a href="#clear" className="clear" onClick={event => { event.preventDefault(); reset(); }}>초기화</a>
        {/* 입력 칸이 여럿이면 제출 단추가 있어야 Enter로 조회된다. 보이는 조회 단추는 제목 줄에 있다. */}
        <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
      </form>
      {unapplied && typed(draft.customer) !== '' && <div className="stale">입력한 고객 ID는 아직 적용되지 않았습니다. 조회를 누르면 해당 고객을 조회합니다.</div>}
      {error && <div className="note err" role="alert">{error}</div>}
      {filters.customer === '' && <div className="pane"><div className="pb empty">고객 ID를 입력하고 조회하세요.<small>문의 처리 화면의 고객 링크로 들어오면 그 고객을 바로 보여 줍니다.</small></div></div>}
      {filters.customer !== '' && history.isFetching && <Loading />}
      {filters.customer !== '' && !history.isFetching && history.isError && <ErrorState error={history.error instanceof ApiError ? history.error : history.error} onRetry={() => void history.refetch()} />}

      {data && <>
        {/* 상태별 문의 건수를 큰 숫자로 보인다. 진행 중 상태는 주황, 종결은 초록. */}
        <div className="state-cards" aria-label="상태별 문의">
          {STATE_ORDER.map(key => <div key={key} className={`state-card${key === 'CLOSED' ? ' closed' : (data.by_state[key] ?? 0) > 0 ? ' open' : ''}`}>
            <small>{stateName(key)}</small><b>{data.by_state[key] ?? 0}</b><span>건</span>
          </div>)}
        </div>

        <div className="cu-cols">
          <div className="pane">
            <div className="ph">기간 내 문의 {data.received}건<div className="r"><span className="meta">최근 문의 최대 200건</span></div></div>
            <div className="logwrap">
              <table className="log" aria-label="기간 내 문의">
                <thead><tr><th style={{ width: 110 }}>문의번호</th><th style={{ width: 130 }}>접수일시</th><th>제목</th><th style={{ width: 76 }}>접수 경로</th><th style={{ width: 140 }}>제품</th><th style={{ width: 90 }}>상태</th></tr></thead>
                <tbody>{data.tickets.length === 0 ? <tr><td colSpan={6} className="none">문의 기록 없음</td></tr> : data.tickets.map(item => <tr key={item.id}>
                  <td><Link className="link" to={`/cs/tickets/${encodeURIComponent(item.id)}`}>{item.external_id ?? item.id.slice(0, 8)}</Link></td>
                  <td className="meta">{formatStamp(item.received_at)}</td>
                  <td className="subj" title={item.subject ?? ''}>{item.subject || '(제목 없음)'}</td>
                  <td>{CHANNEL_LABEL[item.channel as keyof typeof CHANNEL_LABEL] ?? item.channel}</td>
                  <td>{item.product_code}</td>
                  <td><span className={item.workflow_state === 'CLOSED' ? 'chip x' : 'chip'}>{stateName(item.workflow_state)}</span></td>
                </tr>)}</tbody>
              </table>
            </div>
          </div>

          <div className="stack">
            <div className="pane">
              <div className="ph">보유 장비 {data.units.length}대<div className="r"><span className="meta">전체 이력, 기간 필터 미적용</span></div></div>
              <div className="logwrap short">
                <table className="log" aria-label="설치 장비">
                  <thead><tr><th>시리얼</th><th style={{ width: 130 }}>제품</th><th style={{ width: 64 }}>상태</th><th style={{ width: 70 }}>연결 문의</th><th style={{ width: 84 }}>확인 근거</th></tr></thead>
                  <tbody>{data.units.length === 0 ? <tr><td colSpan={5} className="none">설치 장비 기록 없음</td></tr> : data.units.map(item => <tr key={item.id}>
                    <td className="mono">{item.serial_no}</td>
                    <td>{item.product_code}</td>
                    <td>{UNIT_STATUS[item.unit_status] ?? item.unit_status}</td>
                    <td className="c">{item.confirmed_tickets}</td>
                    <td><span className="chip w">{derivationName(item.derivation_kind)}</span></td>
                  </tr>)}</tbody>
                </table>
              </div>
            </div>

            <div className="pane">
              <div className="ph">연결 개선요청 {data.improvement_requests.length}건<div className="r"><span className="meta">전체 이력, 기간 필터 미적용</span></div></div>
              <div className="logwrap short">
                <table className="log" aria-label="연결된 개선요청">
                  <thead><tr><th style={{ width: 92 }}>요청번호</th><th style={{ width: 52 }}>유형</th><th style={{ width: 130 }}>제품</th><th style={{ width: 90 }}>상태</th><th>근거</th></tr></thead>
                  <tbody>{data.improvement_requests.length === 0 ? <tr><td colSpan={5} className="none">연결된 개선요청 없음</td></tr> : data.improvement_requests.map(item => <tr key={item.id}>
                    <td><Link className="link" to={`/cs/improvements/${item.id}`}>{item.id.slice(0, 8)}</Link></td>
                    <td>{REQUEST_TYPE_LABEL[item.request_type as keyof typeof REQUEST_TYPE_LABEL] ?? item.request_type}</td>
                    <td>{item.product_code}</td>
                    <td>{REQUEST_STATUS_LABEL[item.status as keyof typeof REQUEST_STATUS_LABEL] ?? item.status}</td>
                    <td className="meta">{`${item.fault_id ?? '고장 ID 없음'}, 근거 문의 ${item.evidence_tickets}건`}</td>
                  </tr>)}</tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </>}
    </div>
  </main>;
}
