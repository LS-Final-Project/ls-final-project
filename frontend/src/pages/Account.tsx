import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { me, logoutSession } from '../api/platform';
import { sessionCsrfToken } from '../api/client';
import { ErrorState, Loading } from '../components/states';
import PushToggle from '../components/PushToggle';
import { clearPush } from '../notifications/push';
import MailboxConnections from '../components/MailboxConnections';
import LoginScreen from '../components/LoginScreen';
import { KvBox, KvField, KvTitle } from '../ls-shell/kv';

export default function Account() {
  return <AccountPanel operational={!import.meta.env.DEV} />;
}

export function AccountPanel({ operational }: { operational: boolean }) {
  const cache = useQueryClient();
  const user = useQuery({ queryKey: ['me'], queryFn: me, retry: false });
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [signedOut, setSignedOut] = useState(false);
  const identity = user.isSuccess ? user.data?.data : null;
  async function signOut() {
    if (busy || !operational || !identity || !sessionCsrfToken()) return;
    setBusy(true); setError(null);
    try {
      try { await clearPush(identity.id); } catch { setError(new Error('OS 알림 구독 정리를 확인하지 못했습니다. 로그아웃 후 브라우저 사이트 알림 설정을 확인하세요.')); }
      const current = await me();
      if (current?.data.id !== identity.id) throw new Error('계정이 변경되어 로그아웃을 중단했습니다. 현재 계정을 다시 확인하세요.');
      await logoutSession();
      // Clear all prior user's business data only after confirmed server logout.
      await cache.cancelQueries();
      cache.removeQueries({ predicate: query => query.queryKey[0] !== 'me' });
      cache.setQueryData(['me'], null);
      setSignedOut(true); setConfirming(false);
    } catch (cause) {
      setError(cause);
      await user.refetch();
    } finally { setBusy(false); }
  }
  // 세션이 없으면 계정 화면 대신 로그인 화면(보드 CS_K_Login)을 보인다.
  if (user.isSuccess && !identity) return <LoginScreen operational={operational} signedOut={signedOut} notice={error != null ? <ErrorState error={error} /> : undefined} />;
  return <main>
    <KvTitle path="계정" title="내 정보" code="CM0202">
      <button className="kb" disabled={busy || user.isFetching} onClick={() => void user.refetch()}>{operational ? '세션 다시 확인' : '다시 확인'}</button>
    </KvTitle>
    {!operational && <div role="note" className="kv-sub" style={{ margin: '8px 14px 0', padding: '6px 10px', border: '1px solid var(--erp-text)', background: '#fff', color: 'var(--erp-text)' }}>개발 모드입니다. 시연 계정은 Microsoft 로그인이나 사서함 연결을 확인한 계정이 아닙니다. 이 화면에서 운영 로그인·메일 연결을 실행할 수 없습니다.</div>}
    {user.isPending && <Loading label="세션 확인 중" />}
    {user.isError && <ErrorState error={user.error} onRetry={() => user.refetch()} />}
    {error != null && <ErrorState error={error} />}
    {signedOut && <p role="status" className="kv-sub" style={{ margin: '8px 14px 0' }}>로그아웃했습니다.</p>}
    <div className="kv-split" style={{ gridTemplateColumns: 'minmax(0,1fr)' }}>
    {identity && <KvBox title="현재 계정" note="이름, 부서, 역할은 Microsoft 계정과 사용자 등록에서 옵니다">
      <KvField label="이름">{identity.display_name}</KvField>
      <KvField label="이메일">{identity.email}</KvField>
      <KvField label="역할">{identity.roles.join(', ')}</KvField>
      <KvField label="부서">{identity.dept ?? '해당 없음'}</KvField>
      {operational && <div className="kv-actions" style={{ justifyContent: 'flex-start' }}>
        {!sessionCsrfToken() && <p role="alert" className="kv-sub" style={{ margin: 0 }}>세션 확인 정보가 없어 로그아웃할 수 없습니다. 다시 확인하세요.</p>}
        {!confirming
          ? <button className="kb" disabled={busy || !sessionCsrfToken()} onClick={() => setConfirming(true)}>로그아웃</button>
          : <><span className="kv-sub" style={{ margin: 0 }}>현재 계정에서 로그아웃할까요?</span><button className="kb primary" disabled={busy || !sessionCsrfToken()} onClick={() => void signOut()}>{busy ? '로그아웃 중' : '로그아웃 확인'}</button><button className="kb" disabled={busy} onClick={() => setConfirming(false)}>취소</button></>}
      </div>}
    </KvBox>}
    {identity && <KvBox title="알림"><div className="ls-legacy" style={{ minHeight: 0, padding: 0 }}><PushToggle key={identity.id} actorId={identity.id} /></div></KvBox>}
    <KvBox title="메일 연결" note="서비스 로그인과 메일 연결은 별도입니다"><p className="kv-sub" style={{ margin: '0 0 6px' }}>서비스 로그인과 메일 연결은 별도입니다.</p>{operational && identity && <MailboxConnections />}</KvBox>
    </div>
  </main>;
}
