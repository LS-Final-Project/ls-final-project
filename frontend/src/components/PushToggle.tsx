import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { NotificationSessionChanged, pushPublicKey } from '../api/notifications';
import { clearPush, enablePush, existingPushWorker, publicKeyBytes, pushOwner, pushSupported } from '../notifications/push';
import { KvBox } from '../ls-shell/kv';
import { ErrorState } from './states';

export default function PushToggle({ actorId }: { actorId: string }) { return <Toggle key={actorId} actorId={actorId} />; }
function Toggle({ actorId }: { actorId: string }) {
  const cache = useQueryClient();
  const alive = useRef(true);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => { alive.current = true; const controller = new AbortController(); lifetime.current = controller; return () => { alive.current = false; controller.abort(); }; }, []);
  const supported = pushSupported();
  const key = useQuery({ queryKey: ['notification-push-key', actorId], queryFn: ({ signal }) => pushPublicKey(signal), enabled: supported, retry: false });
  const [permission, setPermission] = useState(supported ? Notification.permission : 'default');
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  useEffect(() => {
    let current = true;
    if (supported) void existingPushWorker().then(async registration => { const subscription = await registration?.pushManager.getSubscription(); if (current) setEnabled(!!subscription && pushOwner() === actorId); }).catch(failure => { if (current) setError(failure); });
    return () => { current = false; };
  }, [actorId, supported]);
  let available = false;
  try { available = key.isSuccess && !key.isFetching && !!key.data?.public_key && !!publicKeyBytes(key.data.public_key); } catch { /* invalid key stays disabled */ }
  async function turnOn() {
    if (!supported || !available || busy || permission === 'denied') return;
    setBusy(true); setError(undefined);
    try {
      // This is called directly from the user's click, before any awaited server request.
      const allowed = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
      if (!alive.current) return;
      setPermission(allowed);
      if (allowed !== 'granted') return;
      await enablePush(actorId, key.data!.public_key, lifetime.current!.signal); if (alive.current) setEnabled(true);
    } catch (failure) {
      if (!alive.current) return;
      setError(failure);
      if (failure instanceof NotificationSessionChanged) { cache.setQueryData(['me'], null); void cache.invalidateQueries({ queryKey: ['me'] }); }
    } finally { if (alive.current) setBusy(false); }
  }
  async function turnOff() {
    setBusy(true); setError(undefined);
    try { await clearPush(actorId); if (alive.current) setEnabled(false); } catch (failure) { if (alive.current) { setError(failure); if (pushOwner() !== actorId) setEnabled(false); if (failure instanceof NotificationSessionChanged) { cache.setQueryData(['me'], null); void cache.invalidateQueries({ queryKey: ['me'] }); } } } finally { if (alive.current) setBusy(false); }
  }
  const off = key.error instanceof ApiError && key.error.status === 404;
  return <KvBox title="OS 알림">
    <p>담당 업무 알림을 이 브라우저의 OS 알림으로 받습니다. 앱 내 알림이 원본이며 브라우저 종료 시 수신되지 않을 수 있습니다.</p>
    {!supported ? <p>이 환경에서는 OS 알림을 사용할 수 없습니다. 앱 내 알림을 이용하세요.</p> : <>
      {permission === 'denied' && <p>알림 권한이 거부되었습니다. 브라우저의 사이트 설정에서 변경하세요.</p>}
      {off ? <p>서버의 OS 알림 기능이 꺼져 있습니다.</p> : !available && <p>공개키를 확인할 수 없어 OS 알림을 켤 수 없습니다.</p>}
      {enabled && available && !off && <p role="status">이 계정의 OS 알림 켜짐</p>}
      <button disabled={busy || !available || permission === 'denied' || enabled} onClick={turnOn}>OS 알림 켜기</button>{' '}
      <button disabled={busy || !enabled} onClick={turnOff}>OS 알림 끄기</button>
      {key.isError && !off && <ErrorState error={key.error} onRetry={() => key.refetch()} />}
    </>}
    {error != null && <ErrorState error={error} />}
  </KvBox>;
}
