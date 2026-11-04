import { useState } from 'react';
import { Link } from 'react-router-dom';
import Pager, { paged } from '../components/Pager';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { DEPT_CODES, deptName } from '../ls-shell/cs-labels';
import LogTable, { type LogColumn } from './LogTable';
import './ticket-detail-v3.css';
import './list-cond.css';
import './admin-v3.css';

// 업무 통계 CS0901. 관리자가 SLA·담당자·부서·문의·개선요청 진행을 한눈에 본다.
// 미리보기: 실제 집계(SLA 기한, 부서 회신 시간 등)가 아직 없어 화면 확인용 더미 데이터로 그린다. 실제 집계 연결은 다음 단계.
export default function CsAdmin() {
  const access = useBusinessAccess('cs');
  if (!access.ready) return <BusinessGate access={access} title="업무 통계" />;
  return <StatsView />;
}

// ── 더미 데이터 ──────────────────────────────────────────────
type Priority = { p: string; label: string; met: number; risk: number; over: number };
const SLA = {
  firstResponse: { met: 412, total: 448 },
  resolution: { met: 361, total: 402 },
  risk: 23,
  over: 17,
  byPriority: [
    { p: 'P1', label: '긴급 · 설비 정지', met: 18, risk: 3, over: 2 },
    { p: 'P2', label: '높음 · 생산 영향', met: 64, risk: 7, over: 5 },
    { p: 'P3', label: '보통', met: 196, risk: 9, over: 7 },
    { p: 'P4', label: '낮음 · 일반 문의', met: 83, risk: 4, over: 3 },
  ] as Priority[],
  overdue: [
    { id: 'CSV1-003727', title: '건식 변압기 긴급 고장 · 2차 전압 이상', p: 'P1', over: '1일 4시간', owner: '가정 상담원 5' },
    { id: 'CSV1-002459', title: '권선 온도 경보로 트립', p: 'P2', over: '9시간', owner: '시연 C/S 담당자' },
    { id: 'CSV1-003882', title: '유입 변압기 보증 수리 문의', p: 'P2', over: '6시간', owner: '가정 상담원 1' },
    { id: 'CSV1-003500', title: '가스켓 부위 누유', p: 'P3', over: '2일', owner: '가정 상담원 2' },
    { id: 'CSV1-003186', title: '유효전력 음수 표시', p: 'P3', over: '1일', owner: '가정 상담원 6' },
  ],
};
type Agent = { name: string; open: number; dueToday: number; over: number; closed: number; avgDays: number; reqOpen: number };
const AGENTS: Agent[] = [
  { name: '시연 C/S 담당자', open: 18, dueToday: 3, over: 2, closed: 41, avgDays: 2.1, reqOpen: 12 },
  { name: '가정 상담원 1 (스페인어·포르투갈어)', open: 24, dueToday: 5, over: 3, closed: 37, avgDays: 2.8, reqOpen: 10 },
  { name: '가정 상담원 2 (프랑스어·이탈리아어·독일어)', open: 21, dueToday: 2, over: 1, closed: 44, avgDays: 2.3, reqOpen: 12 },
  { name: '가정 상담원 3 (태국어·베트남어·인도네시아어)', open: 29, dueToday: 6, over: 4, closed: 33, avgDays: 3.4, reqOpen: 12 },
  { name: '가정 상담원 4 (중국어·일본어)', open: 15, dueToday: 1, over: 0, closed: 39, avgDays: 1.9, reqOpen: 10 },
  { name: '가정 상담원 5 (터키어·아랍어·러시아어)', open: 27, dueToday: 4, over: 5, closed: 30, avgDays: 3.1, reqOpen: 11 },
  { name: '가정 상담원 6 (폴란드어·독일어·러시아어)', open: 19, dueToday: 2, over: 2, closed: 36, avgDays: 2.5, reqOpen: 10 },
  { name: '미배정', open: 3, dueToday: 0, over: 0, closed: 0, avgDays: 0, reqOpen: 2 },
];
type Dept = { code: string; sent: number; answered: number; accepted: number; returned: number; waiting: number; late: number; avgDays: number };
const DEPTS: Dept[] = [
  { code: 'TS_TRF', sent: 34, answered: 29, accepted: 24, returned: 2, waiting: 5, late: 2, avgDays: 2.4 },
  { code: 'TS_LV', sent: 41, answered: 38, accepted: 35, returned: 1, waiting: 3, late: 0, avgDays: 1.6 },
  { code: 'TS_MV', sent: 18, answered: 15, accepted: 13, returned: 0, waiting: 3, late: 1, avgDays: 2.9 },
  { code: 'TS_AUTO', sent: 27, answered: 25, accepted: 22, returned: 2, waiting: 2, late: 0, avgDays: 1.8 },
  { code: 'TS_ENERGY', sent: 22, answered: 17, accepted: 14, returned: 1, waiting: 5, late: 3, avgDays: 3.7 },
  { code: 'RND_FW', sent: 15, answered: 11, accepted: 9, returned: 0, waiting: 4, late: 2, avgDays: 4.2 },
  { code: 'QA_CLAIM', sent: 26, answered: 22, accepted: 18, returned: 3, waiting: 4, late: 1, avgDays: 3.0 },
  { code: 'FIELD_SVC', sent: 19, answered: 17, accepted: 16, returned: 0, waiting: 2, late: 0, avgDays: 2.2 },
  { code: 'WARRANTY_DESK', sent: 12, answered: 11, accepted: 10, returned: 1, waiting: 1, late: 0, avgDays: 1.4 },
  { code: 'SCM', sent: 9, answered: 9, accepted: 8, returned: 0, waiting: 0, late: 0, avgDays: 1.1 },
  { code: 'CERT', sent: 4, answered: 3, accepted: 3, returned: 0, waiting: 1, late: 0, avgDays: 2.6 },
  { code: 'SALES_OPS', sent: 6, answered: 6, accepted: 5, returned: 1, waiting: 0, late: 0, avgDays: 1.3 },
];
const TICKET_STAGES: [string, number, string][] = [
  ['접수', 38, '검토 시작 전'], ['검토중', 64, '담당자가 처리 중'], ['고객 회신 대기', 21, '추가 정보 요청함'],
  ['부서 검토 중', 30, '전문 부서 답변 대기'], ['답변 완료', 19, '종결 여부 확인'], ['종결', 287, '이번 기간'],
];
const TICKET_AGE: [string, number][] = [['1일 미만', 61], ['1~3일', 58], ['3~7일', 34], ['7일 이상', 19]];
const REQUEST_STAGES: [string, number, string][] = [
  ['작성 중', 14, 'C/S 검토 대기'], ['검토 완료', 6, '부서 전달 대기'], ['부서에 보냄', 9, '수신 확인 대기'],
  ['부서 확인', 7, '부서 회신 대기'], ['채택', 8, '조치 기록 대기'], ['조치 기록', 5, '후속 확인 대기'],
  ['후속 확인', 4, '재발 점검 중'], ['완료', 11, '이번 기간'],
];
const REQUEST_SIDE = { infoRequested: 3, rejected: 2, replyLate: 2, recurrence: 1 };

const pct = (met: number, total: number) => total ? Math.round(1000 * met / total) / 10 : 0;

function StatsView() {
  const [ownerPage, setOwnerPage] = useState(0);
  const [range, setRange] = useState({ start: '2026-10-01', end: '2026-10-31', dept: '' });

  const priorityMax = Math.max(...SLA.byPriority.map(row => row.met + row.risk + row.over));
  const agentRows = paged(AGENTS, ownerPage, 6);
  const agentMax = Math.max(...AGENTS.map(row => row.open));
  const deptRows = range.dept ? DEPTS.filter(row => row.code === range.dept) : DEPTS;
  const ticketMax = Math.max(...TICKET_STAGES.filter(([label]) => label !== '종결').map(([, n]) => n));
  const ticketOpen = TICKET_STAGES.filter(([label]) => label !== '종결').reduce((sum, [, n]) => sum + n, 0);
  const ageMax = Math.max(...TICKET_AGE.map(([, n]) => n));
  const requestOpen = REQUEST_STAGES.filter(([label]) => label !== '완료').reduce((sum, [, n]) => sum + n, 0);

  const agentColumns: LogColumn<Agent>[] = [
    { key: 'name', header: '담당자', className: 't', cell: row => row.name === '미배정' ? <span className="unc">미배정</span> : <b>{row.name}</b> },
    { key: 'load', header: '진행 문의', width: 200, cell: row => <span className="load"><i style={{ width: `${agentMax ? Math.max(4, Math.round(100 * row.open / agentMax)) : 0}%` }} /><b>{row.open}</b></span> },
    { key: 'today', header: '오늘 기한', width: 80, className: 'c', cell: row => row.dueToday ? <span className="cnt warm">{row.dueToday}</span> : <span className="num">0</span> },
    { key: 'over', header: '기한 초과', width: 80, className: 'c', cell: row => row.over ? <span className="cnt hot">{row.over}</span> : <span className="num">0</span> },
    { key: 'closed', header: '이번 기간 종결', width: 104, className: 'c', cell: row => <span className="num">{row.closed}</span> },
    { key: 'avg', header: '평균 처리일', width: 92, className: 'c', cell: row => row.avgDays ? <span className="num">{row.avgDays}일</span> : <span className="unc">-</span> },
    { key: 'req', header: '진행 개선요청', width: 104, className: 'c', cell: row => <span className="num">{row.reqOpen}</span> },
  ];
  const deptColumns: LogColumn<Dept>[] = [
    { key: 'dept', header: '부서', className: 't', cell: row => <span style={row.late ? { fontWeight: 700 } : undefined}>{deptName(row.code)}</span> },
    { key: 'sent', header: '요청', width: 60, className: 'c', cell: row => <span className="num">{row.sent}</span> },
    { key: 'answered', header: '답변', width: 60, className: 'c', cell: row => <span className="num">{row.answered}</span> },
    { key: 'rate', header: '답변율', width: 140, cell: row => <span className="load"><i className="ok" style={{ width: `${pct(row.answered, row.sent)}%` }} /><b>{Math.round(pct(row.answered, row.sent))}%</b></span> },
    { key: 'accepted', header: '확인 완료', width: 76, className: 'c', cell: row => <span className="num">{row.accepted}</span> },
    { key: 'waiting', header: '답변 대기', width: 76, className: 'c', cell: row => <span className="num">{row.waiting}</span> },
    { key: 'late', header: '7일 넘게 대기', width: 96, className: 'c', cell: row => row.late ? <span className="cnt hot">{row.late}</span> : <span className="num">0</span> },
    { key: 'returned', header: '반송/취소', width: 80, className: 'c', cell: row => row.returned ? <span className="cnt warm">{row.returned}</span> : <span className="num">0</span> },
    { key: 'avg', header: '평균 회신', width: 84, className: 'c', cell: row => <span className="num">{row.avgDays}일</span> },
  ];
  const overdueColumns: LogColumn<typeof SLA.overdue[number]>[] = [
    { key: 'id', header: '문의번호', width: 110, cell: row => <span className="link">{row.id}</span> },
    { key: 'title', header: '제목', className: 't', cell: row => row.title },
    { key: 'p', header: '우선순위', width: 80, className: 'c', cell: row => <span className={row.p === 'P1' ? 'cnt hot' : 'cnt'}>{row.p}</span> },
    { key: 'over', header: '초과', width: 90, className: 'c', cell: row => <b style={{ color: '#a3221a' }}>{row.over}</b> },
    { key: 'owner', header: '담당자', width: 150, cell: row => row.owner },
  ];

  return <main className="td3 as3">
    <div className="title">
      <span className="path">통계/보고서 ›</span><h1>업무 통계</h1><code className="kv-code">CS0901</code>
      <div className="btns">
        <Link className="kb3" to="/cs/admin/assign">업무 배정</Link>
        <button className="kb3" onClick={() => window.print()}>인쇄</button>
        <span className="vsep" />
        <button className="kb3 pri">조회<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      <div className="demo-note" role="status"><b>더미 데이터 미리보기</b> 화면 구성을 확인하기 위한 예시 값입니다. SLA 기한·부서 회신 시간 등 실제 집계는 연결 전입니다.</div>
      <form className="cond lc" onSubmit={event => event.preventDefault()}>
        <span className="field"><span className="k">기간</span><span className="range"><input type="date" aria-label="시작일" value={range.start} onChange={event => setRange({ ...range, start: event.target.value })} /><em>~</em><input type="date" aria-label="종료일 (포함)" value={range.end} onChange={event => setRange({ ...range, end: event.target.value })} /></span></span>
        <label><span className="k">부서</span><select aria-label="이관 부서" value={range.dept} onChange={event => setRange({ ...range, dept: event.target.value })}><option value="">전체</option>{DEPT_CODES.map(code => <option key={code} value={code}>{deptName(code)}</option>)}</select></label>
        <button type="button" className="kb3 sm clear" onClick={() => setRange({ start: '2026-10-01', end: '2026-10-31', dept: '' })}><span aria-hidden="true">↺</span> 조건 지우기</button>
      </form>

      {/* 1. SLA 기준 처리 현황 */}
      <section className="pane" aria-label="SLA 기준 처리 현황">
        <div className="ph">SLA 기준 처리 현황<div className="r"><span className="meta">우선순위별 첫 응답·해결 기한 기준 · 남은 시간 25% 이하면 임박</span></div></div>
        <div className="pb">
          <div className="kpis">
            <div className="kpi ok"><small>첫 응답 준수율</small><b>{pct(SLA.firstResponse.met, SLA.firstResponse.total)}%</b><span>{SLA.firstResponse.met} / {SLA.firstResponse.total}건</span></div>
            <div className="kpi ok"><small>해결 준수율</small><b>{pct(SLA.resolution.met, SLA.resolution.total)}%</b><span>{SLA.resolution.met} / {SLA.resolution.total}건</span></div>
            <div className="kpi warm"><small>기한 임박</small><b>{SLA.risk}</b><span>남은 시간 25% 이하 · 진행 중</span></div>
            <div className="kpi hot"><small>기한 초과</small><b>{SLA.over}</b><span>진행 중 · 해결 기한 지남</span></div>
          </div>
          <div className="sla-grid">
            <div>
              <p className="sub">우선순위별 (준수 · 임박 · 초과)</p>
              <div className="stack">{SLA.byPriority.map(row => { const total = row.met + row.risk + row.over; return <div key={row.p} className="srow">
                <span className="lab"><b>{row.p}</b> {row.label}</span>
                <span className="bar" style={{ width: `${Math.round(100 * total / priorityMax)}%` }}><i className="met" style={{ flex: row.met }} /><i className="risk" style={{ flex: row.risk }} /><i className="over" style={{ flex: row.over }} /></span>
                <span className="nums">{row.met} · <em className="w">{row.risk}</em> · <em className="h">{row.over}</em></span>
              </div>; })}</div>
              <div className="legend"><span><i className="met" />준수</span><span><i className="risk" />임박</span><span><i className="over" />초과</span></div>
            </div>
            <div>
              <p className="sub">기한 초과 문의 (오래 넘긴 순)</p>
              <LogTable label="기한 초과 문의" columns={overdueColumns} rows={SLA.overdue} rowKey={row => row.id} fallback="기한 초과 문의가 없습니다" />
            </div>
          </div>
        </div>
      </section>

      <div className="cols2">
        {/* 4. 문의 처리 진행 상황 */}
        <section className="pane" aria-label="문의 처리 진행 상황">
          <div className="ph">문의 처리 진행 상황<div className="r"><span className="meta">진행 중 {ticketOpen}건 · 이번 기간 종결 287건</span></div></div>
          <div className="pb">
            <div className="stages">{TICKET_STAGES.map(([label, n, hint]) => <div key={label} className={label === '종결' ? 'stage done' : 'stage'}>
              <span className="lab">{label}<small>{hint}</small></span>
              {label === '종결' ? <span className="bar" /> : <span className="bar"><i style={{ width: `${Math.max(3, Math.round(100 * n / ticketMax))}%` }} /></span>}
              <b>{n}</b>
            </div>)}</div>
            <p className="sub" style={{ marginTop: 14 }}>진행 문의 접수 후 경과</p>
            <div className="ages">{TICKET_AGE.map(([label, n], index) => <div key={label} className={index === 3 ? 'late' : undefined}><span>{label}</span><b>{n}</b><i style={{ width: `${Math.max(2, Math.round(100 * n / ageMax))}%` }} aria-hidden="true" /></div>)}</div>
          </div>
        </section>

        {/* 5. 개선 요청 진행 상황 */}
        <section className="pane" aria-label="개선 요청 진행 상황">
          <div className="ph">개선 요청 진행 상황<div className="r"><span className="meta">진행 {requestOpen}건 · 이번 기간 완료 11건</span></div></div>
          <div className="pb">
            <ol className="pipe">{REQUEST_STAGES.map(([label, n, hint]) => <li key={label} className={label === '완료' ? 'done' : undefined}><b>{n}</b><span>{label}</span><small>{hint}</small></li>)}</ol>
            <div className="side-chips">
              <span className="cnt warm">부서가 정보 요청 {REQUEST_SIDE.infoRequested}</span>
              <span className="cnt">반려 {REQUEST_SIDE.rejected}</span>
              <span className="cnt hot">부서 회신 기한 초과 {REQUEST_SIDE.replyLate}</span>
              <span className="cnt hot">완료 후 재발 신호 {REQUEST_SIDE.recurrence}</span>
            </div>
          </div>
        </section>
      </div>

      {/* 2. 담당자별 진행 상황 */}
      <section className="pane" aria-label="담당자별 진행 상황">
        <div className="ph">담당자별 진행 상황<div className="r"><span className="meta">진행은 현재 기준 · 종결·평균 처리일은 기간 기준</span><Link className="link" to="/cs/admin/assign">업무 배정 →</Link></div></div>
        <LogTable label="담당자별 진행 상황" columns={agentColumns} rows={agentRows.shown} rowKey={row => row.name} fallback="담당자 집계 없음" />
        <Pager label="담당자별 진행 쪽" page={agentRows.at} pages={agentRows.pages} onPage={setOwnerPage} />
      </section>

      {/* 3. 부서별 검토 요청 상황 */}
      <section className="pane" aria-label="부서별 검토 요청 상황">
        <div className="ph">부서별 검토 요청 상황<div className="r"><span className="meta">기간 내 요청 기준 · 7일 넘게 대기한 부서 굵게</span></div></div>
        <LogTable label="부서별 검토 요청 상황" columns={deptColumns} rows={deptRows} rowKey={row => row.code} fallback="이관 기록 없음" />
      </section>
    </div>
  </main>;
}
