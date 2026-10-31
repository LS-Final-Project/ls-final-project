import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { listNotifications, markNotificationRead, notificationPath, NotificationSessionChanged, unreadCount, type NotificationItem } from '../api/notifications';
import { me } from '../api/platform';
import { reconcilePush } from '../notifications/push';
import { KvBox } from '../ls-shell/kv';
import { ErrorState, Loading } from './states';
import { formatDateTime } from './tft';

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
    if (busy || !notificationPath(item.link_path)) return;
    setBusy(true); setFailure(undefined); setFailedItem(undefined);
    try {
      await markNotificationRead(actorId, item.id, controller.current!.signal);
      if (!alive.current) return;
      void cache.invalidateQueries({ queryKey: ['notifications', actorId] });
      setOpen(false); navigate(item.link_path);
    } catch (error) {
      if (!alive.current) return;
      setFailure(error);
      if (error instanceof NotificationSessionChanged) { cache.setQueryData(['me'], null); void cache.invalidateQueries({ queryKey: ['me'] }); }
      else if (!(error instanceof ApiError && [401, 403].includes(error.status))) setFailedItem(item);
    } finally { if (alive.current) setBusy(false); }
  }
  return <div style={{ position: 'relative' }}>
    <button type="button" className="kb" aria-label={`알림${count.data && !count.isError ? `, 안 읽음 ${count.data.count}건` : ''}`} aria-expanded={open} onClick={() => setOpen(!open)}>
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9a6 6 0 0 1 12 0v6l2 3H4l2-3V9m4 12h4" fill="none" stroke="currentColor" strokeWidth="2" /></svg>{' '}{!count.isError && count.data ? count.data.count : '—'}
    </button>
    {open && <div style={{ position: 'absolute', right: 0, top: '100%', width: 'min(420px, 85vw)', maxHeight: '75vh', overflow: 'auto', zIndex: 30, background: 'white', color: '#172033', border: '1px solid #bbb' }}>
      <KvBox title="내 알림" note="앱 내 알림이 원본입니다.">
        <button className="kb" disabled={list.isFetching || count.isFetching} onClick={() => { void count.refetch(); void list.refetch(); }}>알림 새로고침</button>
        {count.isError && <ErrorState error={count.error} />}
        {list.isPending && <Loading label="알림 조회 중" />}
        {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
        {failure != null && <ErrorState error={failure} />}
        {failedItem && <button className="kb" onClick={() => { setOpen(false); navigate(failedItem.link_path); }}>읽음 처리 없이 열기</button>}
        {list.data && !list.isError && <>
          {!list.data.items.length && <p>받은 알림이 없습니다.</p>}
          <ul>{list.data.items.map(item => <li key={item.id}>
            <button type="button" disabled={busy || !notificationPath(item.link_path)} onClick={() => choose(item)}>{item.title}</button>{' '}
            <small>{item.read_at ? '읽음' : '안 읽음'} / {formatDateTime(item.created_at)}</small>
            {!notificationPath(item.link_path) && <p>이 알림의 이동 경로를 확인할 수 없습니다.</p>}
          </li>)}</ul>
          <button className="kb" disabled={list.isFetching || offsets.length === 1} onClick={() => setOffsets(offsets.slice(0, -1))}>알림 이전</button>{' '}
          <button className="kb" disabled={list.isFetching || list.data.next_offset === null} onClick={() => list.data?.next_offset !== null && list.data?.next_offset !== undefined && setOffsets([...offsets, list.data.next_offset])}>알림 다음</button>
        </>}
      </KvBox>
    </div>}
  </div>;
}
