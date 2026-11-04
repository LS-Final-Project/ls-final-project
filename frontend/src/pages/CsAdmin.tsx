import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import Pager, { paged } from '../components/Pager';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { ApiError, getApiMode } from '../api/client';
import { getOverview, getOwners, type Overview, type Owners, type StatsFilters } from '../api/cs-stats';
import { listUserNames } from '../api/platform';
import { useHotkey } from '../ls-shell/kv';
import { DEPT_CODES, deptName } from '../ls-shell/cs-labels';
import { emptyFilters, rangeError, toApiFilters } from '../ls-shell/stats-common';
import LogTable, { type LogColumn } from './LogTable';
import './ticket-detail-v3.css';
import './list-cond.css';
import './admin-v3.css';

// 업무 통계 CS0901. 관리자가 SLA·담당자·부서·문의·개선요청 진행을 한눈에 본다.
// 값은 모두 서버 집계(GET /api/v1/cs/stats/overview, /owners, KAN-233)이며, 서버가 주지 않은 칸은 '미확인'으로 둔다.
export default function CsAdmin() {
  const access = useBusinessAccess('cs');
  if (!access.ready) return <BusinessGate access={access} title="업무 통계" />;
  return <StatsView />;
}

type Data = Overview['data'];
type Dept = Data['departments'][number];
type OwnerRow = Owners['data']['items'][number];
type OverdueRow = NonNullable<Data['sla']>['overdue_tickets'][number];
const PRIORITY_LABEL: Record<string, string> = { P1: '긴급 · 설비 정지', P2: '높음 · 생산 영향', P3: '보통', P4: '낮음 · 일반 문의' };
const TICKET_STAGES: [string, string, string][] = [
  ['RECEIVED', '접수', '검토 시작 전'], ['IN_REVIEW', '검토중', '담당자가 처리 중'], ['WAITING_CUSTOMER', '고객 회신 대기', '추가 정보 요청함'],
  ['TRANSFERRED', '부서 검토 중', '전문 부서 답변 대기'], ['ANSWERED', '답변 완료', '종결 여부 확인'],
];
const REQUEST_STAGES: [string, string, string][] = [
  ['DRAFT', '작성 중', 'C/S 검토 대기'], ['REVIEWED', '검토 완료', '부서 전달 대기'], ['SENT', '부서에 보냄', '수신 확인 대기'],
  ['ACKNOWLEDGED', '부서 확인', '부서 회신 대기'], ['ADOPTED', '채택', '조치 기록 대기'], ['ACTION_TAKEN', '조치 기록', '후속 확인 대기'],
  ['FOLLOW_UP', '후속 확인', '재발 점검 중'],
];
const UNKNOWN = <span className="unc">미확인</span>;

const pct = (met: number, total: number) => total ? Math.round(1000 * met / total) / 10 : 0;
// 초과 시간(시간 단위)을 "1일 4시간", "9시간"처럼 보인다.
function overText(hours: number) {
  const whole = Math.max(0, Math.round(hours));
  const days = Math.floor(whole / 24), rest = whole % 24;
  if (!days) return `${Math.max(1, rest)}시간`;
  return rest ? `${days}일 ${rest}시간` : `${days}일`;
}
// 서버가 칸을 주지 않으면(이전 서버 등) 0이 아니라 미확인으로 보인다.
const count = (value: number | null | undefined, hot?: 'warm' | 'hot') => value === null || value === undefined ? UNKNOWN
  : value && hot ? <span className={`cnt ${hot}`}>{value}</span> : <span className="num">{value}</span>;

function StatsView() {
  const mode = getApiMode();
  const [draft, setDraft] = useState(emptyFilters);
  const [filters, setFilters] = useState<StatsFilters>(emptyFilters);
  const [error, setError] = useState('');
  const [ownerPage, setOwnerPage] = useState(0);
  const overview = useQuery({ queryKey: ['cs-stats', mode, 'overview', filters], queryFn: ({ signal }) => getOverview(filters, signal), retry: false, staleTime: 0 });
  // 담당자별 집계는 관리자 범위다. 권한이 없으면 그 구역만 안내로 바꾸고 나머지는 그대로 보인다.
  const owners = useQuery({ queryKey: ['cs-stats', mode, 'owners', filters], queryFn: ({ signal }) => getOwners(filters, signal), retry: false, staleTime: 0 });
  const refresh = () => { void overview.refetch(); void owners.refetch(); };
  const apply = () => {
    const invalid = rangeError(draft);
    setError(invalid);
    if (invalid) return;
    const next = toApiFilters(draft);
    setOwnerPage(0);
    if (JSON.stringify(next) === JSON.stringify(filters)) refresh(); else setFilters(next);
  };
  const clear = () => { setDraft(emptyFilters); setFilters(emptyFilters); setError(''); setOwnerPage(0); };
  useHotkey('F8', apply);

  const busy = overview.isFetching || owners.isFetching;
  const o = overview.isSuccess && !overview.isFetching ? overview.data.data : undefined;
  const ownerRows = owners.isSuccess && !owners.isFetching ? owners.data.data.items : undefined;
  const ownerDenied = owners.isError && !owners.isFetching && owners.error instanceof ApiError && (owners.error.status === 403 || owners.error.status === 401);
  const sla = o?.sla ?? null;

  // 담당자 이름은 플랫폼에서 읽는다. 못 읽으면 ID 앞부분으로 보인다.
  const ids = [...(ownerRows ?? []).map(row => row.owner_user_id), ...(sla?.overdue_tickets ?? []).map(row => row.owner_user_id)].filter((id): id is string => !!id);
  const ownerIds = [...new Set(ids)].sort();
  const names = useQuery({ queryKey: ['user-names', mode, ownerIds], queryFn: ({ signal }) => listUserNames(ownerIds, signal), enabled: ownerIds.length > 0 && mode === 'live', retry: false });
  const nameOf = (id: string | null) => !id ? '미배정' : (Array.isArray(names.data?.data?.items) ? names.data.data.items : []).find(item => item.user_id === id)?.display_name ?? `사용자 ${id.slice(0, 8)}`;

  const priorityRows = sla?.by_priority ?? [];
  const priorityMax = Math.max(1, ...priorityRows.map(row => row.met + row.at_risk + row.overdue));
  const agentRows = paged(ownerRows ?? [], ownerPage, 6);
  const agentMax = Math.max(0, ...(ownerRows ?? []).map(row => row.open));
  const byState = o?.tickets.by_state ?? {};
  const ticketMax = Math.max(1, ...TICKET_STAGES.map(([key]) => byState[key as keyof typeof byState] ?? 0));
  const ages: [string, number][] = o ? [['1일 미만', o.tickets.open_age.under_1_day], ['1~3일', o.tickets.open_age.from_1_to_3_days], ['3~7일', o.tickets.open_age.from_3_to_7_days], ['7일 이상', o.tickets.open_age.over_7_days]] : [];
  const ageMax = Math.max(1, ...ages.map(([, n]) => n));
  const imp = o?.improvements ?? null;
  const firstTotal = sla ? sla.first_response.met + sla.first_response.breached : 0;
  const resolutionTotal = sla ? sla.resolution.met + sla.resolution.breached : 0;
  const recurrence = imp?.recurrence_after_completion;

  const agentColumns: LogColumn<OwnerRow>[] = [
    { key: 'name', header: '담당자', className: 't', cell: row => row.owner_user_id ? <b>{nameOf(row.owner_user_id)}</b> : <span className="unc">미배정</span> },
    { key: 'load', header: '진행 문의', width: 200, cell: row => <span className="load"><i style={{ width: `${agentMax ? Math.max(4, Math.round(100 * row.open / agentMax)) : 0}%` }} /><b>{row.open}</b></span> },
    { key: 'today', header: '오늘 기한', width: 80, className: 'c', cell: row => count(row.due_today, 'warm') },
    { key: 'over', header: '기한 초과', width: 80, className: 'c', cell: row => count(row.overdue, 'hot') },
    { key: 'closed', header: '이번 기간 종결', width: 104, className: 'c', cell: row => <span className="num">{row.closed}</span> },
    { key: 'avg', header: '평균 처리일', width: 92, className: 'c', cell: row => row.avg_handle_days === undefined ? UNKNOWN : row.avg_handle_days === null ? <span className="unc">-</span> : <span className="num">{row.avg_handle_days}일</span> },
    { key: 'req', header: '진행 개선요청', width: 104, className: 'c', cell: row => count(row.improvements_open) },
  ];
  const deptColumns: LogColumn<Dept>[] = [
    { key: 'dept', header: '부서', className: 't', cell: row => <span style={row.waiting_over_7_days ? { fontWeight: 700 } : undefined}>{deptName(row.dept_code)}</span> },
    { key: 'sent', header: '요청', width: 60, className: 'c', cell: row => <span className="num">{row.transferred}</span> },
    { key: 'answered', header: '답변', width: 60, className: 'c', cell: row => <span className="num">{row.results_submitted}</span> },
    { key: 'rate', header: '답변율', width: 140, cell: row => <span className="load"><i className="ok" style={{ width: `${pct(row.results_submitted, row.transferred)}%` }} /><b>{Math.round(pct(row.results_submitted, row.transferred))}%</b></span> },
    { key: 'accepted', header: '확인 완료', width: 76, className: 'c', cell: row => <span className="num">{row.results_accepted}</span> },
    { key: 'waiting', header: '답변 대기', width: 76, className: 'c', cell: row => count(row.waiting) },
    { key: 'late', header: '7일 넘게 대기', width: 96, className: 'c', cell: row => count(row.waiting_over_7_days, 'hot') },
    { key: 'returned', header: '반송/취소', width: 80, className: 'c', cell: row => row.returned_or_recalled ? <span className="cnt warm">{row.returned_or_recalled}</span> : <span className="num">0</span> },
    { key: 'avg', header: '평균 회신', width: 84, className: 'c', cell: row => row.avg_reply_days === undefined ? UNKNOWN : row.avg_reply_days === null ? <span className="unc">-</span> : <span className="num">{row.avg_reply_days}일</span> },
  ];
  const overdueColumns: LogColumn<OverdueRow>[] = [
    { key: 'id', header: '문의번호', width: 110, cell: row => <Link className="link" to={`/cs/tickets/${encodeURIComponent(row.ticket_id)}`}>{row.external_id ?? row.ticket_id.slice(0, 8)}</Link> },
    { key: 'title', header: '제목', className: 't', cell: row => row.subject ?? <span className="unc">조회 범위 밖</span> },
    { key: 'p', header: '우선순위', width: 80, className: 'c', cell: row => <span className={row.priority === 'P1' ? 'cnt hot' : 'cnt'}>{row.priority}</span> },
    { key: 'over', header: '초과', width: 90, className: 'c', cell: row => <b style={{ color: '#a3221a' }}>{overText(row.overdue_hours)}</b> },
    { key: 'owner', header: '담당자', width: 150, cell: row => row.owner_user_id ? nameOf(row.owner_user_id) : <span className="unc">미배정</span> },
  ];

  return <main className="td3 as3">
    <div className="title">
      <span className="path">통계/보고서 ›</span><h1>업무 통계</h1><code className="kv-code">CS0901</code>
      <div className="btns">
        <Link className="kb3" to="/cs/admin/assign">업무 배정</Link>
        <button className="kb3" onClick={() => window.print()}>인쇄</button>
        <span className="vsep" />
        <button className="kb3 pri" disabled={busy} onClick={apply}>조회<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      {mode === 'mock' && <div className="stale" role="status">모의 응답 모드 — 계약 예시이며 실제 업무 집계가 아닙니다.</div>}
      <form className="cond lc" onSubmit={event => { event.preventDefault(); apply(); }}>
        <span className="field"><span className="k">기간</span><span className="range"><input type="date" aria-label="시작일" value={draft.start} onChange={event => setDraft({ ...draft, start: event.target.value })} /><em>~</em><input type="date" aria-label="종료일 (포함)" value={draft.end} onChange={event => setDraft({ ...draft, end: event.target.value })} /></span></span>
        <label><span className="k">부서</span><select aria-label="이관 부서" value={draft.dept} onChange={event => setDraft({ ...draft, dept: event.target.value })}><option value="">전체</option>{DEPT_CODES.map(code => <option key={code} value={code}>{deptName(code)}</option>)}</select></label>
        <button type="button" className="kb3 sm clear" onClick={clear}><span aria-hidden="true">↺</span> 조건 지우기</button>
        <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
      </form>
      {error && <div className="kv-err" role="alert" style={{ padding: '6px 10px' }}>{error}</div>}
      {overview.isFetching && <Loading />}
      {!overview.isFetching && overview.isError && <ErrorState error={overview.error} onRetry={refresh} />}

      {o && <>
      {/* 1. SLA 기준 처리 현황 */}
      <section className="pane" aria-label="SLA 기준 처리 현황">
        <div className="ph">SLA 기준 처리 현황<div className="r"><span className="meta">우선순위별 첫 응답·해결 기한 기준 · 남은 시간 25% 이하면 임박{sla ? ` · 분류 없어 SLA 미확인 ${sla.unknown}건` : ''}</span></div></div>
        <div className="pb">
          <div className="kpis">
            <div className="kpi ok"><small>첫 응답 준수율</small><b>{sla && firstTotal ? `${pct(sla.first_response.met, firstTotal)}%` : '미확인'}</b><span>{sla ? `${sla.first_response.met} / ${firstTotal}건` : '집계 없음'}</span></div>
            <div className="kpi ok"><small>해결 준수율</small><b>{sla && resolutionTotal ? `${pct(sla.resolution.met, resolutionTotal)}%` : '미확인'}</b><span>{sla ? `${sla.resolution.met} / ${resolutionTotal}건` : '집계 없음'}</span></div>
            <div className="kpi warm"><small>기한 임박</small><b>{sla ? sla.at_risk : '미확인'}</b><span>남은 시간 25% 이하 · 진행 중</span></div>
            <div className="kpi hot"><small>기한 초과</small><b>{sla ? sla.overdue : '미확인'}</b><span>진행 중 · 해결 기한 지남</span></div>
          </div>
          <div className="sla-grid">
            <div>
              <p className="sub">우선순위별 (준수 · 임박 · 초과)</p>
              <div className="stack">{priorityRows.length ? priorityRows.map(row => { const total = row.met + row.at_risk + row.overdue; return <div key={row.priority} className="srow">
                <span className="lab"><b>{row.priority}</b> {PRIORITY_LABEL[row.priority] ?? ''}</span>
                <span className="bar" style={{ width: `${Math.round(100 * total / priorityMax)}%` }}><i className="met" style={{ flex: row.met }} /><i className="risk" style={{ flex: row.at_risk }} /><i className="over" style={{ flex: row.overdue }} /></span>
                <span className="nums">{row.met} · <em className="w">{row.at_risk}</em> · <em className="h">{row.overdue}</em></span>
              </div>; }) : <span className="unc">미확인</span>}</div>
              <div className="legend"><span><i className="met" />준수</span><span><i className="risk" />임박</span><span><i className="over" />초과</span></div>
            </div>
            <div>
              <p className="sub">기한 초과 문의 (오래 넘긴 순)</p>
              {sla ? <LogTable label="기한 초과 문의" columns={overdueColumns} rows={sla.overdue_tickets} rowKey={row => row.ticket_id} fallback="기한 초과 문의가 없습니다" /> : <div className="none">미확인</div>}
            </div>
          </div>
        </div>
      </section>

      <div className="cols2">
        {/* 4. 문의 처리 진행 상황 */}
        <section className="pane" aria-label="문의 처리 진행 상황">
          <div className="ph">문의 처리 진행 상황<div className="r"><span className="meta">진행 중 {o.tickets.open}건 · 이번 기간 종결 {o.tickets.closed}건</span></div></div>
          <div className="pb">
            <div className="stages">{TICKET_STAGES.map(([key, label, hint]) => { const n = byState[key as keyof typeof byState] ?? 0; return <div key={key} className="stage">
              <span className="lab">{label}<small>{hint}</small></span>
              <span className="bar"><i style={{ width: `${Math.max(3, Math.round(100 * n / ticketMax))}%` }} /></span>
              <b>{n}</b>
            </div>; })}
              <div className="stage done"><span className="lab">종결<small>이번 기간</small></span><span className="bar" /><b>{o.tickets.closed}</b></div>
            </div>
            <p className="sub" style={{ marginTop: 14 }}>진행 문의 접수 후 경과</p>
            <div className="ages">{ages.map(([label, n], index) => <div key={label} className={index === 3 ? 'late' : undefined}><span>{label}</span><b>{n}</b><i style={{ width: `${Math.max(2, Math.round(100 * n / ageMax))}%` }} aria-hidden="true" /></div>)}</div>
          </div>
        </section>

        {/* 5. 개선 요청 진행 상황 */}
        <section className="pane" aria-label="개선 요청 진행 상황">
          <div className="ph">개선 요청 진행 상황<div className="r"><span className="meta">{imp ? `진행 ${imp.open}건 · 이번 기간 완료 ${imp.completed_in_period ?? '미확인'}건` : '관리자 범위'}</span></div></div>
          {imp ? <div className="pb">
            <ol className="pipe">{REQUEST_STAGES.map(([key, label, hint]) => <li key={key}><b>{imp.by_status[key] ?? 0}</b><span>{label}</span><small>{hint}</small></li>)}
              <li className="done"><b>{imp.completed_in_period ?? '미확인'}</b><span>완료</span><small>이번 기간</small></li>
            </ol>
            <div className="side-chips">
              <span className="cnt warm">부서가 정보 요청 {imp.info_requested ?? '미확인'}</span>
              <span className="cnt">반려 {imp.rejected ?? '미확인'}</span>
              <span className="cnt hot">부서 회신 기한 초과 {imp.reply_overdue ?? '미확인'}</span>
              <span className="cnt hot">완료 후 재발 신호 {recurrence ? recurrence.recurrence : '미확인'}</span>
            </div>
          </div> : <div className="none">개선 집계는 관리자 범위에서만 제공됩니다.</div>}
        </section>
      </div>

      {/* 2. 담당자별 진행 상황 */}
      <section className="pane" aria-label="담당자별 진행 상황">
        <div className="ph">담당자별 진행 상황<div className="r"><span className="meta">진행은 현재 기준 · 종결·평균 처리일은 기간 기준</span><Link className="link" to="/cs/admin/assign">업무 배정 →</Link></div></div>
        {ownerDenied ? <div className="none">담당자별 현황은 관리자 범위에서만 제공됩니다.</div>
          : owners.isError && !owners.isFetching ? <div className="none">담당자별 현황을 불러오지 못했습니다.</div>
            : <LogTable label="담당자별 진행 상황" columns={agentColumns} rows={agentRows.shown} rowKey={row => row.owner_user_id ?? 'unassigned'} fallback={ownerRows ? '담당자 집계 없음' : '불러오는 중'} />}
        <Pager label="담당자별 진행 쪽" page={agentRows.at} pages={agentRows.pages} onPage={setOwnerPage} />
      </section>

      {/* 3. 부서별 검토 요청 상황 */}
      <section className="pane" aria-label="부서별 검토 요청 상황">
        <div className="ph">부서별 검토 요청 상황<div className="r"><span className="meta">기간 내 요청 기준 · 7일 넘게 대기한 부서 굵게</span></div></div>
        <LogTable label="부서별 검토 요청 상황" columns={deptColumns} rows={o.departments} rowKey={row => row.dept_code} fallback="이관 기록 없음" />
      </section>
      </>}
    </div>
  </main>;
}
