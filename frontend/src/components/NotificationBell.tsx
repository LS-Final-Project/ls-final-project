import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { listNotifications, markNotificationRead, notificationTarget, NotificationSessionChanged, unreadCount, type NotificationItem } from '../api/notifications';
import { me } from '../api/platform';
import { reconcilePush } from '../notifications/push';
import { ErrorState, Loading } from './states';
import { formatDateTime } from './tft';
import './notification-bell.css';


export default function NotificationBell() {
  const cache = useQueryClient();
  const session = useQuery({ queryKey: ['me'], queryFn: me, retry: false });
  const [cleanupError, setCleanupError] = useState<unknown>();
  const actorId = session.data?.data.id ?? null;
  useEffect(() => {
    let current = true;
    setCleanupError(undefined);
    if (session.isSuccess) void reconcilePush(actorId).catch(error => { if (current) { setCleanupError(error); if (error instanceof NotificationSessionChanged) { cache.setQueryData(['me'], null); void cache.invalidateQueries({ queryKey: ['me'] }); } } });
    return () => { current = false; };
  }, [session.isSuccess, actorId]);
  return <>{cleanupError != null && <ErrorState error={cleanupError} />}{!session.isError && actorId && <Inbox key={actorId} actorId={actorId} />}</>;
}
function Inbox({ actorId }: { actorId: string }) {
  const cache = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [offsets, setOffsets] = useState([0]);
  const offset = offsets.at(-1)!;
  const [failure, setFailure] = useState<unknown>();
  const [failedItem, setFailedItem] = useState<NotificationItem>();
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => { alive.current = true; controller.current = new AbortController(); return () => { alive.current = false; controller.current?.abort(); }; }, []);
  const count = useQuery({ queryKey: ['notifications', actorId, 'count'], queryFn: ({ signal }) => unreadCount(signal), retry: false, refetchInterval: query => query.state.error ? false : 30000, refetchIntervalInBackground: false, refetchOnWindowFocus: query => !query.state.error });
  const list = useQuery({ queryKey: ['notifications', actorId, 'list', offset], queryFn: ({ signal }) => listNotifications(offset, signal), enabled: open, retry: false });
  async function choose(item: NotificationItem) {
    const target = notificationTarget(item);
    if (busy || !target) return;
    setBusy(true); setFailure(undefined); setFailedItem(undefined);
    try {
      await markNotificationRead(actorId, item.id, controller.current!.signal);
      if (!alive.current) return;
      void cache.invalidateQueries({ queryKey: ['notifications', actorId] });
      setOpen(false); navigate(target);
    } catch (error) {
      if (!alive.current) return;
      setFailure(error);
      if (error instanceof NotificationSessionChanged) { cache.setQueryData(['me'], null); void cache.invalidateQueries({ queryKey: ['me'] }); }
      else if (!(error instanceof ApiError && [401, 403].includes(error.status))) setFailedItem(item);
    } finally { if (alive.current) setBusy(false); }
  }
  return <div className="nb-wrap">
    <button type="button" className="kb nb-bell" aria-label={`알림${count.data && !count.isError ? `, 안 읽음 ${count.data.count}건` : ''}`} aria-expanded={open} onClick={() => setOpen(!open)}>
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9a6 6 0 0 1 12 0v6l2 3H4l2-3V9m4 12h4" fill="none" stroke="currentColor" strokeWidth="2" /></svg>
      {count.isError ? <span className="nb-err" aria-hidden="true">—</span> : <Badge count={count.data?.count ?? 0} />}
    </button>
    {open && <div className="nb-pop" role="dialog" aria-label="내 알림">
      <div className="nb-head"><b>내 알림</b>
        <button type="button" className="nb-link" disabled={list.isFetching || count.isFetching} onClick={() => { void count.refetch(); void list.refetch(); }}>알림 새로고침</button></div>
      <div className="nb-body">
        {count.isError && <ErrorState error={count.error} />}
        {list.isPending && <Loading label="알림 조회 중" />}
        {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
        {failure != null && <ErrorState error={failure} />}
        {failedItem && <button className="nb-link" onClick={() => { setOpen(false); navigate(notificationTarget(failedItem) ?? failedItem.link_path); }}>읽음 처리 없이 열기</button>}
        {list.data && !list.isError && <>
          {!list.data.items.length && <p className="nb-empty">받은 알림이 없습니다.</p>}
          <ul>{list.data.items.map(item => <li key={item.id} className={item.read_at ? 'read' : 'unread'}>
            <button type="button" aria-label={item.title} title={`${item.read_at ? '읽음' : '안 읽음'} · ${formatDateTime(item.created_at)}`} disabled={busy || !notificationTarget(item)} onClick={() => choose(item)}>
              <span className="nb-title">{!item.read_at && <i aria-hidden="true" />}{item.title}</span>
              <small>{item.read_at ? '읽음' : '안 읽음'} · {formatDateTime(item.created_at)}</small>
              {!notificationTarget(item) && <small className="nb-warn">이 알림의 이동 경로를 확인할 수 없습니다.</small>}
            </button>
          </li>)}</ul>
          {(offsets.length > 1 || list.data.next_offset !== null) && <div className="nb-foot">
            <button className="nb-link" disabled={list.isFetching || offsets.length === 1} onClick={() => setOffsets(offsets.slice(0, -1))}>알림 이전</button>
            <button className="nb-link" disabled={list.isFetching || list.data.next_offset === null} onClick={() => list.data?.next_offset !== null && list.data?.next_offset !== undefined && setOffsets([...offsets, list.data.next_offset])}>알림 다음</button>
          </div>}
        </>}
      </div>
      <div className="nb-note">앱 내 알림이 원본입니다.</div>
    </div>}
  </div>;
}

// 안 읽은 알림 배지(transitions.dev "Notification badge"). 새 알림으로 수가 늘면 대각선으로 다시 들어오며 튀어나온다.
function Badge({ count }: { count: number }) {
  const [shown, setShown] = useState(count);
  const [open, setOpen] = useState(false);
  const [pulse, setPulse] = useState(0);
  const last = useRef(0);
  useEffect(() => {
    if (count > 0) setShown(count);
    if (count > last.current) {
      // 닫힌 상태로 한 번 그린 뒤 열어야 튀어나오는 전환이 다시 돈다.
      setOpen(false); setPulse(value => value + 1);
      const frame = requestAnimationFrame(() => requestAnimationFrame(() => setOpen(true)));
      last.current = count;
      return () => cancelAnimationFrame(frame);
    }
    last.current = count;
    setOpen(count > 0);
    return undefined;
  }, [count]);
  return <span key={pulse} className="t-badge" data-open={open ? 'true' : 'false'} aria-hidden="true">
    <span className="t-badge-dot">{shown > 99 ? '99+' : shown}</span>
  </span>;
}
