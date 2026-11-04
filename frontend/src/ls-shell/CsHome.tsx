import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { useHotkey } from './kv';
import { CHANNEL_LABEL, REQUEST_STATUS_LABEL, formatStamp, type TicketState } from './cs-labels';
import { getApiMode } from '../api/client';
import { getTicketAnalysis, listTicketsBy, type TicketAnalysisResponse, type TicketListResponse } from '../api/cs';
import { me } from '../api/platform';
import { listMyImprovements } from '../api/cs-improvements';
import '../pages/ticket-detail-v3.css';
import './cs-home.css';

type Ticket = TicketListResponse['data']['items'][number];
const DAY = 86_400_000;
const LIMIT = '100';
// 기한·긴급·처리 준비를 읽어 올 최대 문의 수(내 담당 먼저, 다음 새 문의).
const SIGNAL_LIMIT = 10;
// 내가 움직여야 하는 문의 상태와 그때 할 일. 부서·고객 답을 기다리는 상태와 종결은 빠진다.
const NEXT_ACTION: Partial<Record<TicketState, string>> = { RECEIVED: '검토 시작', IN_REVIEW: '답변·부서 검토·정보 요청', ANSWERED: '종결 여부 확인' };
// 개선요청에서 C/S 담당자가 할 일. 급한 순서(앞일수록 먼저).
const REQUEST_TASK: [string, string][] = [
  ['REJECTED', '반려 확인'], ['INFO_REQUESTED', '자료 보완 후 다시 요청'], ['REVIEWED', '부서 전달'],
  ['ACTION_TAKEN', '후속 확인 계획'], ['FOLLOW_UP', '후속 확인 결과·완료'], ['DRAFT', '검토 완료 기록'],
];
const taskOf = new Map(REQUEST_TASK);
const rank = new Map(REQUEST_TASK.map(([status], index) => [status, index]));
const countText = (n: number, more: boolean) => `${n}${more ? '+' : ''}`;
// 개선요청 목록 API가 한 번에 주는 최대 건수(backend LIST_LIMIT). 꽉 차면 더 있을 수 있다.
const REQUEST_PAGE = 200;

// 업무 홈 CS0001. 출근해서 처음 여는 화면: 오늘 내가 할 일을 한눈에 보인다.
// 건수는 서버 필터(상태·담당자)로 센다. 한 번에 최대 100건까지 받고 넘으면 '+'를 붙인다.
export default function CsHome() {
  const access = useBusinessAccess('cs');
  if (!access.ready) return <BusinessGate access={access} title="업무 홈" />;
  return <HomeView />;
}

function HomeView() {
  const mode = getApiMode();
  const queryClient = useQueryClient();
  const identity = useQuery({ queryKey: ['cs-home', mode, 'me'], queryFn: me, retry: false, staleTime: 60_000 });
  const myId = identity.data?.data?.id ?? null;
  const fresh = useQuery({ queryKey: ['cs-home', mode, 'new'], queryFn: ({ signal }) => listTicketsBy({ state: 'RECEIVED', sort: 'received_at_asc', limit: LIMIT }, signal), retry: false, staleTime: 0 });
  const mine = useQuery({ queryKey: ['cs-home', mode, 'mine', myId], queryFn: ({ signal }) => listTicketsBy({ state: 'IN_REVIEW,ANSWERED', owner: myId!, sort: 'received_at_asc', limit: LIMIT }, signal), enabled: !!myId, retry: false, staleTime: 0 });
  const waiting = useQuery({ queryKey: ['cs-home', mode, 'waiting', myId], queryFn: ({ signal }) => listTicketsBy({ state: 'TRANSFERRED,WAITING_CUSTOMER', owner: myId!, limit: LIMIT }, signal), enabled: !!myId, retry: false, staleTime: 0 });
  // 개선요청도 내 담당(cs_owner)만. 팀 전체 요청은 개선요청 화면에서 본다(KAN-214 자동 배정 뒤).
  const requests = useQuery({ queryKey: ['cs-home', mode, 'requests', myId], queryFn: ({ signal }) => listMyImprovements(myId!, signal), enabled: !!myId, retry: false, staleTime: 0 });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['cs-home', mode] });
  useHotkey('F8', refresh);

  const now = Date.now();
  const ageOf = (item: Ticket) => Math.max(0, Math.floor((now - Date.parse(item.received_at)) / DAY));
  const newItems = fresh.data?.data.items ?? [];
  const mineItems = mine.data?.data.items ?? [];
  const waitItems = waiting.data?.data.items ?? [];
  // 기한·긴급·처리 준비(건별 분석). 로컬 서버가 몰린 요청에 500을 내서 2건씩 차례로 읽는다.
  const candidates = [...mineItems, ...newItems].slice(0, SIGNAL_LIMIT);
  const ids = candidates.map(item => item.id);
  const analyses = useQuery({ queryKey: ['cs-home', mode, 'analysis', ids.join(',')], enabled: ids.length > 0, retry: false, staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const out: Record<string, { analysis?: TicketAnalysisResponse }> = {};
      for (let i = 0; i < ids.length; i += 2) await Promise.all(ids.slice(i, i + 2).map(async id => {
        out[id] = { analysis: await getTicketAnalysis(id, signal).catch(() => undefined) };
      }));
      return out;
    } });
  // 한국어 제목은 문의 목록 응답에 같이 온다(KAN-213 접수 때 번역). 없으면 원문 제목을 보인다.
  const koById = new Map([...newItems, ...mineItems, ...waitItems].map(item => [item.id, item.subject_ko ?? null]));
  const koOf = (id: string) => koById.get(id) || null;
  const signalOf = new Map(candidates.map(item => [item.id, signals(analyses.data?.[item.id]?.analysis, now)]));
  // 정렬: 긴급(P1) → 기한 가까운 순 → 오래된 순. 신호가 없는 건은 뒤로.
  const urgency = (item: Ticket) => { const sig = signalOf.get(item.id); return sig ? (sig.priority === 'P1' ? -1000 : 0) + sig.dueDays : 1000; };
  const todo = [...mineItems, ...newItems].sort((a, b) => urgency(a) - urgency(b) || ageOf(b) - ageOf(a));
  const sigs = [...signalOf.values()].filter((sig): sig is Signal => !!sig);
  const overdue = sigs.filter(sig => sig.dueDays < 0).length;
  const dueToday = sigs.filter(sig => sig.dueDays === 0).length;
  const p1 = sigs.filter(sig => sig.priority === 'P1').length;
  const reqItems = requests.data?.data.items ?? [];
  // 서버가 담당으로 걸렀지만, 화면도 내 담당만 센다(다른 사람 요청이 섞이지 않게).
  const reqTodo = reqItems
    .filter(item => item.cs_owner === myId && item.request_type === 'FAULT' && taskOf.has(item.status))
    .sort((a, b) => (rank.get(a.status)! - rank.get(b.status)!) || Date.parse(a.created_at) - Date.parse(b.created_at));
  const reqMore = reqItems.length >= REQUEST_PAGE;
  // 초안(DRAFT)도 내가 검토 완료를 기록할 일이라 오늘 할 일에 센다.
  const reqDraft = reqTodo.filter(item => item.status === 'DRAFT').length;
  const name = identity.data?.data?.display_name;
  const today = new Date().toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
  const total = todo.length + reqTodo.length;
  // 개선요청을 못 읽었으면 합계에서 그만큼 빠졌다는 것을 숨기지 않는다.
  const totalMore = !!(fresh.data?.data.next_cursor || mine.data?.data.next_cursor) || reqMore || requests.isError;
  const failed = fresh.isError ? fresh.error : mine.isError ? mine.error : null;

  // 회신 도착: 부서 회신이나 고객 답장이 와서 다시 내 차례가 된 내 담당 문의.
  const focus = todo[0];
  const focusSig = focus ? signalOf.get(focus.id) : undefined;
  const queue = todo.slice(1, 4);
  const actionOf = (item: Ticket) => NEXT_ACTION[item.workflow_state] ?? '';
  const label = (item: Ticket) => item.external_id ?? item.id.slice(0, 8);
  const loadingTodo = fresh.isPending || (!!myId && mine.isPending);
  // 부서 검토·회신: 내 개선요청 할 일 + 부서 답을 기다리는 문의.
  const REQ_TONE: Record<string, string> = { REJECTED: 'red', INFO_REQUESTED: 'amber', REVIEWED: 'blue', ACTION_TAKEN: 'green', FOLLOW_UP: 'green', DRAFT: 'gray' };
  const deptRows = [
    ...reqTodo.map(item => ({ id: item.id, to: `/cs/improvements/${encodeURIComponent(item.id)}`, chip: taskOf.get(item.status)!, tone: REQ_TONE[item.status] ?? 'gray',
      title: `${item.product_code}${item.fault_id ? ` · ${item.fault_id}` : ''}`, sub: `개선요청 · ${REQUEST_STATUS_LABEL[item.status] ?? item.status} · ${formatStamp(item.created_at)}` })),
    ...waitItems.filter(item => item.workflow_state === 'TRANSFERRED').map(item => ({ id: item.id, to: `/cs/tickets/${item.id}`, chip: '부서 회신 대기', tone: 'blue',
      title: koOf(item.id) ?? item.subject ?? '(제목 없음)', sub: `${label(item)} · 부서 답이 오면 내 차례` })),
  ];

  return <main className="td3 home3">
    <div className="title">
      <span className="path">C/S 관리 ›</span><h1>업무 홈</h1><code className="kv-code">CS0001</code>
      <div className="btns">
        <button className="kb3 pri" disabled={fresh.isFetching || mine.isFetching} onClick={refresh}>새로고침<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      <div className="hello">
        <div><small>{today}</small><h2>{name ? `${name}님, ` : ''}오늘 할 일 <b>{fresh.isPending || (myId && mine.isPending) ? '…' : `${total}${totalMore ? '+' : ''}건`}</b></h2>{requests.isError && <small className="req-miss" role="status">개선요청을 불러오지 못해 합계에서 빠졌습니다</small>}</div>
        {(overdue + dueToday + p1) > 0 && <span className="late">{[overdue && `기한 넘긴 ${overdue}건`, dueToday && `오늘 기한 ${dueToday}건`, p1 && `긴급 ${p1}건`].filter(Boolean).join(' · ')}부터 처리하세요</span>}
      </div>
      {(identity.isError || (identity.isSuccess && !myId)) && <div className="stale">로그인 정보를 확인하지 못해 내 담당 문의는 비워 둡니다. 다시 로그인하세요.</div>}
      {failed && <ErrorState error={failed} onRetry={refresh} />}

      {!failed && <div className="cards">
        <Link className="card hot" to="/cs/tickets">
          <small>새로 접수된 문의</small><b>{fresh.isPending ? '…' : countText(newItems.length, !!fresh.data?.data.next_cursor)}</b>
          <span>{newItems.length ? `가장 오래된 건 ${ageOf(newItems[0])}일째 · 검토 시작` : '검토할 새 문의 없음'}</span>
        </Link>
        <Link className="card" to="/cs/tickets">
          <small>내가 처리 중인 문의</small><b>{!myId ? '-' : mine.isPending ? '…' : countText(mineItems.length, !!mine.data?.data.next_cursor)}</b>
          <span>{mineItems.filter(item => item.workflow_state === 'ANSWERED').length}건은 종결 여부만 확인</span>
        </Link>
        <Link className="card calm" to="/cs/tickets">
          <small>답을 기다리는 문의</small><b>{!myId ? '-' : waiting.isPending ? '…' : waiting.isError ? '!' : countText(waitItems.length, !!waiting.data?.data.next_cursor)}<em>건</em></b>
          <span>{waiting.isError ? '대기 목록을 불러오지 못함 · 새로고침하세요' : <>부서 {waitItems.filter(item => item.workflow_state === 'TRANSFERRED').length} · 고객 {waitItems.filter(item => item.workflow_state === 'WAITING_CUSTOMER').length} · 답이 오면 다시 내 차례</>}</span>
        </Link>
        <Link className="card req" to="/cs/improvements">
          <small>내 개선요청 할 일</small><b>{!myId ? '-' : requests.isPending ? '…' : requests.isError ? '!' : countText(reqTodo.length, reqMore)}</b>
          <span>{requests.isError ? '불러오지 못함 · 합계에서 빠짐' : `내 담당만 · 초안 검토 ${reqDraft}건 포함`}</span>
        </Link>
      </div>}

      {!failed && <div className="home-cols">
        <div className="pane focus-pane">
          <div className="ph"><h2>지금 할 일</h2><div className="r"><span className="meta">{loadingTodo ? '' : `처리할 문의 ${countText(todo.length, totalMore)}건 중 1순위`}</span></div></div>
          {loadingTodo ? <Loading /> : !focus ? <div className="pb none">지금 처리할 문의가 없습니다.</div> : <div className="hm-focus">
            <div className="hm-main">
              <div className="hm-tags">
                {focusSig?.priority === 'P1' && <span className="flag p1">긴급 P1</span>}
                <span className={focus.workflow_state === 'RECEIVED' ? 'chip st-new' : 'chip st-open'}>{actionOf(focus)}</span>
              </div>
              <h3 title={focus.subject ? `원문: ${focus.subject}` : ''}>{koOf(focus.id) ?? <><span className="flag raw">번역 전</span>{focus.subject || '(제목 없음)'}</>}</h3>
              <p className="hm-meta">{label(focus)} · {CHANNEL_LABEL[focus.channel as keyof typeof CHANNEL_LABEL] ?? focus.channel} · {focus.product_code}{focusSig && <> · <span className={`due ${focusSig.dueDays < 0 ? 'over' : focusSig.dueDays === 0 ? 'today' : ''}`}>기한 {dueText(focusSig.dueDays)}</span></>}</p>
              <div className="hm-act">
                <div className="hm-next"><small>할 일</small>{focus.workflow_state === 'RECEIVED' ? '내용을 확인하고 검토 시작' : focus.workflow_state === 'ANSWERED' ? '고객 답변 뒤 종결해도 되는지 확인' : '답변·부서 검토·추가 정보 요청 중 고르기'}{focusSig && focusSig.ready.tone !== 'idle' && <span className={`ready ${focusSig.ready.tone}`}>{focusSig.ready.text}</span>}</div>
                <Link className="hm-go" to={`/cs/tickets/${focus.id}`}>문의 처리 <span aria-hidden="true">→</span></Link>
              </div>
            </div>
            <div className="hm-queue">
              <small>다음 순서</small>
              {queue.map((item, index) => { const sig = signalOf.get(item.id); return <Link key={item.id} className="hm-q" to={`/cs/tickets/${item.id}`}>
                <span className="n">{index + 2}</span>
                <span className="b"><b>{koOf(item.id) ?? item.subject ?? '(제목 없음)'}</b><em>{[sig?.priority === 'P1' ? '긴급 P1' : null, actionOf(item), sig ? `기한 ${dueText(sig.dueDays)}` : `${ageOf(item)}일째`].filter(Boolean).join(' · ')}</em></span>
              </Link>; })}
              {todo.length > 4 && <Link className="hm-q" to="/cs/tickets"><span className="n">+{todo.length - 4}</span><span className="b"><b>나머지 문의</b><em>문의 조회에서 보기</em></span></Link>}
              {queue.length === 0 && <em className="none">다음 문의 없음</em>}
            </div>
          </div>}
        </div>
        <div className="pane">
          <div className="ph"><h2>부서 검토·회신</h2><div className="r"><span className="meta">내 담당 {deptRows.length}건</span><Link className="link" to="/cs/improvements">모두 보기</Link></div></div>
          {!myId ? <div className="pb none">로그인 정보를 확인하지 못했습니다.</div> : requests.isPending ? <Loading /> : requests.isError ? <div className="pb"><ErrorState error={requests.error} onRetry={() => void requests.refetch()} /></div>
            : deptRows.length === 0 ? <div className="pb none">부서와 주고받을 일이 없습니다.</div>
            : <ul className="hm-dept">{deptRows.slice(0, 4).map(row => <li key={row.id}><Link to={row.to}>
              <span className={`hm-chip ${row.tone}`}><i aria-hidden="true" />{row.chip}</span>
              <span className="b"><b>{row.title}</b><em>{row.sub}</em></span>
              <span className="go">처리 →</span>
            </Link></li>)}</ul>}
        </div>
      </div>}
    </div>
  </main>;
}

type Signal = { priority: string | null; dueDays: number; ready: { text: string; tone: string } };
const dueText = (days: number) => days < 0 ? `${-days}일 넘김` : days === 0 ? '오늘' : `D-${days}`;
const startOfDay = (t: number) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };

// 저장된 AI 분석에서 우선순위·기한·처리 준비를 뽑는다. 분석이나 기한이 없으면 신호 없음(값을 지어내지 않는다).
function signals(analysis: TicketAnalysisResponse | undefined, now: number): Signal | undefined {
  const data = analysis?.data as unknown as { latest_run?: unknown; latest_valid_draft_id?: string | null; missing_info?: { effective?: { blocking_missing?: string[] } | null }; rules?: { effective?: { priority?: string } | null; resolution_due?: { effective?: string | null } | null; first_response_due?: { effective?: string | null } | null } | null } | undefined;
  const due = data?.rules?.resolution_due?.effective ?? data?.rules?.first_response_due?.effective ?? null;
  if (!data?.latest_run || !due) return undefined;
  const blocking = data.missing_info?.effective?.blocking_missing?.length ?? 0;
  return { priority: data.rules?.effective?.priority ?? null, dueDays: Math.floor((startOfDay(Date.parse(due)) - startOfDay(now)) / DAY),
    ready: data.latest_valid_draft_id ? { text: '답변 초안 있음', tone: 'ok' } : blocking ? { text: '정보 부족', tone: 'warn' } : { text: 'AI 분석 끝', tone: 'ok' } };
}
