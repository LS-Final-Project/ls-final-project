import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { useHotkey } from './kv';
import { STATE_LABEL, formatStamp, type TicketState } from './cs-labels';
import { getApiMode } from '../api/client';
import { listTicketsBy, type TicketListResponse } from '../api/cs';
import { me } from '../api/platform';
import { listImprovements } from '../api/cs-improvements';
import '../pages/ticket-detail-v3.css';
import './cs-home.css';

type Ticket = TicketListResponse['data']['items'][number];
const DAY = 86_400_000;
const LIMIT = '100';
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
  const requests = useQuery({ queryKey: ['cs-home', mode, 'requests'], queryFn: ({ signal }) => listImprovements(signal), retry: false, staleTime: 0 });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['cs-home', mode] });
  useHotkey('F8', refresh);

  const now = Date.now();
  const ageOf = (item: Ticket) => Math.max(0, Math.floor((now - Date.parse(item.received_at)) / DAY));
  const newItems = fresh.data?.data.items ?? [];
  const mineItems = mine.data?.data.items ?? [];
  const waitItems = waiting.data?.data.items ?? [];
  const todo = [...mineItems, ...newItems].sort((a, b) => ageOf(b) - ageOf(a));
  const late = todo.filter(item => ageOf(item) >= 7).length;
  const reqTodo = (requests.data?.data.items ?? [])
    .filter(item => item.request_type === 'FAULT' && taskOf.has(item.status))
    .sort((a, b) => (rank.get(a.status)! - rank.get(b.status)!) || Date.parse(a.created_at) - Date.parse(b.created_at));
  const reqUrgent = reqTodo.filter(item => item.status !== 'DRAFT').length;
  const reqDraft = reqTodo.length - reqUrgent;
  const name = identity.data?.data?.display_name;
  const today = new Date().toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
  const total = todo.length + reqUrgent;
  const failed = fresh.isError ? fresh.error : mine.isError ? mine.error : null;

  return <main className="td3 home3">
    <div className="title">
      <span className="path">C/S 관리 ›</span><h1>업무 홈</h1><code className="kv-code">CS0001</code>
      <div className="btns">
        <Link className="kb3" to="/cs/tickets">문의 조회</Link>
        <Link className="kb3" to="/cs/improvements">개선요청</Link>
        <span className="vsep" />
        <button className="kb3 pri" disabled={fresh.isFetching || mine.isFetching} onClick={refresh}>새로고침<kbd aria-hidden="true">F8</kbd></button>
      </div>
    </div>

    <div className="body">
      <div className="hello">
        <div><small>{today}</small><h2>{name ? `${name}님, ` : ''}오늘 할 일 <b>{fresh.isPending || (myId && mine.isPending) ? '…' : `${total}${fresh.data?.data.next_cursor || mine.data?.data.next_cursor ? '+' : ''}건`}</b></h2></div>
        {late > 0 && <span className="late">7일 넘은 문의 {late}건부터 처리하세요</span>}
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
          <small>답을 기다리는 문의</small><b>{!myId ? '-' : waiting.isPending ? '…' : waiting.isError ? '!' : countText(waitItems.length, !!waiting.data?.data.next_cursor)}</b>
          <span>{waiting.isError ? '불러오지 못함 · 새로고침하세요' : <>부서 {waitItems.filter(item => item.workflow_state === 'TRANSFERRED').length} · 고객 {waitItems.filter(item => item.workflow_state === 'WAITING_CUSTOMER').length} · 답이 오면 다시 내 차례</>}</span>
        </Link>
        <Link className="card req" to="/cs/improvements">
          <small>개선요청 할 일</small><b>{requests.isPending ? '…' : reqUrgent}</b>
          <span>{requests.isError ? '불러오지 못함' : `검토 기다리는 초안 ${reqDraft}건 별도`}</span>
        </Link>
      </div>}

      {!failed && <div className="home-cols">
        <div className="pane">
          <div className="ph"><h2>오늘 처리할 문의</h2><div className="r"><span className="meta">오래된 순 · 내 담당 + 새 문의</span><Link className="link" to="/cs/tickets">모두 보기</Link></div></div>
          {(fresh.isPending || (myId && mine.isPending)) ? <Loading /> : <div className="logwrap"><table className="log" aria-label="처리할 문의">
            <thead><tr><th style={{ width: 110 }}>문의</th><th>제목</th><th style={{ width: 110 }} className="c">상태</th><th style={{ width: 170 }}>할 일</th><th style={{ width: 70 }} className="c">경과</th></tr></thead>
            <tbody>{todo.length === 0 ? <tr><td colSpan={5} className="none">지금 처리할 문의가 없습니다.</td></tr> : todo.slice(0, 10).map(item => <tr key={item.id}>
              <td><Link className="link" to={`/cs/tickets/${item.id}`}>{item.external_id ?? item.id.slice(0, 8)}</Link></td>
              <td className="t" title={item.subject ?? ''}>{item.subject || '(제목 없음)'}</td>
              <td className="c"><span className={item.workflow_state === 'RECEIVED' ? 'chip st-new' : 'chip st-open'}>{STATE_LABEL[item.workflow_state]}</span></td>
              <td>{NEXT_ACTION[item.workflow_state]}</td>
              <td className={ageOf(item) >= 7 ? 'c age late' : 'c age'}>{ageOf(item)}일</td>
            </tr>)}</tbody>
          </table></div>}
        </div>
        <div className="pane">
          <div className="ph"><h2>개선요청 할 일</h2><div className="r"><Link className="link" to="/cs/improvements">모두 보기</Link></div></div>
          {requests.isPending ? <Loading /> : requests.isError ? <div className="pb"><ErrorState error={requests.error} onRetry={() => void requests.refetch()} /></div>
            : <ul className="req-list">{reqTodo.length === 0 ? <li className="none">할 일이 없습니다.</li> : reqTodo.slice(0, 7).map(item => <li key={item.id}>
              <Link to={`/cs/improvements/${encodeURIComponent(item.id)}`}><b>{item.product_code}{item.fault_id ? ` · ${item.fault_id}` : ''}</b><small>{formatStamp(item.created_at)}</small></Link>
              <span className={item.status === 'DRAFT' ? 'task draft' : 'task'}>{taskOf.get(item.status)}</span>
            </li>)}</ul>}
        </div>
      </div>}
      {waitItems.length > 0 && <p className="meta wait-note">답을 기다리는 문의: {waitItems.slice(0, 5).map(item => `${item.external_id ?? item.id.slice(0, 8)}(${item.workflow_state === 'TRANSFERRED' ? '부서' : '고객'})`).join(', ')}{waitItems.length > 5 ? ' 외' : ''}. 답이 오면 위 목록에 다시 올라옵니다.</p>}
    </div>
  </main>;
}
