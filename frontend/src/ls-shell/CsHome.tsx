import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvGrid, KvTitle, useHotkey, KvKey, type KvColumn } from './kv';
import { STATE_LABEL, dayOf, type TicketState } from './cs-labels';
import { useTicketPages } from './use-tickets';
import type { TicketListResponse } from '../api/cs';

type Ticket = TicketListResponse['data']['items'][number];
const DAY = 86_400_000;
const STEPS: [string, TicketState | null][] = [['접수', 'RECEIVED'], ['검토중', 'IN_REVIEW'], ['부서 검토', 'TRANSFERRED'], ['고객 회신 대기', 'WAITING_CUSTOMER'], ['오늘 종결', null]];
const TERMS: [string, string][] = [
  ['내 차례', '다음 행동을 내가 해야 하는 문의. 부서나 고객 답을 기다리는 건은 빠집니다.'],
  ['경과일', '접수된 날부터 오늘까지 며칠 지났는지. 7일이 넘으면 먼저 봅니다.'],
  ['부서 검토', '기술 판단이 필요해 담당 부서에 넘긴 상태. 답변이 오면 다시 내 차례가 됩니다.'],
  ['고객 회신 대기', '고객에게 정보를 더 물어 둔 상태. 고객 답장이 오면 자동으로 돌아옵니다.'],
  ['개선요청', '단순 문의가 아니라 제품이나 절차를 고쳐 달라는 건. 번호가 IR로 시작합니다.'],
  ['반복 사례 모음', '비슷한 문의와 그때의 답변을 모아 둔 곳. 답변 초안의 근거로 씁니다.'],
];
// 부서나 고객의 답을 기다리는 상태(TRANSFERRED, WAITING_CUSTOMER)와 종결은 "내 차례"에서 뺀다.
const NEXT_ACTION: Partial<Record<TicketState, string>> = { RECEIVED: '검토 시작', IN_REVIEW: '부서 검토 요청 또는 고객 정보 요청', ANSWERED: '종결 여부 확인' };
const PENDING = '연결 전입니다. 처리 기록 API가 연결되면 표시됩니다.';

// 업무 홈 CS0001. 건수와 처리할 문의는 문의 목록(받아 온 쪽)에서 센다. 담당자·종결 시각·처리 기록·공지 API는 아직 없다.
export default function CsHome() {
  const access = useBusinessAccess('cs');
  const queryClient = useQueryClient();
  const tickets = useTicketPages(access.ready);
  const refresh = () => void queryClient.invalidateQueries();
  useHotkey('F8', refresh, access.ready);
  if (!access.ready) return <BusinessGate access={access} title="업무 홈" />;

  const ok = tickets.isSuccess && !tickets.isError;
  const all: Ticket[] = ok ? tickets.data.pages.flatMap(page => page.data.items) : [];
  const now = Date.now();
  const ageOf = (item: Ticket) => Math.max(0, Math.floor((now - Date.parse(item.received_at)) / DAY));
  const count = (state: TicketState) => all.filter(item => item.workflow_state === state).length;
  const stale = all.filter(item => item.workflow_state !== 'CLOSED' && ageOf(item) >= 7).length;
  const today = dayOf(new Date().toISOString());
  const todayCount = all.filter(item => dayOf(item.received_at) === today).length;
  const mine = all.filter(item => NEXT_ACTION[item.workflow_state]).sort((a, b) => ageOf(b) - ageOf(a));

  const columns: KvColumn<Ticket>[] = [
    { key: 'pick', header: <input type="checkbox" aria-label="모두 선택" disabled />, width: 30, className: 'c', cell: item => <input type="checkbox" aria-label={`${item.external_id ?? item.id.slice(0, 8)} 선택`} disabled /> },
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_item, index) => index + 1 },
    { key: 'type', header: '유형', width: 64, className: 'c', cell: () => '문의' },
    { key: 'id', header: '번호', width: 96, className: 'id', headClass: '', cell: item => <Link to={`/cs/tickets/${item.id}`}>{item.external_id ?? item.id.slice(0, 8)}</Link> },
    { key: 'next', header: '다음 행동', width: 220, headClass: '', cell: item => NEXT_ACTION[item.workflow_state] },
    { key: 'age', header: '경과일', width: 56, className: 'c', sort: 'descending', cell: item => ageOf(item) },
    { key: 'state', header: '상태', width: 100, className: 'c', cell: item => STATE_LABEL[item.workflow_state] },
  ];

  return <main>
    <KvTitle path="C/S 관리" title="업무 홈" code="CS0001">
      <button className="kb primary" disabled={tickets.isFetching} onClick={refresh}>조회<KvKey>F8</KvKey></button>
      <Link className="kb" to="/cs/tickets">문의 조회</Link>
      <button className="kb" onClick={() => window.print()}>인쇄</button>
    </KvTitle>
    <div className="kv-next">
      <span className="k">다음 할 일</span>
      {ok
        ? <><span>다음 <b>접수 {count('RECEIVED')}건 검토 시작</b>, 7일 이상 미처리 <b>{stale}</b></span><span className="sep">|</span><span><span className="m">받아 온 {all.length}건 기준, 오늘 접수 {todayCount}. 담당 구분과 요약·분류는 아직 없음</span></span></>
        : <span>{tickets.isPending ? '불러오는 중' : '문의 목록을 불러오지 못했습니다.'}</span>}
    </div>
    <div className="kv-split" style={{ gridTemplateColumns: '1fr 1.15fr' }}>
      <section className="kv-box">
        <h2>업무 개요<small>팀 전체, 받아 온 목록을 상태별로 센 것</small></h2>
        <div className="body" style={{ padding: 0 }}>
          <div className="kv-steps" style={{ margin: 0, border: 0, borderBottom: '1px solid var(--erp-border-soft)' }}>
            {STEPS.map(([label, state]) => <span key={label} title={state ? undefined : '종결 시각 API가 아직 없습니다'} style={{ height: 30, lineHeight: '30px' }}>{label} <b style={{ fontWeight: 500, fontSize: 14 }}>{ok && state ? count(state) : '-'}</b></span>)}
          </div>
          <div style={{ padding: '8px 10px', fontSize: '12.5px', lineHeight: 1.55, color: 'var(--erp-text)' }}>
            메일과 전화로 들어온 문의가 접수되면 AI가 요약과 분류를 먼저 붙입니다. 상담원이 내용을 확인하고, 필요하면 부서에 검토를 요청하거나 고객에게 정보를 더 묻고, 답변을 보내면 종결됩니다. 아래 표에는 그중 내가 움직여야 하는 건만 모여 있습니다.
          </div>
        </div>
      </section>
      <section className="kv-box">
        <h2>용어 풀이</h2>
        <div className="body" style={{ padding: '6px 10px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 14px' }}>
          {TERMS.map(([term, text]) => <div key={term} className="kv-field" style={{ gridTemplateColumns: '84px 1fr', marginTop: 0, alignItems: 'start', padding: '3px 0', borderBottom: '1px solid var(--erp-border-soft)' }}>
            <span>{term}</span><div style={{ lineHeight: 1.45 }}>{text}</div>
          </div>)}
        </div>
      </section>
    </div>
    <div className="kv-gridhead"><span>{ok ? `처리할 문의 ${mine.length}건, 경과일 내림차순. 담당 구분 API가 없어 팀 전체` : '처리할 문의'}</span></div>
    {tickets.isPending && <Loading />}
    {tickets.isError && <ErrorState error={tickets.error} onRetry={() => void tickets.refetch()} />}
    {ok && <KvGrid label="처리할 문의" frozen columns={columns} rows={mine} rowKey={item => item.id} fallback="지금 처리할 문의가 없습니다." />}
    <div className="kv-split" style={{ marginTop: 12 }}>
      <div>
        <div className="kv-sub" style={{ margin: '0 0 6px' }}>최근 처리 기록, 오늘</div>
        <div className="kv-tablewrap"><table className="kv-grid">
          <colgroup><col style={{ width: 56 }} /><col style={{ width: 84 }} /><col /><col style={{ width: 64 }} /></colgroup>
          <thead><tr><th>일시</th><th>번호</th><th>내용</th><th>처리자</th></tr></thead>
          <tbody><tr><td colSpan={4}>{PENDING}</td></tr></tbody>
        </table></div>
      </div>
      <div>
        <div className="kv-sub" style={{ margin: '0 0 6px' }}>공지</div>
        <div className="kv-tablewrap"><table className="kv-grid">
          <colgroup><col style={{ width: 56 }} /><col /></colgroup>
          <thead><tr><th>날짜</th><th>내용</th></tr></thead>
          <tbody><tr><td colSpan={2}>공지 API가 아직 없습니다.</td></tr></tbody>
        </table></div>
      </div>
    </div>
  </main>;
}
