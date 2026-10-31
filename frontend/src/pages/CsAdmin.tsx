import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getOverview, getOwners, type Overview, type Owners, type StatsFilters } from '../api/cs-stats';
import { ErrorState, Loading, Preparing } from '../components/states';
import { KvCond, KvCondField, KvCondRow, KvGrid, KvKey, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { DEPT_CODES, REQUEST_STATUS_LABEL, STATE_LABEL, UNCONFIRMED_LABEL, deptName } from '../ls-shell/cs-labels';
import { emptyFilters, rangeError, timestamp, toApiFilters } from '../ls-shell/stats-common';

type Dept = Overview['data']['departments'][number];
type OwnerRow = Owners['data']['items'][number];
type Judge = { label: string; count: number | null; what: string; go: ReactLink | null };
type ReactLink = { to: string; text: string };
const bar = (value: number, max: number) => <span style={{ display: 'inline-block', height: 10, width: `${max ? Math.max(2, Math.round(100 * value / max)) : 0}%`, background: 'var(--erp-accent)' }} aria-hidden="true" />;

// 운영 현황 CS0901. 관리자가 판단할 건과 처리·개선 집계를 저장된 기록에서 읽어 보인다.
export default function CsAdmin() {
  if (!import.meta.env.DEV) return <Preparing title="운영 현황" message="운영 인증 중계와 권한 검증 후 제공됩니다." />;
  return <AdminView />;
}

function AdminView() {
  const mode = getApiMode();
  const [draft, setDraft] = useState(emptyFilters);
  const [filters, setFilters] = useState<StatsFilters>(emptyFilters);
  const [error, setError] = useState('');
  const overview = useQuery({ queryKey: ['cs-stats', mode, 'overview', filters], queryFn: ({ signal }) => getOverview(filters, signal), retry: false, staleTime: 0 });
  // 담당자별 집계는 관리자 범위다. 권한이 없으면 이 구역만 안내로 바꾸고 나머지 집계는 그대로 보인다.
  const owners = useQuery({ queryKey: ['cs-stats', mode, 'owners', { ...filters, customer: '' }], queryFn: ({ signal }) => getOwners({ ...filters, customer: '' }, signal), retry: false, staleTime: 0 });
  const refresh = () => { void overview.refetch(); void owners.refetch(); };
  const apply = () => {
    const invalid = rangeError(draft);
    setError(invalid);
    if (invalid) return;
    const next = toApiFilters(draft);
    if (JSON.stringify(next) === JSON.stringify(filters)) refresh(); else setFilters(next);
  };
  useHotkey('F8', apply);

  const busy = overview.isFetching || owners.isFetching;
  const o = overview.isSuccess && !overview.isFetching ? overview.data.data : undefined;
  const ownerRows = owners.isSuccess && !owners.isFetching ? owners.data.data.items : undefined;
  const ownerDenied = owners.isError && !owners.isFetching && owners.error instanceof ApiError && (owners.error.status === 403 || owners.error.status === 401);

  const judges: Judge[] = o ? [
    { label: '7일 이상 미처리', count: o.tickets.open_age.over_7_days, what: '담당자가 혼자 못 푸는 건인지 보고, 재배정하거나 부서에 독촉합니다.', go: { to: '/cs/tickets', text: '문의 조회, 7일 이상' } },
    { label: '담당자 없음', count: ownerRows ? ownerRows.filter(item => item.owner_user_id === null).reduce((sum, item) => sum + item.open, 0) : null, what: '접수만 되고 아무도 맡지 않은 문의. 담당자를 정해야 움직입니다. 진행은 전체 기간 기준이며 고객 조건을 적용하지 않습니다.', go: null },
    { label: '부서가 반송·취소한 검토 요청', count: o.departments.reduce((sum, item) => sum + item.returned_or_recalled, 0), what: '부서가 자기 일이 아니라고 돌려보낸 건. 다른 부서로 다시 보낼지 정합니다.', go: null },
    { label: '부서 답변 왔는데 확인 전', count: o.departments.reduce((sum, item) => sum + Math.max(0, item.results_submitted - item.results_accepted), 0), what: '부서는 답했는데 담당자가 아직 고객 답변으로 옮기지 않은 건.', go: { to: '/cs/tickets', text: '문의 조회, 검토 답변 확인' } },
    { label: '개선요청 검토 부서 미정', count: o.improvements ? o.improvements.receiver_unconfirmed : null, what: '개선요청이 어느 부서로 가야 할지 정해지지 않았습니다.', go: { to: '/cs/improvements', text: '개선요청 현황' } },
  ] : [];
  const judgeColumns: KvColumn<Judge>[] = [
    { key: 'item', header: '항목', width: 190, headClass: '', cell: item => item.label },
    { key: 'count', header: '건수', width: 56, className: 'c', cell: item => item.count ?? '-' },
    { key: 'what', header: '판단할 내용', headClass: '', cell: item => item.what },
    { key: 'go', header: '바로 가기', width: 160, headClass: '', cell: item => item.go ? <Link to={item.go.to}>{item.go.text}</Link> : <span className="unc">연결 전</span> },
  ];
  const deptColumns: KvColumn<Dept>[] = [
    { key: 'dept', header: '부서', headClass: '', cell: item => <span style={item.returned_or_recalled > 0 ? { fontWeight: 600 } : undefined}>{deptName(item.dept_code)}</span> },
    { key: 'sent', header: '요청', width: 56, className: 'c', cell: item => item.transferred },
    { key: 'answered', header: '답변', width: 56, className: 'c', cell: item => item.results_submitted },
    { key: 'accepted', header: '확인 완료', width: 70, className: 'c', cell: item => item.results_accepted },
    { key: 'returned', header: '반송/취소', width: 70, className: 'c', cell: item => item.returned_or_recalled },
  ];
  const ownerColumns: KvColumn<OwnerRow>[] = [
    { key: 'owner', header: '담당자', headClass: '', cell: item => item.owner_user_id ?? '미배정' },
    { key: 'open', header: '진행', width: 56, className: 'c', cell: item => item.open },
    { key: 'received', header: '접수', width: 56, className: 'c', cell: item => item.received },
    { key: 'closed', header: '종결', width: 56, className: 'c', cell: item => item.closed },
  ];
  const ages: [string, number][] = o ? [['1일 미만', o.tickets.open_age.under_1_day], ['1~3일', o.tickets.open_age.from_1_to_3_days], ['3~7일', o.tickets.open_age.from_3_to_7_days], ['7일 이상', o.tickets.open_age.over_7_days]] : [];
  const maxAge = Math.max(0, ...ages.map(([, count]) => count));
  const openStates = o ? Object.entries(o.tickets.by_state).filter(([key]) => key !== 'CLOSED') : [];

  return <main>
    <KvTitle path="통계/보고서" title="운영 현황" code="CS0901">
      <button className="kb primary" disabled={busy} onClick={apply}>조회<KvKey>F8</KvKey></button>
      <button className="kb" onClick={() => window.print()}>인쇄</button>
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 계약 예시이며 실제 업무 집계가 아닙니다. 필터·고객별 예시가 없는 요청은 실패할 수 있습니다.</div>}
    <KvCond onSearch={apply}>
      <KvCondRow columns={3}>
        <KvCondField label="기간"><input type="date" aria-label="시작일" value={draft.start} onChange={event => setDraft({ ...draft, start: event.target.value })} /><em>~</em><input type="date" aria-label="종료일 (포함)" value={draft.end} onChange={event => setDraft({ ...draft, end: event.target.value })} /></KvCondField>
        <KvCondField label="부서"><select aria-label="이관 부서" value={draft.dept} onChange={event => setDraft({ ...draft, dept: event.target.value })}><option value="">전체</option>{DEPT_CODES.map(code => <option key={code} value={code}>{deptName(code)}</option>)}</select></KvCondField>
        <KvCondField label="고객"><input type="text" aria-label="고객 ID" maxLength={64} placeholder="고객코드" autoComplete="off" value={draft.customer} onChange={event => setDraft({ ...draft, customer: event.target.value })} /></KvCondField>
      </KvCondRow>
    </KvCond>
    <div className="kv-saved"><span>담당자별 집계는 고객 조건을 적용하지 않습니다.</span><a href="#clear" onClick={event => { event.preventDefault(); setDraft(emptyFilters); setFilters(emptyFilters); setError(''); }}>초기화</a></div>
    {error && <div className="kv-err" role="alert" style={{ margin: '6px 14px 0', padding: '6px 10px' }}>{error}</div>}
    {overview.isFetching && <Loading />}
    {!overview.isFetching && overview.isError && <ErrorState error={overview.error} onRetry={refresh} />}

    {o && <>
      <div className="kv-sub" style={{ margin: '8px 14px 0' }}>{o.scope === 'ALL' ? '전체' : '본인 담당'} 범위, 기간 {o.period.start ? timestamp(o.period.start) : '처음부터'} ~ {o.period.end ? timestamp(o.period.end) : '현재까지'} (종료 미포함). 최초·최종 접수 기록 {timestamp(o.data_range.first_received_at)} ~ {timestamp(o.data_range.last_received_at)}. 미확인 지표: {o.unconfirmed.length ? o.unconfirmed.map(key => UNCONFIRMED_LABEL[key] ?? `알 수 없는 지표 (${key})`).join(', ') : '없음'}</div>
      <div className="kv-gridhead"><span>판단이 필요한 것</span><span className="unc" style={{ marginLeft: 8 }}>건수는 조회 조건(기간·부서·고객)을 따릅니다</span></div>
      <KvGrid label="판단이 필요한 것" columns={judgeColumns} rows={judges} rowKey={item => item.label} fallback="판단할 건 없음" />

      <div className="kv-split" style={{ marginTop: 12 }}>
        <section className="kv-box">
          <h2>처리 현황<small>접수 {o.tickets.received}, 종결 {o.tickets.closed}, 기간 내 접수 중 현재 진행 {o.tickets.open}</small></h2>
          <div className="body">
            <div className="kv-sub" style={{ margin: '0 0 4px' }}>진행 문의 접수 후 경과 기간 (SLA 지연 아님)</div>
            <table className="kv-grid free"><tbody>{ages.map(([label, count]) => <tr key={label}><td style={{ width: 70 }}>{label}</td><td style={{ width: 40 }} className="c">{count}</td><td>{bar(count, maxAge)}</td></tr>)}</tbody></table>
            <div className="kv-sub" style={{ margin: '10px 0 4px' }}>진행 중 상태별</div>
            <table className="kv-grid free"><thead><tr><th>상태</th><th className="c">건수</th></tr></thead><tbody>{openStates.length ? openStates.map(([key, count]) => <tr key={key}><td>{STATE_LABEL[key as keyof typeof STATE_LABEL] ?? key}</td><td className="c">{count}</td></tr>) : <tr><td colSpan={2}>저장된 집계 없음</td></tr>}</tbody></table>
            <div className="kv-sub" style={{ margin: '10px 0 0' }}>자료 구분: {Object.keys(o.tickets.by_origin).length ? Object.entries(o.tickets.by_origin).map(([key, count]) => `${key} ${count}`).join(', ') : '기록 없음'}</div>
          </div>
        </section>
        <section className="kv-box">
          <h2>부서별 검토 요청<small>기간 내, 반송/취소 있는 부서 굵게</small></h2>
          <div className="body" style={{ padding: 0 }}><KvGrid label="부서별 검토 요청" columns={deptColumns} rows={o.departments} rowKey={item => item.dept_code} fallback="이관 기록 없음" /></div>
        </section>
      </div>

      <div className="kv-split" style={{ marginTop: 12 }}>
        <section className="kv-box">
          <h2>담당자별 진행<small>접수·종결은 기간 기준, 진행은 전체 기간</small></h2>
          <div className="body" style={{ padding: 0 }}>
            {ownerDenied ? <div className="kv-sub" style={{ margin: 0, padding: 10 }}>담당자별 현황은 관리자 범위에서만 제공됩니다.</div>
              : owners.isError && !owners.isFetching ? <div className="kv-sub" style={{ margin: 0, padding: 10 }}>담당자별 현황을 불러오지 못했습니다.</div>
                : <KvGrid label="담당자별 진행" columns={ownerColumns} rows={ownerRows ?? []} rowKey={item => item.owner_user_id ?? 'unassigned'} fallback={ownerRows ? '담당자 집계 없음' : '불러오는 중'} />}
            <div className="kv-sub" style={{ margin: 0, padding: '6px 10px' }}>사용자 이름은 추측하지 않고 API 식별자로 표시합니다.</div>
          </div>
        </section>
        <section className="kv-box">
          <h2>고객별<small>접수 상위 10, 진행 중</small></h2>
          <div className="body" style={{ padding: 0 }}>
            <KvGrid label="고객별 접수" columns={[
              { key: 'c', header: '고객', headClass: '', cell: (item: Overview['data']['top_customers'][number]) => item.customer_external_id },
              { key: 'o', header: '진행', width: 56, className: 'c', cell: item => item.open },
              { key: 'r', header: '접수', width: 56, className: 'c', cell: item => item.received },
            ]} rows={o.top_customers} rowKey={item => item.customer_external_id} fallback="고객 집계 없음" />
          </div>
        </section>
      </div>

      <div className="kv-split" style={{ marginTop: 12 }}>
        <section className="kv-box">
          <h2>개선요청{o.improvements && <small>진행 {o.improvements.open} / 수신 부서 미확인 {o.improvements.receiver_unconfirmed}</small>}</h2>
          <div className="body" style={{ padding: 0 }}>
            {o.improvements
              ? <KvGrid label="개선요청 상태" columns={[
                { key: 's', header: '상태', headClass: '', cell: (item: [string, number]) => REQUEST_STATUS_LABEL[item[0] as keyof typeof REQUEST_STATUS_LABEL] ?? item[0] },
                { key: 'n', header: '건수', width: 56, className: 'c', cell: item => item[1] },
              ]} rows={Object.entries(o.improvements.by_status)} rowKey={item => item[0]} fallback="저장된 집계 없음" />
              : <div className="kv-sub" style={{ margin: 0, padding: 10 }}>개선 집계는 관리자 범위에서만 제공됩니다.</div>}
          </div>
        </section>
        <section className="kv-box">
          <h2>AI 처리<span className="unc" style={{ marginLeft: 8 }}>아직 없음, KAN-103 뒤</span></h2>
          <div className="body"><div className="kv-sub" style={{ margin: 0 }}>요약, 분류, 정보 요청 문안, 담당자가 고친 비율은 연결 전입니다. 처리 기한과 추천 부서도 아직 없습니다.</div></div>
        </section>
      </div>
    </>}
  </main>;
}
